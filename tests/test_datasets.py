"""Data sets from a definitions file (datasets.py): a national layer that is not Polish, defined in JSON, built with
`build ID --datasets FILE` and served with no definitions file. Its time zone is checked at build and at load."""
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fixture import build_national_fixture
from test_intercity import call

from poland_transit_mcp import datasets
from poland_transit_mcp.build.build_city import main as build_main
from poland_transit_mcp.city import City
from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server

DAY = "2026-11-10"  # a Tuesday


def gtfs_zip(path: Path, zone: str = "Europe/Prague") -> None:
    """Two weekday trains Praha hl. n. 08:00 → Brno hl. n. 10:30 and 12:00 → 14:30, local time of `zone`."""
    files = {
        "agency.txt": f"agency_id,agency_name,agency_url,agency_timezone\nCD,České dráhy,https://example.invalid,{zone}\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nPRA,Praha hl. n.,50.0832,14.4353\nBRN,Brno hl. n.,49.1906,16.6129\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nR,CD,R,Rychlík,2\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WD,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id\nR,WD,R1\nR,WD,R2\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          "R1,08:00:00,08:00:00,PRA,1\nR1,10:30:00,10:30:00,BRN,2\n"
                          "R2,12:00:00,12:00:00,PRA,1\nR2,14:30:00,14:30:00,BRN,2\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


def definition(**over) -> dict:
    d = {"id": "testland", "name": "Testland: trains", "kind": "national", "timezone": "Europe/Prague",
         "bbox": [48.5, 12.0, 51.1, 18.9], "notes": ["Synthetic Czech trains."],
         "feeds": [{"key": "CZ", "name": "Testland rail GTFS", "url": "https://example.invalid/cz.zip",
                    "licence": "CC0 1.0", "attribution": "synthetic", "credits": [["converted by", "Nobody", "https://example.invalid"]]}]}
    return {**d, **over}


FEED = {"key": "CZ", "name": "Testland rail GTFS", "url": "https://example.invalid/cz.zip", "licence": "CC0 1.0",
        "attribution": "synthetic"}


def write_defs(tmp: Path, *sets: dict) -> Path:
    f = tmp / "datasets.json"
    f.write_text(json.dumps({"datasets": list(sets)}), encoding="utf-8")
    return f


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.delenv(datasets.ENV, raising=False)
    gtfs_zip(tmp_path / "data" / "raw" / "testland" / "CZ.zip")
    build_main(["testland", "--datasets", str(write_defs(tmp_path, definition())), "--data-dir", str(tmp_path / "data")])
    return tmp_path / "data" / "cities"


def test_a_data_set_from_a_file_is_built_and_served_without_it(built):
    server = create_server(CityPool({"testland": City.open(built, "testland")}, 1000))
    body, err = call(server, "plan_stations", {"from": [{"stop": "Praha hl. n.", "minutes": 20}],
                                               "to": [{"stop": "Brno hl. n.", "minutes": 10}], "depart_at": f"{DAY} 07:00"})
    assert not err, body
    o = body["itineraries"][0]
    assert (o["depart"], o["arrive"], o["legs"][0]["operator"]) == ("07:40", "10:40", "České dráhy")
    assert body["credits"] == ["testland/CZ"]
    full, _ = call(server, "credits", {"ids": body["credits"]})
    assert "Testland rail GTFS" in full["credits"][0]["text"] and "Nobody" in full["credits"][0]["text"]
    cities, _ = call(server, "list_cities", {})
    entry = cities["cities"][0]
    assert (entry["city"], entry["timezone"], entry["notes"]) == ("testland", "Europe/Prague", ["Synthetic Czech trains."])


def test_the_layer_is_named_when_several_are_loaded(built, tmp_path):
    _, _, poland = build_national_fixture(tmp_path / "pl")
    server = create_server(CityPool({"testland": City.open(built, "testland"), "poland": poland}, 1000))
    args = {"from": [{"stop": "Praha hl. n."}], "to": [{"stop": "Brno hl. n."}], "depart_at": f"{DAY} 07:00"}
    text, err = call(server, "plan_stations", args)
    assert err and "several national layers" in text and "poland, testland" in text
    body, err = call(server, "plan_stations", {**args, "layer": "testland"})
    assert not err and body["itineraries"][0]["legs"][0]["depart"] == "08:00"
    body, err = call(server, "plan_stations", {"from": [{"stop": "Alfa Główna"}], "to": [{"stop": "Beta Główna"}],
                                               "depart_at": f"{DAY} 08:00", "layer": "poland"})
    assert not err and body["itineraries"][0]["depart"] == "09:00"


@pytest.mark.parametrize("over, message", [
    ({"colour": "red"}, "unknown key(s) colour"),
    ({"id": "poland"}, "built-in data set"),
    ({"id": "Test Land"}, "id must be"),
    ({"kind": "country"}, 'kind must be "city" or "national"'),
    ({"kind": "city"}, "needs osm_region"),
    ({"feeds": [{"key": "CZ", "name": "x", "url": "u", "licence": "l"}]}, "missing attribution"),
    ({"feeds": [{"key": "CZ", "name": "x", "url": "https://x", "licence": "l", "attribution": "a", "liveness": 1}]}, "unknown key(s) liveness"),
    # values of the wrong type: "false" is not false, a string is not a list of notes
    ({"feeds": [{**FEED, "utc_times": "false"}]}, "utc_times: expected bool"),
    ({"notes": "Synthetic"}, "notes: expected a list"),
    ({"feeds": [{**FEED, "credits": [["a", "b"]]}]}, "credits[0]: expected a list of 3 values"),
    ({"name": 5}, "name: expected str"),
    ({"bbox": [True, 2, 3, 4]}, "bbox[0]: expected float"),
    ({"bbox": [51, 12, 48, 18]}, "min < max"),
    ({"feeds": [{**FEED, "key": True}]}, "key: expected str"),
    ({"id": "cz\n"}, "id must be"),
    ({"feeds": [{**FEED, "key": "CZ\n"}]}, "key must be"),
    ({"timezone": "Nowhere/Zone"}, "unknown time zone"),
    ({"feeds": [{**FEED, "url": "file:///etc/passwd"}]}, "url must start with http"),
    ({"kind": "city", "osm_region": "../../x"}, "osm_region must be a region name"),
])
def test_bad_definitions_are_refused(tmp_path, over, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        datasets.load(write_defs(tmp_path, definition(**over)))


def test_the_build_names_an_unknown_data_set(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(datasets.ENV, raising=False)
    with pytest.raises(SystemExit):
        build_main(["testland", "--data-dir", str(tmp_path)])
    assert "unknown data set 'testland'" in capsys.readouterr().err


@pytest.mark.parametrize("zone_of_set, zone_of_feed", [("Europe/London", "Europe/London"), ("Europe/Prague", "Europe/London")])
def test_the_build_refuses_a_clock_other_than_polish_time(tmp_path, monkeypatch, zone_of_set, zone_of_feed):
    monkeypatch.delenv(datasets.ENV, raising=False)
    gtfs_zip(tmp_path / "data" / "raw" / "testland" / "CZ.zip", zone_of_feed)
    defs = write_defs(tmp_path, definition(timezone=zone_of_set))
    with pytest.raises(SystemExit, match="wrong clock"):
        build_main(["testland", "--datasets", str(defs), "--data-dir", str(tmp_path / "data")])
    assert not list((tmp_path / "data" / "cities").glob("testland.sqlite*"))


def test_the_zone_is_checked_without_a_timetable_too(tmp_path, monkeypatch):
    monkeypatch.delenv(datasets.ENV, raising=False)
    defs = write_defs(tmp_path, definition(timezone="Europe/London"))  # and no feed file at all
    with pytest.raises(SystemExit, match="wrong clock"):
        build_main(["testland", "--datasets", str(defs), "--data-dir", str(tmp_path / "data")])


def test_a_refused_build_keeps_the_good_file(built, tmp_path):
    good = (built / "testland.sqlite").read_bytes()
    gtfs_zip(tmp_path / "data" / "raw" / "testland" / "CZ.zip", "Europe/London")
    with pytest.raises(SystemExit, match="wrong clock"):
        build_main(["testland", "--datasets", str(tmp_path / "datasets.json"), "--data-dir", str(tmp_path / "data")])
    assert (built / "testland.sqlite").read_bytes() == good
    assert sorted(p.name for p in built.iterdir()) == ["testland.sqlite"]


def test_files_without_the_new_meta_take_notes_from_config(tmp_path):
    from poland_transit_mcp.config import CITIES
    _, _, poland = build_national_fixture(tmp_path)  # its meta has no notes or time zone, like files built before
    assert poland.notes == CITIES["poland"].notes and poland.timezone == "Europe/Warsaw"


def test_plan_trip_names_its_layer_when_several_are_loaded(built, tmp_path):
    from test_intercity import END, START
    alfa, beta, poland = build_national_fixture(tmp_path / "pl")
    server = create_server(CityPool({"alfa": alfa, "beta": beta, "poland": poland,
                                     "testland": City.open(built, "testland")}, 1500))
    args = {"city": "alfa", "from": START, "to_city": "beta", "to": END, "depart_at": f"{DAY} 08:40"}
    text, err = call(server, "plan_trip", args)
    assert err and "several national layers" in text
    body, err = call(server, "plan_trip", {**args, "layer": "poland"})
    assert not err and body["itineraries"][0]["stations"]["to"] == "Beta Główna"
    text, err = call(server, "plan_trip", {"city": "alfa", "from": START, "to": START, "layer": "poland"})
    assert err and "layer applies only" in text


def test_a_file_on_another_clock_is_not_loaded(built):
    db = sqlite3.connect(built / "testland.sqlite")
    db.execute("UPDATE meta SET value = 'America/New_York' WHERE key = 'timezone'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="America/New_York"):
        City.open(built, "testland")


def test_the_definitions_file_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(datasets.ENV, str(write_defs(tmp_path, definition())))
    assert datasets.registry()["testland"].timezone == "Europe/Prague"
    assert "gdansk" in datasets.registry()
