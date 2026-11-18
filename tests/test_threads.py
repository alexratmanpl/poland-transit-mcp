"""Tool calls run in worker threads, many at once over --http: every read of a city file must work from several
threads at the same time, also while the memory budget unloads the city (pool.py)."""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fixture import build_national_fixture

from poland_transit_mcp.mapdata import map_features
from poland_transit_mcp.places import find_places
from poland_transit_mcp.timeutil import LocalTime
from poland_transit_mcp.transit import line_info, resolve_stops, scheduled_departures, train_info

THREADS = 16


@pytest.fixture(scope="module")
def three(tmp_path_factory):
    return build_national_fixture(tmp_path_factory.mktemp("threads"))


@pytest.fixture()
def often_switching():
    """Switch threads every microsecond, so reads interleave on every run instead of now and then."""
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(before)


def at_once(*jobs, rounds: int = 40) -> list:
    """Runs every job `rounds` times from THREADS threads, started together; returns the results in order."""
    start = threading.Barrier(THREADS)

    def worker(i: int) -> list:
        start.wait()
        return [jobs[(i + r) % len(jobs)]() for r in range(rounds)]

    with ThreadPoolExecutor(THREADS) as ex:
        return [x for f in [ex.submit(worker, i) for i in range(THREADS)] for x in f.result()]


def test_trip_details_from_many_threads_at_once(three, often_switching):
    nat = three[2]
    trips = [r[0] for r in nat.db.execute("SELECT trip_idx FROM trip_info").fetchall()]
    expected = {t: nat.tt.trip_details(t) for t in trips}
    assert any(expected.values())
    got = at_once(*[lambda t=t: (t, nat.tt.trip_details(t)) for t in trips])
    assert all(details == expected[t] for t, details in got)


def test_every_reader_of_a_city_file_works_from_many_threads(city, three, often_switching):
    """departures, train details, places, map features, line shapes and service days, all at once."""
    nat = three[2]
    when = LocalTime(20261110, 8 * 3600)
    stops = resolve_stops(city, "T:A")
    station = next(s for s in nat.tt.stops if s is not None)
    train = nat.db.execute("SELECT train FROM trip_info WHERE train IS NOT NULL").fetchone()[0]
    bbox = (city.bbox[0], city.bbox[1], city.bbox[2], city.bbox[3])
    jobs = [
        lambda: scheduled_departures(city, stops, when, None, 5),
        lambda: scheduled_departures(nat, [station], when, None, 5),
        lambda: train_info(nat, train, 20261110),
        lambda: find_places(city, "Rynek"),
        lambda: map_features(city, bbox, ["water", "buildings"]),
        lambda: line_info(city, "1", 20261110),
        lambda: sorted(city.tt.services(20261111)),
        lambda: city.tt.shape(next(p.shape for p in city.tt.patterns.values() if p.shape is not None)),
    ]
    expected = [job() for job in jobs]
    got = at_once(*[lambda i=i, job=job: (i, job()) for i, job in enumerate(jobs)])
    assert all(result == expected[i] for i, result in got)


def test_a_city_unloaded_while_other_threads_read_it(three, often_switching):
    """The pool unloads a city's timetable when another call needs the memory; a call still reading its own
    reference to the old timetable must finish, and the next call loads it again."""
    nat = three[2]
    station = next(s for s in nat.tt.stops if s is not None)
    when = LocalTime(20261110, 8 * 3600)
    expected = scheduled_departures(nat, [station], when, None, 5)

    def read():
        return scheduled_departures(nat, [station], when, None, 5)

    def unload():
        nat.unload()
        return expected

    assert all(r == expected for r in at_once(read, read, read, unload))


def test_calls_arriving_together_at_an_unloaded_city_load_it_once(three, monkeypatch, often_switching):
    """Each copy would be memory the budget does not count."""
    from poland_transit_mcp import city as city_module

    alfa, nat = three[0], three[2]
    built = []

    class Counted(city_module.Timetable):
        def __post_init__(self):
            built.append(self.city_id)
            super().__post_init__()

    class CountedGraph(city_module.WalkGraph):
        def __init__(self, *a, **k):
            built.append("graph")
            super().__init__(*a, **k)

    monkeypatch.setattr(city_module, "Timetable", Counted)
    monkeypatch.setattr(city_module, "WalkGraph", CountedGraph)
    nat.unload()
    alfa.unload()
    at_once(lambda: nat.tt, lambda: alfa.graph, rounds=4)
    assert sorted(built) == ["graph", "poland"]


def slowed(monkeypatch, obj, name: str, calls: list) -> None:
    """Records each call of obj.name and makes it take a few ms, so threads asking at once overlap."""
    orig = getattr(obj, name)

    def run(*a):
        calls.append(a)
        time.sleep(0.005)
        return orig(*a)

    monkeypatch.setattr(obj, name, run)


def test_calls_asking_for_the_same_new_date_build_it_once(city_dir, monkeypatch, often_switching):
    """A date and each of its routing directions are built once; the others wait for that build."""
    from poland_transit_mcp import engine
    from poland_transit_mcp.city import City

    tt = City.open(city_dir, "testowo").tt
    days, tables, transfers = [], [], []
    slowed(monkeypatch, tt, "_build_day", days)
    slowed(monkeypatch, tt, "_build_raptor", tables)
    if engine.available():
        slowed(monkeypatch, engine, "transfers", transfers)
    got = at_once(lambda: (tt.day(20261110), tt.raptor(tt.day(20261110), False)),
                  lambda: (tt.day(20261110), tt.raptor(tt.day(20261110), True)), rounds=3)
    assert days == [(20261110,)]
    assert sorted(backward for _, backward in tables) == [False, True]
    assert len(transfers) == (1 if engine.available() else 0)
    day = got[0][0]
    assert all(d is day for d, _ in got)  # one Day, one table per direction, for every call
    assert {id(rd) for _, rd in got} == {id(day.forward), id(day.backward)}


def test_a_failed_build_fails_every_call_waiting_for_it_and_the_next_call_tries_again(city_dir, monkeypatch,
                                                                                      often_switching):
    from poland_transit_mcp.city import City

    tt = City.open(city_dir, "testowo").tt
    orig, calls = tt._build_day, []

    def fail_once(d):
        calls.append(d)
        time.sleep(0.005)
        if len(calls) == 1:
            raise OSError("disk went away")
        return orig(d)

    monkeypatch.setattr(tt, "_build_day", fail_once)
    start = threading.Barrier(THREADS)

    def ask():
        start.wait()
        try:
            return tt.day(20261110)
        except OSError as e:
            return e

    with ThreadPoolExecutor(THREADS) as ex:
        got = [f.result() for f in [ex.submit(ask) for _ in range(THREADS)]]
    failed = [g for g in got if isinstance(g, OSError)]
    assert failed and all(str(e) == "disk went away" for e in failed)  # nobody hangs
    day = tt.day(20261110)
    assert day.trips and tt.days_cached() == [20261110]
    assert len(calls) == 2  # the failed build, then one more (by a call that came after it, or this one)


def test_the_size_estimate_equals_a_full_recount(city_dir, often_switching):
    """Each date carries its own size, added to as its routing tables are built from many threads at once."""
    from poland_transit_mcp.city import City
    from poland_transit_mcp.timetable import raptor_bytes, trips_bytes

    tt = City.open(city_dir, "testowo").tt
    tt.days_kept = 3
    dates = [20261110, 20261111, 20261112, 20261113]
    at_once(*[lambda d=d, back=back: tt.raptor(tt.day(d), back) for d in dates for back in (False, True)], rounds=8)
    cached = list(tt._days.values())
    assert len(cached) == 3
    transfers = tt._engine_transfers.nbytes() if tt._engine_transfers is not None else 0
    recount = 1900 * (len(tt.stops) + len(tt.patterns)) + transfers + sum(
        trips_bytes(day.trips) + sum(raptor_bytes(rd) for rd in (day.forward, day.backward) if rd is not None)
        for day in cached)
    assert tt.approx_bytes() == recount
