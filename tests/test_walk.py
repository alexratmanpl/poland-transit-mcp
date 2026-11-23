from dataclasses import replace

import numpy as np
import pytest
from fixture import BRIDGE_ROW, COLS, RIVER_COL, at, grid_graph, node

from poland_transit_mcp.walk import FLAG_BRIDGE, WalkGraph

g = WalkGraph(grid_graph())


def test_snap_nearest_within_limit():
    lat, lon = at(5, 5)
    assert g.snap(lat + 0.0001, lon + 0.0001).node == node(5, 5)
    assert g.snap(lat + 0.05, lon, 400) is None


def test_route_crosses_river_only_on_bridge():
    cost, path = g.route(node(2, 2), node(2, 12))
    for a, b in zip(path, path[1:]):
        if (a % COLS < RIVER_COL) != (b % COLS < RIVER_COL):
            assert b // COLS == BRIDGE_ROW, "river crossed away from the bridge"
    _, flags = g.describe(path)
    assert any(f & FLAG_BRIDGE for f in flags)
    assert cost > 3600  # detour via row 20 and back


def test_bounded_search_respects_limit():
    dist, _ = g.search(node(10, 2), 250)
    assert dist[node(12, 2)] <= 250
    assert dist[node(14, 2)] == float("inf")


@pytest.mark.parametrize("damage", ["adj_out_of_range", "off_out_of_order", "coords_too_short"])
def test_malformed_graph_is_rejected(damage):
    """A damaged or tampered city file must fail with a readable error before scipy's compiled Dijkstra sees it."""
    a = grid_graph()
    adj, off, coords = a.adj.copy(), a.off.copy(), a.coords.copy()
    if damage == "adj_out_of_range":
        adj[0] = 10**6
    elif damage == "off_out_of_order":
        off[1], off[2] = off[2], off[1]
    else:
        coords = coords[:-2]
    with pytest.raises(ValueError, match="malformed"):
        WalkGraph(replace(a, adj=adj, off=off, coords=coords))


FAR = (70_000_000, 59_000_000)  # one node moved to 70° N, 59° E: inside the valid range, far from the city


@pytest.mark.parametrize("bbox", [None, [49.998, 18.998, 50.023, 19.023]])
def test_a_node_far_away_is_refused_before_the_grid_is_allocated(bbox, monkeypatch):
    """Coordinates far out would stretch the snapping grid over most of a continent (~1e8 cells, enough to get
    the process killed): the file is refused with a readable error, before anything that size is allocated."""
    a = grid_graph()
    coords = a.coords.copy()
    coords[0], coords[1] = FAR
    allocated = []
    real_arange = np.arange
    monkeypatch.setattr(np, "arange", lambda n, *x, **k: allocated.append(n) or real_arange(n, *x, **k))
    with pytest.raises(ValueError, match="malformed.*(outside the city's area|grid cells)"):
        WalkGraph(replace(a, coords=coords), bbox)
    assert not any(isinstance(n, int) and n > 1_000_000 for n in allocated)


@pytest.mark.parametrize("lat_lon", [(91_000_000, 19_000_000), (50_000_000, -181_000_000)])
def test_coordinates_off_the_globe_are_refused(lat_lon):
    a = grid_graph()
    coords = a.coords.copy()
    coords[2], coords[3] = lat_lon
    with pytest.raises(ValueError, match="outside the valid range"):
        WalkGraph(replace(a, coords=coords))


def test_nodes_just_outside_the_box_are_kept():
    """The build keeps whole ways that cross the box's edge, so nodes a little outside it are normal."""
    a = grid_graph()
    coords = a.coords.copy()
    coords[0] -= 300_000  # 0.3° south of the first node
    g = WalkGraph(replace(a, coords=coords), [50.0, 19.0, 50.03, 19.03])
    assert g.n == len(coords) // 2


def test_a_city_file_with_a_node_far_away_fails_with_a_readable_error(city_dir, tmp_path):
    import shutil
    import sqlite3

    from poland_transit_mcp.city import City

    shutil.copy(city_dir / "testowo.sqlite", tmp_path / "testowo.sqlite")
    db = sqlite3.connect(tmp_path / "testowo.sqlite")
    coords = np.frombuffer(db.execute("SELECT data FROM blobs WHERE name = 'walk_coords'").fetchone()[0], "<i4").copy()
    coords[0], coords[1] = FAR
    db.execute("UPDATE blobs SET data = ? WHERE name = 'walk_coords'", (coords.astype("<i4").tobytes(),))
    db.commit()
    db.close()
    c = City.open(tmp_path, "testowo")
    with pytest.raises(ValueError, match="1 of 384 nodes lie outside the city's area"):
        c.graph


def test_indoor_corridors_are_walked_and_private_ones_are_not(tmp_path):
    """A station's halls (highway=corridor) join its platform side to the city side: without them the walk went round
    by the next street (Katowice station to the Rondo, 1,712 m instead of 1,265 m)."""
    import json
    import sqlite3

    from poland_transit_mcp.build.build_city import Builder, walkable
    from poland_transit_mcp.config import CITIES
    from poland_transit_mcp.schema import SCHEMA

    assert walkable({"highway": "corridor", "indoor": "yes"})
    assert not walkable({"highway": "corridor", "indoor": "yes", "access": "private"})
    south, north = (19.0, 50.0), (19.0, 50.003)  # 333 m apart, the station between them
    ways = [({"highway": "footway"}, [south, (19.0, 50.001)]),
            ({"highway": "corridor", "indoor": "yes", "level": "0"}, [(19.0, 50.001), (19.0, 50.002)]),
            ({"highway": "footway"}, [(19.0, 50.002), north]),
            ({"highway": "residential"}, [south, (19.01, 50.0), (19.01, 50.003), north])]  # the way round, ~1.75 km
    path = tmp_path / "hw.geojsonseq"
    path.write_text("".join("\x1e" + json.dumps({"type": "Feature", "properties": t,
                                                  "geometry": {"type": "LineString", "coordinates": [list(c) for c in cs]}}) + "\n"
                            for t, cs in ways))
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    g = WalkGraph(Builder(CITIES["katowice"], db).highways(path))
    a, b = g.snap(south[1], south[0], 10), g.snap(north[1], north[0], 10)
    cost, _ = g.route(a.node, b.node)
    assert cost == pytest.approx(333.6, abs=1)
