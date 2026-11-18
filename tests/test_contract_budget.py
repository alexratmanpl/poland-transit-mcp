"""The budget: the largest drawing the published schema allows (the whole test city, a trip across it, the largest
size, as PNG) stays under fixed ceilings of time and memory. Run in its own process, so the memory
high-water mark is this call's alone."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SECONDS = 6.0  # a caller's own itinerary (30 legs of 5,000 points) took 1.3 s before render_map took only `trip`
PEAK_MB = 300  # growth of the process's memory high-water mark during the call; measured 90-92 MB then

SCRIPT = r"""
import json, math, resource, sys, time
import anyio
from mcp import Client
from poland_transit_mcp.city import City
from poland_transit_mcp.server import create_server

city_dir = sys.argv[1]
server = create_server({"testowo": City.open(city_dir, "testowo")})

box = list(City.open(city_dir, "testowo").bbox)  # the whole test city

def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10

async def go():
    async with Client(server) as c:
        await c.call_tool("list_cities", {})
        plan = await c.call_tool("plan_trip", {"city": "testowo", "from": [50.0018, 19.0014], "to": [50.0162, 19.0168],
                                               "depart_at": "2026-11-10 07:58"})
        trip = plan.structured_content["itineraries"][0]["trip"]
        args = {"city": "testowo", "bbox": box, "trip": trip, "size": 2000, "output": "png"}
        before = rss_mb()
        t = time.perf_counter()
        r = await c.call_tool("render_map", args)
        seconds = time.perf_counter() - t
        text = next(b.text for b in r.content if b.type == "text")
        print(json.dumps({"seconds": seconds, "peak_mb": rss_mb() - before, "error": r.is_error, "text": text[:300]}))

anyio.run(go)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the resource module is POSIX only")
def test_the_largest_drawing_the_schema_allows_stays_within_budget(city_dir: Path):
    env = {**os.environ, "POLAND_TRANSIT_NO_WARMUP": "1"}
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(city_dir)], capture_output=True, text=True, env=env,
                         timeout=SECONDS * 4, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr[-2000:]
    m = json.loads(out.stdout.strip().splitlines()[-1])
    assert not m["error"], m["text"]
    assert m["seconds"] < SECONDS, m
    assert m["peak_mb"] < PEAK_MB, m
