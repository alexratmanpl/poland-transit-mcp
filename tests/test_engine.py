"""The optional Rust engine gives the same answers as the Python search (skipped when not installed)."""
import pytest

from poland_transit_mcp import engine
from poland_transit_mcp.city import City
from poland_transit_mcp.plan import plan_trip

pytestmark = pytest.mark.skipif(not engine.available(), reason="Rust engine (poland-transit-mcp-engine) not installed")

QUERIES = [  # from, to, keyword arguments
    ((2, 1), (18, 12), {"depart_at": "2026-11-10 07:58"}),
    ((2, 1), (18, 12), {"depart_at": "2026-11-10 08:00", "max_transfers": 0}),
    ((2, 1), (18, 12), {"arrive_by": "2026-11-10 09:00"}),
    ((2, 1), (18, 12), {"depart_at": "2026-11-10 23:50"}),  # night line after midnight
    ((2, 1), (2, 12), {"depart_at": "2026-11-14 10:00"}),  # Saturday timetable
    ((22, 1), (22, 6), {"depart_at": "2026-11-10 09:55"}),  # line L3 with its looping shape
    ((23, 10), (23, 14), {"depart_at": "2026-11-10 06:02"}),  # frequency-based metro
]


def plans(city):
    from fixture import at
    return [plan_trip(city, at(*a), at(*b), **kw) for a, b, kw in QUERIES]


def test_rust_and_python_give_the_same_plans(city_dir, monkeypatch):
    rust_city, python_city = City.open(city_dir, "testowo"), City.open(city_dir, "testowo")
    with monkeypatch.context() as m:
        m.setattr(engine, "_rust", None)  # this city's tables are built for the Python search
        expected = plans(python_city)
    got = plans(rust_city)
    assert got == expected
    assert any(it["legs"] for p in got for it in p["itineraries"])
    assert rust_city.tt.day(20261110).forward.engine is not None
    assert python_city.tt.day(20261110).forward.engine is None


def test_inconsistent_tables_are_rejected():
    import poland_transit_engine as rust
    with pytest.raises(ValueError, match="inconsistent tables"):
        rust.Transfers(engine._bytes("q", [0, 1]), engine._bytes("i", [5]), engine._bytes("i", [60]))  # stop 5 of 1
    with pytest.raises(ValueError, match="multiple of 4"):
        rust.Transfers(engine._bytes("q", [0]), b"\x00", b"")


def test_earliest_arrival_at_every_stop_is_the_same(city_dir, monkeypatch):
    """raptor_all: the one-to-all search that seeds intercity trips, forward and backward."""
    from fixture import at

    from poland_transit_mcp.raptor import INF, raptor_all
    from poland_transit_mcp.transit import walk_to_stops

    def run(c):
        acc = walk_to_stops(c, at(2, 1), 15 * 60)
        day = c.tt.day(20261110)
        return [raptor_all(c.tt.raptor(day, back), c.tt.transfers, acc.stops, -t if back else t, rounds).tolist()
                for back, t, rounds in ((False, 7 * 3600 + 58 * 60, 3), (False, 23 * 3600 + 50 * 60, 2), (True, 9 * 3600, 3))]

    rust_city, python_city = City.open(city_dir, "testowo"), City.open(city_dir, "testowo")
    with monkeypatch.context() as m:
        m.setattr(engine, "_rust", None)
        expected = run(python_city)
    got = run(rust_city)
    assert got == expected
    assert sum(v < INF for v in got[0]) > 2  # the morning search reaches beyond the two stops it starts from
