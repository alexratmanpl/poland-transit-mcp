"""Starts the server over stdio, as an MCP client does, and checks that it answers real questions.

  uv run python scripts/smoke.py                          # every city file found in data/cities
  uv run python scripts/smoke.py --cities gdansk,poland   # chosen cities (add `poland` for a trip between cities)
  uv run python scripts/smoke.py --memory-budget 700 --data-dir /path/to/cities

For each city it lists the tools once, then asks find_places for two stops and plans a trip between them.
A city without timetables (a Zakopane file built from this repository) gets a walk_route between two places
instead. With `poland` and two cities it also plans one trip between the cities. It prints the search engine in use
(`rust` needs the engine built: `uv sync --extra fast`), and exits 0 only if every call answered.
Nothing is written. Cold calls load a city into memory (0.3-2 s, up to 4.5 s on slower machines); repeats take tens of milliseconds.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def call(s: ClientSession, tool: str, args: dict):
    t = time.perf_counter()
    r = await s.call_tool(tool, args)
    text = r.content[0].text if r.content else ""
    ms = (time.perf_counter() - t) * 1000
    if r.is_error:
        return None, ms, text
    return json.loads(text), ms, ""


def valid_from(city: dict) -> str | None:
    """First date the city's timetable is valid for (the national layer lists validity per feed)."""
    t = city.get("timetable") or {}
    starts = [t["valid"]] if t.get("valid") else [f["valid"] for f in t.get("feeds", []) if f.get("valid")]
    return max(v.split("..")[0] for v in starts) if starts else None


async def run(a) -> int:
    data = Path(a.data_dir).resolve()
    cities = a.cities or ",".join(sorted(p.stem for p in data.glob("*.sqlite")))
    if not cities:
        print(f"no city files in {data}: see AGENTS.md, step 4 (build or download data)")
        return 2
    have = {c for c in cities.split(",") if c and (data / f"{c}.sqlite").exists()}
    absent = [c for c in cities.split(",") if c and c not in have]
    if not have:
        print(f"FAIL: none of {cities} has a file in {data}: see AGENTS.md, step 4 (build data)")
        return 1
    exe = ROOT / ".venv" / "bin" / "poland-transit-mcp"
    cmd = str(exe) if exe.exists() else sys.executable
    args = ["--cities", ",".join(sorted(have)), "--data-dir", str(data), "--no-download", "--memory-budget", str(a.memory_budget)]
    if cmd == sys.executable:
        args = ["-m", "poland_transit_mcp", *args]
    failures = len(absent)
    for cid in absent:
        print(f"FAIL {cid}: requested but there is no file for it in {data} (AGENTS.md, step 4)")
    async with stdio_client(StdioServerParameters(command=cmd, args=args)) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print(f"tools ({len(tools)}): {', '.join(tools)}")
            lc, _, err = await call(s, "list_cities", {})
            if lc is None:
                print("FAIL list_cities:", err)
                return 1
            from poland_transit_mcp import engine  # the same install the server runs from
            print(f"search engine: {engine.NAME}")
            info = {c["city"]: c for c in lc["cities"]}
            for cid in sorted(have - set(info)):
                print(f"FAIL {cid}: the file exists but the server did not load it (see the server's stderr)")
                failures += 1
            for cid, c in info.items():
                if c["features"].get("walking") is False or cid == "poland":
                    continue  # the national layer has no map; it is checked below
                centre = [(c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2]
                if c["features"].get("timetables") is False:  # map and walking only
                    places, ms, err = await call(s, "find_places", {"city": cid, "near": centre, "radius_m": 5000, "limit": 5,
                                                                    "kinds": ["museum", "hotel", "attraction", "landmark", "park"]})
                    found = (places or {}).get("places", [])
                    if len(found) < 2:
                        print(f"FAIL {cid}: find_places -> {err or 'fewer than two places'}")
                        failures += 1
                        continue
                    for other in found[1:]:  # a place can lie off the walking graph; try the next one
                        walk, ms2, err = await call(s, "walk_route", {"city": cid, "from": found[0]["at"], "to": other["at"]})
                        if walk is not None and walk.get("path"):
                            break
                    if walk is None or not walk.get("path"):
                        print(f"FAIL {cid}: walk_route -> {err or (walk or {}).get('note') or 'no path'}")
                        failures += 1
                        continue
                    print(f"ok   {cid}: no timetables; walk_route {walk['distance_m']} m, {walk['minutes']} min, "
                          f"find_places {ms:.0f} ms, walk_route {ms2:.0f} ms (cold)")
                    continue
                valid = valid_from(c) or "today"
                stops, ms, err = await call(s, "find_places", {"city": cid, "near": centre,
                                                                 "kinds": ["stop"], "radius_m": 3000, "limit": 2})
                if stops is None or len(stops.get("places", stops.get("results", []))) < 2:
                    print(f"FAIL {cid}: find_places stop search -> {err or 'fewer than two stops'}")
                    failures += 1
                    continue
                pts = [p["at"] for p in stops.get("places", stops.get("results", []))[:2]]
                trip, ms2, err = await call(s, "plan_trip", {"city": cid, "from": pts[0], "to": pts[1], "depart_at": f"{valid} 09:00" if valid != "today" else "09:00"})
                if trip is None or not trip.get("itineraries"):
                    print(f"FAIL {cid}: plan_trip -> {err or 'no itinerary'}")
                    failures += 1
                    continue
                it = trip["itineraries"][0]
                print(f"ok   {cid}: plan_trip {it['depart']}->{it['arrive']} ({it['duration_min']} min, {it['transfers']} transfers), find_places {ms:.0f} ms, plan_trip {ms2:.0f} ms (cold)")
            if "poland" in info and len([c for c in info if c != "poland"]) < 2:
                print("skip intercity: needs `poland` plus two city files (AGENTS.md, step 4)")
            if "poland" in info and len([c for c in info if c != "poland"]) >= 2:
                a_id, b_id = [c for c in info if c != "poland"][:2]
                pa, pb = info[a_id]["bbox"], info[b_id]["bbox"]
                valid = valid_from(info["poland"]) or "today"
                trip, ms, err = await call(s, "plan_trip", {"city": a_id, "from": [(pa[0] + pa[2]) / 2, (pa[1] + pa[3]) / 2],
                                                            "to": [(pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2], "to_city": b_id, "depart_at": f"{valid} 08:00" if valid != "today" else "08:00"})
                if trip is None or not trip.get("itineraries"):
                    print(f"FAIL intercity {a_id}->{b_id}: {err or 'no itinerary'}")
                    failures += 1
                else:
                    it = trip["itineraries"][0]
                    print(f"ok   intercity {a_id}->{b_id}: {it['depart']}->{it['arrive']} ({it['duration_min']} min), {ms:.0f} ms (cold)")
    print("PASS" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--cities", help="comma-separated city ids (default: every .sqlite in the data directory)")
ap.add_argument("--data-dir", default=str(ROOT / "data" / "cities"))
ap.add_argument("--memory-budget", type=float, default=700, help="MB; 600 keeps three big cities loaded, 700 with `poland` (default 700)")
sys.exit(anyio.run(run, ap.parse_args()))
