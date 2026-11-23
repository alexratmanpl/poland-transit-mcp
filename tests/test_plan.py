import pytest
from fixture import at

from poland_transit_mcp.plan import plan_trip
from poland_transit_mcp.transit import walk_route


def transit(it):
    return [l for l in it["legs"] if l["mode"] != "walk"]


def first_transit(r):
    return next(i for i in r["itineraries"] if transit(i))


def test_direct_line(city):
    r = plan_trip(city, at(2, 1), at(2, 12), depart_at="2026-11-10 07:58", max_transfers=0)
    assert [(l["line"], l["depart"], l["arrive"]) for l in transit(first_transit(r))] == [("1", "08:00", "08:10")]


def test_one_transfer_with_foot_transfer(city):
    r = plan_trip(city, at(2, 1), at(18, 12), depart_at="2026-11-10 07:58")
    it = first_transit(r)
    assert [l["line"] for l in transit(it)] == ["1", "2"]
    assert transit(it)[1]["depart"] == "08:15" and it["arrive"] == "08:25" and it["transfers"] == 1
    assert len(r["itineraries"]) <= 3


def test_max_transfers_zero(city):
    r = plan_trip(city, at(2, 1), at(18, 12), depart_at="2026-11-10 07:58", max_transfers=0)
    assert not any(transit(i) for i in r["itineraries"])


def test_arrive_by(city):
    r = plan_trip(city, at(2, 1), at(18, 12), arrive_by="2026-11-10 08:40")
    assert [(l["line"], l["depart"], l["arrive"]) for l in transit(first_transit(r))] == [("1", "08:10", "08:20"), ("2", "08:30", "08:40")]


def test_late_night(city):
    r = plan_trip(city, at(2, 1), at(18, 12), depart_at="2026-11-10 23:55")
    assert [(l["line"], l["depart"], l["arrive"]) for l in transit(first_transit(r))] == [("N1", "2026-11-11 00:30", "2026-11-11 01:10")]


def test_walk_uses_the_bridge(city):
    w = walk_route(city, at(2, 6), at(2, 9))
    assert w["distance_m"] > 3000 and w["bridges_crossed"] == 1


def test_bad_inputs(city):
    with pytest.raises(ValueError, match="no place named"):
        plan_trip(city, "Nibylandia", at(2, 1))
    with pytest.raises(ValueError, match="outside testowo"):
        plan_trip(city, [52.2, 21.0], at(2, 1))


def test_options_come_earliest_arrival_first_and_latest_departure_first_with_arrive_by():
    """The order the tool descriptions state (plan_trip, plan_stations): ranked() for both searches."""
    from poland_transit_mcp.plan import Itin, ranked

    def it(start, end, transfers):
        return Itin(0, 0, 0, 0, [], f"{start}-{end}", start, end, transfers)

    # 14:34→17:06 (2 changes), 14:13→15:41 (1), 13:55→15:56 (0): none beats another on start, end and changes
    opts = [it(13 * 60 + 55, 15 * 60 + 56, 0), it(14 * 60 + 13, 15 * 60 + 41, 1), it(14 * 60 + 34, 17 * 60 + 6, 2)]
    assert [o.start for o in ranked(opts, backward=True)] == [14 * 60 + 34, 14 * 60 + 13, 13 * 60 + 55]
    assert [o.end for o in ranked(opts, backward=False)] == [15 * 60 + 41, 15 * 60 + 56, 17 * 60 + 6]

