"""Measures trips between cities against the real server over stdio: latency per city pair, memory
with the cities and the national layer loaded, and how often a connection is found.

  python scripts/measure_intercity.py [--cities gdansk,krakow,warszawa] [--trips 20] [--date YYYY-MM-DD]
                                      [--memory-budget 700] [--data-dir data/cities]

Points are random stops of each city, moved by up to ~200 m; times are random between 05:30 and 21:00.
Without --date, the first day on which every loaded file's timetable is valid (City.valid_from).
One query in five uses arrive_by. The first query of each pair is reported apart: it finds that city's
stations (once per process) and may load the cities.
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from poland_transit_mcp.city import City

ap = argparse.ArgumentParser()
ap.add_argument("--cities", default="gdansk,krakow,warszawa")
ap.add_argument("--trips", type=int, default=20, help="per ordered city pair")
ap.add_argument("--date", help="YYYY-MM-DD (default: the first day every loaded file's timetable is valid)")
ap.add_argument("--memory-budget", type=float, help="passed to the server (MB)")
ap.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / "data" / "cities"))
a = ap.parse_args()
CITIES = a.cities.split(",")
DATA = Path(a.data_dir)
rnd = random.Random(42)
_starts = [s for c in [*CITIES, "poland"] if (DATA / f"{c}.sqlite").exists() and (s := City(DATA / f"{c}.sqlite").valid_from)]
DATE = a.date or (max(_starts) if _starts else datetime.date.today().isoformat())


def params() -> StdioServerParameters:
    extra = ["--memory-budget", str(a.memory_budget)] if a.memory_budget else []
    return StdioServerParameters(command=sys.executable, args=["-m", "poland_transit_mcp", "--data-dir", str(DATA),
                                 "--cities", ",".join([*CITIES, "poland"]), "--no-download", *extra],
                                 env=dict(os.environ, POLAND_TRANSIT_NO_WARMUP="1"))


def rss_of(pid: int) -> dict:
    st = Path(f"/proc/{pid}/status").read_text()
    kb = lambda k: int(re.search(rf"{k}:\s+(\d+)", st)[1])  # noqa: E731
    return {"rss_mb": round(kb("VmRSS") / 1024), "peak_mb": round(kb("VmHWM") / 1024)}


def server_pid() -> int:
    pids = [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()
            and b"poland_transit_mcp" in (p / "cmdline").read_bytes()]
    return max(pids)


def pct(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def anchors(city: str) -> list[tuple[float, float]]:
    db = sqlite3.connect(f"file:{DATA / f'{city}.sqlite'}?mode=ro", uri=True)
    rows = db.execute("SELECT lat, lon FROM stops WHERE node IS NOT NULL").fetchall()
    db.close()
    return rows


async def main() -> dict:
    out: dict = {"date_queried": DATE, "cities": CITIES, "memory_budget": a.memory_budget,
                 "files_mb": {c: round((DATA / f"{c}.sqlite").stat().st_size / 1e6, 1) for c in [*CITIES, "poland"]}}
    pts = {c: anchors(c) for c in CITIES}

    def point(c: str) -> list[float]:
        la, lo = rnd.choice(pts[c])
        return [round(la + (rnd.random() - .5) * .004, 5), round(lo + (rnd.random() - .5) * .006, 5)]

    def when() -> str:
        m = 330 + rnd.randrange(21 * 60 - 330)
        return f"{DATE} {m // 60:02d}:{m % 60:02d}"

    lat: dict[str, list[float]] = {}
    first: dict[str, int] = {}
    outcomes = {"with_train_or_coach": 0, "none": 0}
    errors: list[str] = []
    async with Client(params(), read_timeout_seconds=300) as client:
        pid = server_pid()
        out["memory_after_start"] = rss_of(pid)
        for c1, c2 in itertools.permutations(CITIES, 2):
            key = f"{c1}->{c2}"
            for i in range(a.trips + 1):
                args = {"city": c1, "from": point(c1), "to_city": c2, "to": point(c2),
                        ("arrive_by" if i % 5 == 4 else "depart_at"): when()}
                t = time.perf_counter()
                r = await client.call_tool("plan_trip", args)
                ms = (time.perf_counter() - t) * 1000
                if i == 0:
                    first[key] = round(ms)
                else:
                    lat.setdefault(key, []).append(ms)
                    lat.setdefault("all", []).append(ms)
                if r.is_error:
                    errors.append(f"{json.dumps(args, ensure_ascii=False)}: {r.content[0].text[:200]}")
                    continue
                its = json.loads(r.content[0].text).get("itineraries", [])
                found = any(leg["mode"] in ("rail", "bus") and leg.get("from", {}).get("city") == "poland"
                            for it in its for leg in it["legs"])
                outcomes["with_train_or_coach" if found else "none"] += 1
        out["memory_after_queries"] = rss_of(pid)
    out["first_query_ms"] = first
    out["latency_ms"] = {k: {"n": len(v), "p50": round(pct(v, 50)), "p95": round(pct(v, 95)), "max": round(max(v))}
                         for k, v in lat.items()}
    out["outcomes"] = outcomes
    out["error_count"] = len(errors)
    out["errors"] = errors[:5]
    return out


print(json.dumps(anyio.run(main), indent=1, ensure_ascii=False))
