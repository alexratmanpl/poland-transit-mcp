"""Measures the weight budget against the real server over stdio: file sizes, cold start, memory with
all cities loaded, and tool latencies over random queries.

  python scripts/measure.py [--cities gdansk,wroclaw,krakow] [--trips 200] [--date YYYY-MM-DD] [--data-dir data/cities]

Without --date, each city is queried on the first day its timetable file is valid (City.valid_from).
The output says, per city, the date queried, whether the file has timetables, and how many plans found transit.
Linux only: memory is read from /proc.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import random
import re
import sys
import time
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from poland_transit_mcp.city import City
from poland_transit_mcp.shared_db import connect_read_only

ap = argparse.ArgumentParser()
ap.add_argument("--cities", default="gdansk,wroclaw,krakow")
ap.add_argument("--trips", type=int, default=200)
ap.add_argument("--date", help="YYYY-MM-DD (default: the first day each city's timetable is valid)")
ap.add_argument("--memory-budget", type=float, help="passed to the server (MB)")
ap.add_argument("--interleave", action="store_true",
                help="only plan_trip, one city after another on every query (as an assistant switching between "
                     "cities would), to see reloads when the memory budget is too small")
ap.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / "data" / "cities"))
a = ap.parse_args()
CITIES = a.cities.split(",")
DATA = Path(a.data_dir)
rnd = random.Random(42)
_files = {c: City(DATA / f"{c}.sqlite") for c in CITIES}
DATES = {c: a.date or f.valid_from or datetime.date.today().isoformat() for c, f in _files.items()}
TIMETABLES = {c: f.has_transit for c, f in _files.items()}


def params() -> StdioServerParameters:
    extra = ["--memory-budget", str(a.memory_budget)] if a.memory_budget else []
    # the whole environment, so OTEL_* and POLAND_TRANSIT_* settings reach the server (the MCP client passes only a few by default)
    return StdioServerParameters(command=sys.executable, args=["-m", "poland_transit_mcp", "--data-dir", str(DATA), "--cities", ",".join(CITIES), "--no-download", *extra],
                                 env=dict(os.environ))


def rss_of(pid: int) -> dict:
    st = Path(f"/proc/{pid}/status").read_text()
    kb = lambda k: int(re.search(rf"{k}:\s+(\d+)", st)[1])  # noqa: E731
    return {"rss_mb": round(kb("VmRSS") / 1024), "peak_mb": round(kb("VmHWM") / 1024)}


def server_pid() -> int:
    """The newest python process running our module (the stdio child)."""
    pids = [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()
            and b"poland_transit_mcp" in (p / "cmdline").read_bytes()]
    return max(pids)


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


async def main() -> dict:
    out: dict = {"python": platform.python_version(), "date_queried": DATES, "timetables": TIMETABLES, "cpu": platform.processor() or platform.machine()}
    out["files_mb"] = {c: round((DATA / f"{c}.sqlite").stat().st_size / 1e6, 1) for c in CITIES}
    colds = []
    for _ in range(5):
        t = time.perf_counter()
        async with Client(params()) as c:
            await c.call_tool("list_cities", {})
            colds.append(round((time.perf_counter() - t) * 1000))
    out["cold_start_ms"] = {"runs": colds, "max": max(colds)}
    lat: dict[str, list[float]] = {}
    outcomes: dict[str, dict] = {}
    errors: list[str] = []
    first: dict[str, int] = {}
    async with Client(params(), read_timeout_seconds=120) as client:
        pid = server_pid()
        await anyio.sleep(2.5)  # background warm-up
        out["memory_after_start"] = rss_of(pid)

        async def timed(tool: str, args: dict, key: str | None = None) -> dict:
            t = time.perf_counter()
            r = await client.call_tool(tool, args)
            ms = (time.perf_counter() - t) * 1000
            lat.setdefault(tool, []).append(ms)
            if key:
                lat.setdefault(key, []).append(ms)
            if r.is_error:
                errors.append(f"{tool} {json.dumps(args, ensure_ascii=False)}: {r.content[0].text}")
                return {}
            return json.loads(r.content[0].text)

        if a.interleave:
            anchors_of = {}
            for c in CITIES:
                db = connect_read_only(DATA / f'{c}.sqlite')
                anchors_of[c] = db.execute("SELECT lat, lon FROM stops WHERE node IS NOT NULL").fetchall()
                db.close()

            def near(c: str) -> list[float]:
                la, lo = rnd.choice(anchors_of[c])
                return [round(la + (rnd.random() - .5) * .004, 5), round(lo + (rnd.random() - .5) * .006, 5)]

            for i in range(a.trips * len(CITIES)):
                c = CITIES[i % len(CITIES)]
                m = 330 + rnd.randrange(23 * 60 + 50 - 330)
                await timed("plan_trip", {"city": c, "from": near(c), "to": near(c),
                                          "depart_at": f"{DATES[c]} {m // 60:02d}:{m % 60:02d}"}, f"plan_trip:{c}")
            out["memory_after_queries"] = rss_of(pid)
            out["plan_trip_over_500ms"] = sum(1 for v in lat["plan_trip"] if v > 500)
        for c in ([] if a.interleave else CITIES):
            db = connect_read_only(DATA / f'{c}.sqlite')
            stops = db.execute("SELECT stop_id, lat, lon FROM stops WHERE node IS NOT NULL").fetchall()
            # a city without timetables: sample around mapped places instead (a random point in the
            # bounding box may be in a forest or on a mountain, which walking correctly refuses)
            anchors = stops or db.execute("SELECT id, lat, lon FROM places WHERE kind != 'street'").fetchall()
            bbox = json.loads(db.execute("SELECT value FROM meta WHERE key = 'bbox'").fetchone()[0])
            db.close()

            def point() -> list[float]:
                if anchors:
                    _, la, lo = rnd.choice(anchors)
                    return [round(la + (rnd.random() - .5) * .004, 5), round(lo + (rnd.random() - .5) * .006, 5)]
                return [round(bbox[0] + (.3 + .4 * rnd.random()) * (bbox[2] - bbox[0]), 5), round(bbox[1] + (.3 + .4 * rnd.random()) * (bbox[3] - bbox[1]), 5)]

            def when() -> str:
                m = 330 + rnd.randrange(23 * 60 + 50 - 330)
                return f"{DATES[c]} {m // 60:02d}:{m % 60:02d}"

            t = time.perf_counter()
            await client.call_tool("plan_trip", {"city": c, "from": point(), "to": point(), "depart_at": when()})
            first[c] = round((time.perf_counter() - t) * 1000)
            o = outcomes.setdefault(c, {"transit": 0, "walk_only": 0, "none": 0})
            for i in range(a.trips):
                r = await timed("plan_trip", {"city": c, "from": point(), "to": point(), ("arrive_by" if i % 5 == 4 else "depart_at"): when()}, f"plan_trip:{c}")
                its = r.get("itineraries", [])
                o["transit" if any(l["mode"] != "walk" for it in its for l in it["legs"]) else "walk_only" if its else "none"] += 1
            for _ in range(30):
                await timed("walk_route", {"city": c, "from": point(), "to": point()})
            if stops:
                for _ in range(30):
                    await timed("stops_near", {"city": c, "point": point(), "date": DATES[c]})
                    await timed("departures", {"city": c, "stop_id": rnd.choice(stops)[0], "at": when()})
        out["memory_after_queries"] = rss_of(pid)
    out["first_plan_trip_ms"] = first
    out["latency_ms"] = {k: {"n": len(v), "p50": round(pct(v, 50)), "p95": round(pct(v, 95)), "max": round(max(v))} for k, v in lat.items()}
    out["plan_trip_outcomes"] = outcomes
    out["error_count"] = len(errors)
    out["errors"] = errors[:5]
    return out


print(json.dumps(anyio.run(main), indent=1, ensure_ascii=False))
