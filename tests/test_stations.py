"""plan_stations (stations.py): trains and coaches between stops of the national layer, each start and end
stop with its own minutes, on the synthetic national layer (fixture.build_national_fixture): IC 101 Alfa
Główna 09:00 → Alfa Wschód 09:10 → Beta Główna 11:00, IC 103 the same at 13:00, a coach Alfa Dworzec
Autobusowy 08:30 → Beta Dworzec Autobusowy 10:30 (Polish time)."""
import pytest
from fixture import build_national_fixture
from test_intercity import call

from poland_transit_mcp import engine
from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server
from poland_transit_mcp.stations import plan_stations

DAY = "2026-11-10"  # a Tuesday


@pytest.fixture(scope="module")
def nat(tmp_path_factory):
    return build_national_fixture(tmp_path_factory.mktemp("stations"))[2]


def brief(r):
    return [(o["depart"], o["arrive"], o["start"]["name"], o["start"]["minutes"], o["end"]["name"], o["end"]["minutes"])
            for o in r["itineraries"]]


def test_one_station_to_another(nat):
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna"}], depart_at=f"{DAY} 08:00")
    assert brief(r) == [("09:00", "11:00", "Alfa Główna", 0, "Beta Główna", 0),
                        ("13:00", "15:00", "Alfa Główna", 0, "Beta Główna", 0)]
    leg = r["itineraries"][0]["legs"][0]
    assert (leg["mode"], leg["train"], leg["from"]["platform"], leg["from"]["track"]) == ("rail", "101 Testowy", "II", "4")
    assert len(r["itineraries"][0]["legs"]) == 1  # no walk is invented before or after the train


def test_minutes_count_before_boarding_and_after_arriving(nat):
    r = plan_stations(nat, [{"stop": "Alfa Główna", "minutes": 45}], [{"stop": "Beta Główna", "minutes": 20}],
                      depart_at=f"{DAY} 08:00")
    assert brief(r)[0] == ("08:15", "11:20", "Alfa Główna", 45, "Beta Główna", 20)
    assert r["itineraries"][0]["duration_min"] == 185
    # ready only at 09:01: the 09:00 train is missed, so the 13:00 one is boarded and the start is 11:59
    r = plan_stations(nat, [{"stop": "Alfa Główna", "minutes": 61}], [{"stop": "Beta Główna"}], depart_at=f"{DAY} 08:00")
    assert brief(r)[0][:2] == ("11:59", "15:00")


def test_a_far_start_stop_does_not_hide_a_later_option_from_a_near_one(nat):
    """Option 1 boards at a stop the traveller reaches 120 min after depart_at (start 07:00). The next option
    must be searched from the traveller's start, not from that train's time, or the 08:30 coach from the
    other stop (start 08:30, ending later) is cut off."""
    r = plan_stations(nat, [{"stop": "Alfa Główna", "minutes": 120}, {"stop": "Alfa Dworzec Autobusowy"}],
                      [{"stop": "Beta Główna"}, {"stop": "Beta Dworzec Autobusowy", "minutes": 60}],
                      depart_at=f"{DAY} 07:00")
    assert brief(r) == [("07:00", "11:00", "Alfa Główna", 120, "Beta Główna", 0),
                        ("08:30", "11:30", "Alfa Dworzec Autobusowy", 0, "Beta Dworzec Autobusowy", 60),
                        ("11:00", "15:00", "Alfa Główna", 120, "Beta Główna", 0)]
    assert r["itineraries"][1]["legs"][0]["operator"] == "FlixBus"


def test_the_stop_that_lets_the_traveller_start_latest_is_used(nat):
    """IC 101 calls at both start stops; with equal minutes the later one (Alfa Wschód, 09:10) is used."""
    r = plan_stations(nat, [{"stop": "Alfa Główna"}, {"stop": "Alfa Wschód"}], [{"stop": "Beta Główna"}],
                      depart_at=f"{DAY} 08:00")
    assert brief(r)[0] == ("09:10", "11:00", "Alfa Wschód", 0, "Beta Główna", 0)


def test_arrive_by_counts_the_end_minutes(nat):
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna", "minutes": 30}], arrive_by=f"{DAY} 15:29")
    assert brief(r) == [("09:00", "11:30", "Alfa Główna", 0, "Beta Główna", 30)]
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna", "minutes": 30}], arrive_by=f"{DAY} 15:30")
    assert brief(r)[0][:2] == ("13:00", "15:30")


def test_minutes_past_midnight_search_the_next_day(nat):
    """Ready at 08:00 on the 10th (600 min after 22:00 on the 9th): the 10th's timetable is searched."""
    r = plan_stations(nat, [{"stop": "Alfa Główna", "minutes": 600}], [{"stop": "Beta Główna"}], depart_at="2026-11-09 22:00")
    assert brief(r)[0] == ("23:00", "2026-11-10 11:00", "Alfa Główna", 600, "Beta Główna", 0)
    assert r["itineraries"][0]["legs"][0]["depart"] == "2026-11-10 09:00"


def test_arrive_by_midnight_and_end_minutes_search_the_day_before(nat):
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna"}], arrive_by="2026-11-11 00:00")
    assert brief(r)[0][:2] == ("2026-11-10 13:00", "2026-11-10 15:00")
    # at Beta Główna by 00:00 at the latest (600 min before 10:00 on the 11th)
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna", "minutes": 600}], arrive_by="2026-11-11 10:00")
    assert brief(r)[0][:2] == ("2026-11-10 13:00", "01:00")


def test_a_stop_id_names_one_platform(nat):
    r = plan_stations(nat, [{"stop": "PL:AG2"}], [{"stop": "Beta Główna"}], depart_at=f"{DAY} 08:00")
    assert brief(r) == [("13:00", "15:00", "Alfa Główna", 0, "Beta Główna", 0)]
    assert r["itineraries"][0]["start"] == {"stop": "PL:AG2", "name": "Alfa Główna", "stop_id": "PL:AG2", "minutes": 0}


def test_no_connection_is_said(nat):
    r = plan_stations(nat, [{"stop": "Alfa Główna"}], [{"stop": "Beta Główna"}], depart_at="2026-11-14 08:00")  # Saturday
    assert r["itineraries"] == [] and "no connection" in r["note"]


@pytest.mark.parametrize("starts, ends, message", [
    ([{"stop": "Nowhere"}], [{"stop": "Beta Główna"}], 'no start stop "Nowhere"'),
    ([{"stop": "Alfa Główna"}], [{"stop": "Alfa Główna"}], "both a start and an end"),
    ([], [{"stop": "Beta Główna"}], "at least one start stop"),
    ([{"stop": "Alfa Główna", "minutes": -1}], [{"stop": "Beta Główna"}], "minutes must be"),
])
def test_bad_requests_say_why(nat, starts, ends, message):
    with pytest.raises(ValueError, match=message):
        plan_stations(nat, starts, ends, depart_at=f"{DAY} 08:00")


@pytest.mark.skipif(not engine.available(), reason="needs the Rust engine")
def test_rust_and_python_plan_the_same_trips(tmp_path, monkeypatch):
    queries = [([{"stop": "Alfa Główna", "minutes": 120}, {"stop": "Alfa Dworzec Autobusowy", "minutes": 10}, {"stop": "Alfa Wschód", "minutes": 5}],
                [{"stop": "Beta Główna", "minutes": 15}, {"stop": "Beta Dworzec Autobusowy", "minutes": 60}], q)
               for q in ({"depart_at": f"{DAY} 07:00"}, {"arrive_by": f"{DAY} 16:00"})]
    rust = build_national_fixture(tmp_path / "rust")[2]
    with monkeypatch.context() as m:
        m.setattr(engine, "_rust", None)
        python = build_national_fixture(tmp_path / "python")[2]
        expected = [plan_stations(python, s, e, **q) for s, e, q in queries]
    assert all(r["itineraries"] for r in expected)
    assert [plan_stations(rust, s, e, **q) for s, e, q in queries] == expected


def test_over_mcp(tmp_path):
    alfa, beta, nat = build_national_fixture(tmp_path)
    server = create_server(CityPool({"alfa": alfa, "beta": beta, "poland": nat}, 1000))
    body, err = call(server, "plan_stations", {"from": [{"stop": "Alfa Główna", "minutes": 30}],
                                               "to": [{"stop": "Beta Główna"}], "depart_at": f"{DAY} 08:00"})
    assert not err, body
    assert body["itineraries"][0]["start"]["minutes"] == 30 and body["itineraries"][0]["depart"] == "08:30"
    assert "gtfs_PL" in body["as_of"]
    text, err = call(server, "plan_stations", {"from": [{"stop": "Alfa Główna"}], "to": [{"stop": "Beta Główna"}],
                                               "depart_at": "08:00", "arrive_by": "12:00"})
    assert err and "not both" in text
    text, err = call(server, "plan_stations", {"from": [{"stop": "Alfa Główna", "minute": 45}], "to": [{"stop": "Beta Główna"}]})
    assert err and "minute" in text  # a misspelt key is refused, not read as 0 minutes
    without = create_server(CityPool({"alfa": alfa}, 1000))
    text, err = call(without, "plan_stations", {"from": [{"stop": "Alfa Główna"}], "to": [{"stop": "Beta Główna"}]})
    assert err and "national layer" in text
