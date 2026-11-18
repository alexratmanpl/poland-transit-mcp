"""map_extract on hand-made features: every ring of a polygon kept (holes and extra outer parts), layers left out
from the lowest priority up."""
from types import SimpleNamespace

from poland_transit_mcp import mapdata
from poland_transit_mcp.mapdata import Feature

BOX = (50.0, 19.0, 50.02, 19.03)


def square(lat, lon, d):
    return [(lat, lon), (lat, lon + d), (lat + d, lon + d), (lat + d, lon), (lat, lon)]


def extract(monkeypatch, feats, layers, max_points=1000):
    monkeypatch.setattr(mapdata, "map_features", lambda *a, **k: (feats, []))
    return mapdata.map_extract(SimpleNamespace(), BOX, layers, max_points)


def test_a_polygon_keeps_its_hole_and_its_other_outer_parts(monkeypatch):
    lake = square(50.002, 19.002, 0.012)
    island = square(50.006, 19.006, 0.004)
    pond = square(50.016, 19.02, 0.003)  # a second outer part of the same feature
    body = extract(monkeypatch, [Feature("water", "area", "polygon", [lake, island, pond])], ["water"])
    polygons = body["layers"]["water"]["polygons"]
    assert len(polygons) == 1 and len(polygons[0]) == 3, polygons  # one feature: all its rings, filled even-odd


def test_layers_are_left_out_from_the_lowest_priority_up(monkeypatch):
    """A layer that does not fit takes every lower layer with it: stops are never dropped while paths stay."""
    stops = [Feature("stops", None, "point", [[(50.001 + i * 1e-5, 19.001)]], f"S{i}", f"s{i}") for i in range(200)]
    paths = [Feature("paths", "path", "line", [[(50.001, 19.001 + i * 1e-4), (50.019, 19.001 + i * 1e-4)]])
             for i in range(10)]
    body = extract(monkeypatch, stops + paths, ["stops", "paths"], max_points=300)
    assert "stops" not in body["layers"] and "paths" not in body["layers"], body["layers"].keys()
    assert "stops, paths" in body["note"]

