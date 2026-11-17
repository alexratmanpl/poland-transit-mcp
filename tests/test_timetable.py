import pytest

from poland_transit_mcp.timeutil import LocalTime
from poland_transit_mcp.transit import line_info, resolve_stops, scheduled_departures


def H(h, m=0):
    return h * 3600 + m * 60


def svc_ids(city, d):
    """Services from the feed's own calendar (services the import derives for merged trips are left out)."""
    ids = (city.db.execute("SELECT service_id FROM calendar WHERE service_idx = ?", (i,)).fetchone()[0] for i in city.tt.services(d))
    return sorted(i for i in ids if ":merged-" not in i)


def deps(city, stop, d, secs, line=None, limit=3):
    return scheduled_departures(city, resolve_stops(city, stop), LocalTime(d, secs), line, limit)


def test_valid_from_is_the_first_day_every_feed_covers(city):
    """The scripts' default query date: a fixed date would silently test nothing once the feed moves on."""
    assert city.valid_from == "2026-01-01"
    other = object.__new__(type(city))
    other.feeds = [{"status": "ok", "start_date": 20261110}, {"status": "ok", "start_date": 20261116},
                   {"status": "error"}]
    assert other.valid_from == "2026-11-16"
    other.feeds = [{"status": "no machine-readable timetable"}]
    assert other.valid_from is None


def test_calendar_exceptions(city):
    assert svc_ids(city, 20261110) == ["T:ALL", "T:WD"]  # Tuesday
    assert svc_ids(city, 20261111) == ["T:ALL", "T:SAT"]  # holiday: WD removed, SAT added
    assert svc_ids(city, 20261114) == ["T:ALL", "T:SAT"]  # Saturday
    assert svc_ids(city, 20261115) == ["T:ALL"]  # Sunday


def test_stop_names(city):
    assert city.tt.stop_by_id["T:A"].name == "Aleja Łąkowa"
    b = city.tt.stop_by_id["T:B"]
    assert (b.name, b.platform) == ("Brzozowa", "01")
    assert len(resolve_stops(city, "centrum")) == 2


def test_weekday_departures(city):
    assert [(d["line"], d["time"]) for d in deps(city, "T:A", 20261110, H(8))] == [("1", "08:00"), ("1", "08:03"), ("1", "08:10")]


def test_no_pickup_trip_not_listed(city):
    assert [d["time"] for d in deps(city, "T:B", 20261110, H(8, 5), limit=2)] == ["08:05", "08:15"]


def test_holiday_uses_saturday_timetable(city):
    assert [d["time"] for d in deps(city, "T:A", 20261111, H(8), "1", 2)] == ["08:05", "08:25"]


def test_night_service_after_midnight(city):
    late = deps(city, "T:A", 20261110, H(23, 50))
    assert [(d["line"], d["time"], d["date"]) for d in late] == [("N1", "00:30", "2026-11-11")]
    after = deps(city, "T:A", 20261111, H(0, 20), limit=1)
    assert [(d["line"], d["time"], d["date"]) for d in after] == [("N1", "00:30", None)]
    nxt = deps(city, "T:A", 20261112, H(0, 20), "N1", 1)  # no WD service on the holiday night
    assert [(d["time"], d["date"]) for d in nxt] == [("00:30", "2026-11-13")]


def test_line_info(city):
    li = line_info(city, "1", 20261110)
    d = li["directions"][0]
    assert (d["first_departure"], d["last_departure"], d["trips"]) == ("06:00", "22:00", 98)
    assert [s["name"] for s in d["stops"]] == ["Aleja Łąkowa", "Brzozowa", "Centrum"]
    assert {"hours": "07-09", "departures": 13, "every_min": 9} in d["frequency"]
    assert line_info(city, "1", 20261114)["directions"][0]["first_departure"] == "06:05"
    assert line_info(city, "n1", 20261110)["directions"][0]["first_departure"] == "00:30 (+1 day)"
    with pytest.raises(ValueError, match='no line "99"'):
        line_info(city, "99", 20261110)


def test_leg_geometry_follows_stop_order_on_a_looping_shape(city):
    """The shape starts at P, loops away and passes Q only on its way back. The leg Q → R must not
    include the loop, even though the shape's first point (at P) is the vertex nearest to Q."""
    from poland_transit_mcp.geo import fast_dist
    tt = city.tt
    p = next(p for p in tt.patterns.values() if p.route.short == "L3")
    q, r = tt.stops[p.stops[1]], tt.stops[p.stops[2]]
    path = tt.leg_path(p, 1, 2)
    length = sum(fast_dist(a[0], a[1], b[0], b[1]) for a, b in zip(path, path[1:]))
    assert length < 1.2 * fast_dist(q.lat, q.lon, r.lat, r.lon), length
    full = tt.leg_path(p, 0, 2)
    assert sum(fast_dist(a[0], a[1], b[0], b[1]) for a, b in zip(full, full[1:])) > 2 * length  # P → R does include the loop


def test_operator_is_reported(city):
    from poland_transit_mcp.plan import plan_trip
    from fixture import at
    d = deps(city, "T:A", 20261110, H(8), limit=1)[0]
    assert d["operator"] == "Testowo Transit"
    assert line_info(city, "1", 20261110)["operator"] == "Testowo Transit"
    r = plan_trip(city, at(2, 1), at(2, 12), depart_at="2026-11-10 07:58", max_transfers=0)
    leg = next(l for i in r["itineraries"] for l in i["legs"] if l["mode"] != "walk")
    assert leg["operator"] == "Testowo Transit"
    assert any("processed by poland-transit-mcp" in s for s in city.provenance(("gtfs",))["source"])


def test_publication_date_falls_back_to_zip_file_dates(tmp_path):
    """A server without Last-Modified (Wrocław's portal) still gives a dated as_of."""
    import sqlite3
    import zipfile

    from fixture import write_gtfs
    from poland_transit_mcp.build.gtfs import import_feed
    from poland_transit_mcp.config import Feed
    from poland_transit_mcp.schema import SCHEMA

    write_gtfs(tmp_path / "gtfs")
    with zipfile.ZipFile(tmp_path / "feed.zip", "w") as z:
        for f in sorted((tmp_path / "gtfs").iterdir()):
            z.writestr(zipfile.ZipInfo(f.name, date_time=(2026, 11, 13, 12, 22, 0)), f.read_bytes())
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    feed = Feed("Z", "Zip feed", "https://example.invalid/z.zip", "CC0", "synthetic test data")
    meta = import_feed(db, tmp_path / "feed.zip", feed, download_meta={"downloaded_at": "2026-11-15T19:10:54Z", "last_modified": None})
    assert meta["status"] == "ok"
    assert meta["files_dated"] == "2026-11-13 12:22"


def test_frequency_only_runs_are_expanded_and_labelled(city):
    d = deps(city, "T:MN", 20261110, H(6), limit=20)
    runs = [x for x in d if x["time"] < "07:00"]
    assert [x["time"] for x in runs] == [f"06:{m:02d}" for m in range(0, 60, 5)]
    assert all(x["status"] == "frequency-based" and x["frequency"] == "every 5 min" for x in runs)
    assert "frequency" not in deps(city, "T:A", 20261110, H(8), limit=1)[0]


def test_identical_trips_of_several_services_are_stored_once(city):
    n = city.db.execute("SELECT COUNT(*) FROM trips WHERE trip_id IN ('M-23-WD', 'M-23-SAT')").fetchone()[0]
    assert n == 1
    for date in (20261110, 20261114, 20261111):  # weekday, Saturday, holiday (Saturday service)
        late = [x for x in deps(city, "T:MN", date, H(22), limit=5) if x["time"] == "23:00"]
        assert len(late) == 1, date
    assert not [x for x in deps(city, "T:MN", 20261115, H(22), limit=5) if x["time"] == "23:00"]  # Sunday


def test_converter_is_credited_with_a_link(city):
    src = " ".join(city.provenance(("gtfs",))["source"])
    assert "GTFS provided by: Test Converter <https://example.invalid/converter>" in src


def test_platform_number_in_a_stop_query_still_resolves(city):
    # "Brzozowa 01" is stored as "Brzozowa" + platform 01; the query with the number still finds it
    assert [s.id for s in resolve_stops(city, "Brzozowa 01")] == ["T:B"]
