"""Runs only when a built data/cities/gdansk.sqlite is present (data is never committed)."""
from pathlib import Path

import pytest

from poland_transit_mcp.city import City
from poland_transit_mcp.geo import decode_parts, fast_dist, point_in_rings
from poland_transit_mcp.transit import walk_path
from poland_transit_mcp.walk import FLAG_BRIDGE, FLAG_TUNNEL

DATA = Path(__file__).resolve().parents[1] / "data" / "cities"
pytestmark = pytest.mark.skipif(not (DATA / "gdansk.sqlite").exists(), reason="no data/cities/gdansk.sqlite")


def longest_wet_stretch(city, coords, flags) -> float:
    """Longest stretch (m) of the non-bridge, non-tunnel part of a route lying inside mapped water."""
    lats, lons = [c[0] for c in coords], [c[1] for c in coords]
    water = [decode_parts(r[0]) for r in city.db.execute(
        "SELECT f.coords FROM features f JOIN features_rtree r ON r.id = f.id WHERE f.layer = 'water' AND f.poly = 1 "
        "AND r.max_lat >= ? AND r.min_lat <= ? AND r.max_lon >= ? AND r.min_lon <= ?", (min(lats), max(lats), min(lons), max(lons)))]
    worst = run = 0.0
    for i in range(1, len(coords) - 2):  # coords = [start, nodes..., end]; flags[i-1] is the edge node i → i+1
        if flags[i - 1] & (FLAG_BRIDGE | FLAG_TUNNEL):
            run = 0.0
            continue
        (a0, a1), (b0, b1) = coords[i], coords[i + 1]
        ln = fast_dist(a0, a1, b0, b1)
        steps = max(1, int(ln / 5) + 1)
        for s in range(steps):
            t = (s + 0.5) / steps
            if any(point_in_rings(a0 + (b0 - a0) * t, a1 + (b1 - a1) * t, w) for w in water):
                run += ln / steps
                worst = max(worst, run)
            else:
                run = 0.0
    return worst


CASES = [
    ("Nowy Port lighthouse → Westerplatte (no ferry)", (54.4074, 18.6614), (54.4063, 18.6770), 4000),
    ("Długie Pobrzeże → Ołowianka (Motława)", (54.3497, 18.6566), (54.3509, 18.6604), 250),
    ("Zielona Brama → Wyspa Spichrzów (Motława)", (54.3485, 18.6560), (54.3478, 18.6590), 200),
    ("Długi Targ → Stogi beach (Motława and Martwa Wisła)", (54.3487, 18.6530), (54.3760, 18.7130), 4000),
]


@pytest.mark.parametrize("name,a,b,min_m", CASES, ids=[c[0] for c in CASES])
def test_walking_never_crosses_water_off_a_bridge(name, a, b, min_m):
    city = City.open(DATA, "gdansk")
    r = walk_path(city, a, b, 30000)
    wet = longest_wet_stretch(city, r.coords, r.flags)
    print(f"{name}: {round(r.meters)} m, {sum(1 for f in r.flags if f & FLAG_BRIDGE)} bridge edges, {wet:.0f} m in water")
    assert r.meters >= min_m
    assert wet <= 15
    assert any(f & FLAG_BRIDGE for f in r.flags)


def test_checker_detects_a_straight_crossing():
    city = City.open(DATA, "gdansk")
    straight = [(54.3497, 18.6566), (54.3497, 18.6566), (54.3509, 18.6604), (54.3509, 18.6604)]
    assert longest_wet_stretch(city, straight, [0]) > 100
    assert longest_wet_stretch(city, straight, [FLAG_BRIDGE]) == 0
