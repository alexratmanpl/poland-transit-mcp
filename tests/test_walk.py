from dataclasses import replace

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
