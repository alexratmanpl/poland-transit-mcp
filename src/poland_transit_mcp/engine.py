"""The optional Rust search engine (package poland-transit-mcp-engine), with the Python one as fallback.

When `poland_transit_engine` is importable, each service day's RAPTOR tables are handed to it once,
as flat arrays, and every search runs there: the same algorithm with the same answers, about 20x
faster per search (docs/CHECKS.md, "Rust engine"). Otherwise, or with
POLAND_TRANSIT_ENGINE=python, the search runs in raptor.py as before.
"""
from __future__ import annotations

import os
import sys
from array import array

try:
    if os.environ.get("POLAND_TRANSIT_ENGINE", "").lower() == "python":
        raise ImportError("Python engine requested")
    import poland_transit_engine as _rust
except ImportError:
    _rust = None

NAME = f"rust {_rust.__version__}" if _rust else "python"


def available() -> bool:
    return _rust is not None


def _bytes(typecode: str, values) -> bytes:
    a = array(typecode, values)
    if sys.byteorder != "little":
        a.byteswap()
    return a.tobytes()


def transfers(lists: list[list[tuple[int, int]]], n_stops: int):
    """A city's foot transfers for the engine (covers at least n_stops stops)."""
    off, to, secs = [0], [], []
    for s in range(max(n_stops, len(lists))):
        for s2, w in lists[s] if s < len(lists) else ():
            to.append(s2)
            secs.append(w)
        off.append(len(to))
    return _rust.Transfers(_bytes("q", off), _bytes("i", to), _bytes("i", secs))


def tables(n_stops: int, patterns, stop_routes, arr: array, dep: array, time_off: list[int]):
    """One day's tables in one direction, for the engine. `arr`/`dep` hold every pattern's times,
    position-major, from `time_off[p]` (built that way by Timetable._build_raptor)."""
    pat_off, stops, pick, drop, ntrips = [0], array("i"), bytearray(), bytearray(), []
    for rp in patterns:
        stops.extend(rp.stops)
        pick += rp.pick
        drop += rp.drop
        pat_off.append(len(stops))
        ntrips.append(rp.n_trips)
    sr_off, sr_pat, sr_pos = [0], array("i"), array("i")
    for lst in stop_routes:
        for p, pos in lst:
            sr_pat.append(p)
            sr_pos.append(pos)
        sr_off.append(len(sr_pat))
    if sys.byteorder != "little":
        arr, dep = array("i", arr), array("i", dep)
        for a in (stops, arr, dep, sr_pat, sr_pos):
            a.byteswap()
    return _rust.Tables(n_stops, _bytes("q", pat_off), stops.tobytes(), bytes(pick), bytes(drop), _bytes("i", ntrips),
                        _bytes("q", time_off), arr.tobytes(), dep.tobytes(), _bytes("q", sr_off), sr_pat.tobytes(), sr_pos.tobytes())
