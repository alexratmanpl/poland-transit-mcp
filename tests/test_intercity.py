"""Trips between cities through the national layer (intercity.py), on two synthetic cities ~111 km
apart, two trains and a UTC coach feed (fixture.build_national_fixture)."""
import json

import anyio
import pytest
from fixture import build_national_fixture, near
from mcp import Client

from poland_transit_mcp import engine
from poland_transit_mcp.intercity import plan_intercity
from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server
from poland_transit_mcp.timeutil import LocalTime
from poland_transit_mcp.transit import resolve_stops, scheduled_departures, train_info

START, END = near("alfa", 2, 1), near("beta", 18, 12)  # tram stop A in alfa, bus stop E in beta


@pytest.fixture(scope="module")
def three(tmp_path_factory):
    return build_national_fixture(tmp_path_factory.mktemp("national"))


def legs(it, mode=None):
    return [leg for leg in it["legs"] if mode is None or leg["mode"] == mode]


def test_coach_times_in_utc_become_polish_time_and_stops_abroad_are_dropped(three):
    _, _, nat = three
    assert not resolve_stops(nat, "Ausland")
    stop = resolve_stops(nat, "Alfa Dworzec Autobusowy")
    winter = scheduled_departures(nat, stop, LocalTime(20261110, 0), limit=5)
    summer = scheduled_departures(nat, stop, LocalTime(20260701, 0), limit=5)
    # 01:30 and 07:30 UTC + 1 h in winter, + 2 h in summer; the next night's run closes the window
    assert [(d.get("date"), d["time"]) for d in winter] == [(None, "02:30"), (None, "08:30"), ("2026-11-11", "02:30")]
    assert [(d.get("date"), d["time"]) for d in summer] == [(None, "03:30"), (None, "09:30"), ("2026-07-02", "03:30")]
    assert winter[0]["operator"] == "FlixBus"



def test_trains_carry_name_category_platform_and_track(three):
    _, _, nat = three
    deps = scheduled_departures(nat, resolve_stops(nat, "Alfa Główna"), LocalTime(20261110, 8 * 3600), limit=5)
    assert [(d["time"], d["train"], d["category"], d["platform"], d["track"]) for d in deps] == [
        ("09:00", "101 Testowy", "IC", "II", "4"), ("13:00", "103 Testowy", "IC", "I", "2")]
    run = train_info(nat, "Testowy", 20261110)["runs"][0]
    assert [(s["name"], s["arrive"], s["depart"], s["platform"]) for s in run["stops"]] == [
        ("Alfa Główna", None, "09:00", "II"), ("Alfa Wschód", "09:10", "09:10", "1"), ("Beta Główna", "11:00", None, "III")]


def test_trip_between_cities(three):
    alfa, beta, nat = three
    r = plan_intercity(alfa, beta, nat, START, END, depart_at="2026-11-10 08:40")
    it = r["itineraries"][0]
    train = legs(it, "rail")[0]
    assert (train["train"], train["category"], train["depart"], train["arrive"]) == ("101 Testowy", "IC", "09:00", "11:00")
    assert (train["from"]["platform"], train["from"]["track"], train["to"]["platform"], train["to"]["track"]) == ("II", "4", "III", "7")
    assert it["stations"] == {"from": "Alfa Główna", "to": "Beta Główna"}
    assert it["legs"][0]["from"]["city"] == "alfa" and it["legs"][-1]["to"]["city"] == "beta"
    # the start is a short walk from the station: leave as late as the train allows (5 min margin)
    assert it["legs"][0]["mode"] == "walk" and it["depart"] >= "08:50"
    # every leg starts when or after the previous one ends
    times = [(leg["depart"], leg["arrive"]) for leg in it["legs"]]
    assert all(a[1] <= b[0] for a, b in zip(times, times[1:]))
    assert r["assumptions"] and r["note"] is None


def test_options_use_different_trains_and_coaches(three):
    alfa, beta, nat = three
    r = plan_intercity(alfa, beta, nat, START, END, depart_at="2026-11-10 07:30")
    first_vehicle = [legs(it, "rail")[0]["train"] if legs(it, "rail") else legs(it, "bus")[-1]["line"]
                     for it in r["itineraries"]]
    # the coach (08:30) arrives first; then train 101 (09:00); then train 103 (13:00)
    assert first_vehicle == ["FlixBus 1", "101 Testowy", "103 Testowy"]


def test_arrive_by_leaves_as_late_as_possible(three):
    alfa, beta, nat = three
    r = plan_intercity(alfa, beta, nat, START, END, arrive_by="2026-11-10 12:00")
    trains = [legs(it, "rail")[0]["train"] for it in r["itineraries"] if legs(it, "rail")]
    assert trains[0] == "101 Testowy"
    assert r["itineraries"][0]["arrive"] <= "12:00"


def test_no_connection_is_said(three):
    alfa, beta, nat = three
    r = plan_intercity(alfa, beta, nat, START, END, depart_at="2026-11-14 16:00")  # Saturday evening: no train
    assert all(not legs(it, "rail") for it in r["itineraries"])


@pytest.mark.skipif(not engine.available(), reason="Rust engine not installed")
def test_rust_and_python_plan_the_same_trips(tmp_path, monkeypatch):
    queries = [{"depart_at": "2026-11-10 07:30"}, {"arrive_by": "2026-11-10 16:00"}]
    rust = build_national_fixture(tmp_path / "rust")
    with monkeypatch.context() as m:
        m.setattr(engine, "_rust", None)
        python = build_national_fixture(tmp_path / "python")
        expected = [plan_intercity(*python, START, END, **q) for q in queries]
    assert [plan_intercity(*rust, START, END, **q) for q in queries] == expected


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args)
            return r.content[0].text, r.is_error
    text, err = anyio.run(go)
    return (text if err else json.loads(text)), err


def test_over_mcp(three):
    alfa, beta, nat = three
    server = create_server(CityPool({"alfa": alfa, "beta": beta, "poland": nat}, 1000))
    body, err = call(server, "plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                           "depart_at": "2026-11-10 08:40"})
    assert not err, body
    assert body["itineraries"][0]["stations"]["to"] == "Beta Główna"
    # sources and dates of all three: both cities' maps, both feeds of the national layer
    assert {"osm_alfa", "osm_beta", "gtfs_PL", "gtfs_FX"} <= set(body["as_of"])
    deps, err = call(server, "departures", {"city": "poland", "stop_id": "Alfa Główna", "at": "2026-11-10 08:00"})
    assert not err and deps["departures"][0]["train"] == "101 Testowy"
    train, err = call(server, "line_info", {"city": "poland", "line": "101", "date": "2026-11-10"})
    assert not err and train["runs"][0]["stops"][-1]["name"] == "Beta Główna"
    places, err = call(server, "find_places", {"city": "poland", "query": "alfa"})
    assert not err and {p["name"] for p in places["places"]} >= {"Alfa Główna", "Alfa Wschód"}
    text, err = call(server, "walk_route", {"city": "poland", "from": START, "to": END})
    assert err and "national layer" in text
    cities, _ = call(server, "list_cities", {})
    assert next(c for c in cities["cities"] if c["city"] == "poland")["features"]["maps"] is False


def test_to_city_without_the_national_layer_says_how_to_add_it(three):
    alfa, beta, _ = three
    server = create_server(CityPool({"alfa": alfa, "beta": beta}, 1000))
    text, err = call(server, "plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END})
    assert err and "add poland to --cities" in text


def test_a_date_only_some_feeds_cover_is_said(three):
    alfa, beta, nat = three
    import json as _json
    feeds = [dict(f) for f in nat.feeds]
    feeds[0]["end_date"] = 20261130  # pretend the trains end before the coaches do
    nat.feeds = feeds
    try:
        assert "covers 2026-01-01..2026-11-30, not 2026-12-10" in nat.partial_note(20261210)
        assert nat.partial_note(20261110) is None
        r = plan_intercity(alfa, beta, nat, START, END, depart_at="2026-12-10 07:30")
        assert any("not 2026-12-10" in n for n in r["notes"])
    finally:
        nat.feeds = _json.loads(nat.db.execute("SELECT value FROM meta WHERE key = 'feeds'").fetchone()[0])
