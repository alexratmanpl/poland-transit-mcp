"""The budget: the largest drawing the published schema allows (30 legs of 5,000 points, every label at its longest,
the largest size, as PNG) stays under fixed ceilings of time and memory. Run in its own process, so the memory
high-water mark is this call's alone."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SECONDS = 6.0  # measured 2026-11-18 on an Apple Silicon Mac: 1.3 s (a 1,519 x 2,000 PNG)
PEAK_MB = 300  # growth of the process's memory high-water mark during the call; measured 90-92 MB

SCRIPT = r"""
import json, math, resource, sys, time
import anyio
from mcp import Client
from poland_transit_mcp.city import City
from poland_transit_mcp.server import create_server

city_dir = sys.argv[1]
server = create_server({"testowo": City.open(city_dir, "testowo")})

def leg(i):
    # a path that stays inside the test city's frame: a spiral of 5,000 points
    path = [[50.0 + 0.009 * (1 + math.sin(i + k / 37)) , 19.0 + 0.01 * (1 + math.cos(i + k / 41))] for k in range(5000)]
    return {"mode": "bus" + "x" * 17, "line": "L" * 60, "color": "#" + "a" * 19,
            "from": {"name": "N" * 200, "at": path[0]}, "to": {"name": "M" * 200, "at": path[-1]}, "path": path}

args = {"city": "testowo", "itinerary": {"legs": [leg(i) for i in range(30)]}, "size": 2000, "output": "png"}

def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10

async def go():
    async with Client(server) as c:
        await c.call_tool("list_cities", {})
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
