"""Checks that the Rust engine and the Python search give the same trip plans, and times both.

  python scripts/compare_engines.py --cities warszawa,katowice,krakow,gdansk --plans 100 [--date YYYY-MM-DD]

Without --date, each city is queried on the first day its timetable file is valid (City.valid_from).

Needs the engine installed (pip install poland-transit-mcp-engine, or `maturin develop` in engine/).
Each city is opened twice: once with its tables built for the Python search, once for the engine.
The same random plan_trip calls (scripts/measure.py's sampling) run on both, and every answer is
compared in full. Times are in-process plan_trip times, without the MCP transport.
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from poland_transit_mcp import engine  # noqa: E402
from poland_transit_mcp.city import City  # noqa: E402
from poland_transit_mcp.plan import plan_trip  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--cities", default="warszawa,katowice,krakow,gdansk")
ap.add_argument("--plans", type=int, default=100)
ap.add_argument("--date", help="YYYY-MM-DD (default: the first day each city's timetable is valid)")
ap.add_argument("--data-dir", default=str(ROOT / "data" / "cities"))
a = ap.parse_args()
if not engine.available():
    sys.exit("the Rust engine is not installed (pip install poland-transit-mcp-engine)")
rust = engine._rust


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def run(city: City, queries) -> tuple[list[str], list[float]]:
    out, ms = [], []
    for frm, to, kw in queries:
        t = time.perf_counter()
        try:
            r = json.dumps(plan_trip(city, frm, to, **kw), sort_keys=True, ensure_ascii=False)
        except ValueError as e:
            r = f"error: {e}"
        ms.append((time.perf_counter() - t) * 1000)
        out.append(r)
    return out, ms


print(f"engine {engine.NAME}; {a.plans} plans per city")
for cid in a.cities.split(","):
    rnd = random.Random(7)
    probe = City.open(Path(a.data_dir), cid)
    date = a.date or probe.valid_from or datetime.date.today().isoformat()
    stops = [s for s in probe.tt.stops if s and s.node is not None]
    queries = []
    for i in range(a.plans):
        p1, p2 = rnd.choice(stops), rnd.choice(stops)
        m = 330 + rnd.randrange(23 * 60 + 50 - 330)
        when = f"{date} {m // 60:02d}:{m % 60:02d}"
        queries.append(([round(p1.lat + (rnd.random() - .5) * .004, 5), round(p1.lon + (rnd.random() - .5) * .006, 5)],
                        [round(p2.lat + (rnd.random() - .5) * .004, 5), round(p2.lon + (rnd.random() - .5) * .006, 5)],
                        {"arrive_by": when} if i % 5 == 4 else {"depart_at": when}))
    engine._rust = None  # tables for the Python search
    py_city = City.open(Path(a.data_dir), cid)
    py_out, py_ms = run(py_city, queries)
    engine._rust = rust
    rs_city = City.open(Path(a.data_dir), cid)
    rs_out, rs_ms = run(rs_city, queries)
    same = sum(x == y for x, y in zip(py_out, rs_out))
    print(f"{cid} on {date}: identical answers {same}/{len(queries)}; plan_trip p50/p95 "
          f"Python {pct(py_ms, 50):.0f}/{pct(py_ms, 95):.0f} ms, Rust {pct(rs_ms, 50):.0f}/{pct(rs_ms, 95):.0f} ms "
          f"(total {sum(py_ms) / sum(rs_ms):.1f}x faster)")
    for i, (x, y) in enumerate(zip(py_out, rs_out)):
        if x != y:
            print(f"  differs: plan {i} {queries[i]}")
            break
