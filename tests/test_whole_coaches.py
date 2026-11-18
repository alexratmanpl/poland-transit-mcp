"""Whole FlixBus trips at import, with the real FlixBus feed settings: waypoints are left out, a run that
starts abroad the evening before takes the UTC offset of the day it reaches Poland, and a run is cut to end within
48 h of its day's midnight."""
import sqlite3

import numpy as np

from poland_transit_mcp.build.gtfs import import_feed
from poland_transit_mcp.config import CITIES
from poland_transit_mcp.schema import SCHEMA

FX = next(f for f in CITIES["poland"].feeds if f.key == "FX")


def imported(tmp_path) -> sqlite3.Connection:
    d = tmp_path / "fx"
    d.mkdir()
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nF,FlixBus-eu,https://example.invalid,UTC\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,stop_timezone\n"
                     "B,Berlin central bus station,52.507,13.279,Europe/Berlin\n"
                     "W,Berlin Pre Waypoint,52.40,13.50,Europe/Berlin\n"
                     "P,Poznan Bus Station,52.401,16.912,Europe/Warsaw\nV,Warsaw Bus Station West,52.221,20.959,Europe/Warsaw\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nR,F,FlixBus 1,,3\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "S,1,1,1,1,1,1,1,20260101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id\nR,S,night\nR,S,long\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          # leaves Berlin 20:00 UTC, through a waypoint, reaches Poznań at 01:30 UTC the next day
                          "night,20:00:00,20:00:00,B,1\nnight,20:30:00,20:30:00,W,2\nnight,25:30:00,25:30:00,P,3\nnight,27:00:00,27:00:00,V,4\n"
                          # a run of nearly two days: Berlin at 00:00, Poznań at 46:30, Warszawa at 47:30 (UTC)
                          "long,00:00:00,00:00:00,B,1\nlong,46:30:00,46:30:00,P,2\nlong,47:30:00,47:30:00,V,3\n",
    }
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    import_feed(db, d, FX)
    return db


def runs(db, trip: str) -> dict[int, list[tuple[str, int]]]:
    """For each date a run of the trip serves: its stops with their times (seconds after that date's midnight)."""
    names = dict(db.execute("SELECT stop_idx, name FROM stops"))
    out = {}
    for times, pattern, service in db.execute("SELECT times, pattern_idx, service_idx FROM trips WHERE trip_id = ?", (trip,)):
        stops = np.frombuffer(db.execute("SELECT stops FROM patterns WHERE pattern_idx = ?", (pattern,)).fetchone()[0], "<i4")
        t = np.frombuffer(times, "<i4")
        for (date,) in db.execute("SELECT date FROM calendar_dates WHERE service_idx = ?", (service,)):
            out[date] = [(names[int(s)], int(x)) for s, x in zip(stops, t)]
    return out


def test_waypoints_are_left_out(tmp_path):
    db = imported(tmp_path)
    assert not db.execute("SELECT 1 FROM stops WHERE name LIKE '%Waypoint%'").fetchall()
    assert [s for s, _ in runs(db, "night")[20260310]] == ["Berlin central bus station", "Poznan Bus Station", "Warsaw Bus Station West"]


def test_a_night_run_takes_the_offset_of_the_day_it_reaches_poland(tmp_path):
    """Summer time starts on 2026-03-29 at 01:00 UTC: the run leaving Berlin on the 28th reaches Poznań at
    01:30 UTC = 03:30 Polish summer time, which is 27:30 after the 28th's midnight (not 26:30)."""
    night = runs(imported(tmp_path), "night")
    assert dict(night[20260328])["Poznan Bus Station"] == 27 * 3600 + 1800
    assert dict(night[20260310])["Poznan Bus Station"] == 26 * 3600 + 1800  # winter: + 1 h


def test_a_run_ends_within_48_hours_of_its_day(tmp_path):
    """The two-day run keeps its Polish stops and leaves out its start abroad, so a day's timetable (which
    reaches back one day) still holds it."""
    long = runs(imported(tmp_path), "long")
    stops = long[20260311]  # the run that left Berlin on the 10th, moved to the day it reaches Poland
    assert [s for s, _ in stops] == ["Poznan Bus Station", "Warsaw Bus Station West"]
    assert max(t for _, t in stops) < 48 * 3600
