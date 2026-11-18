"""On-demand loading within a memory budget (pool.py), on two copies of the synthetic city."""
import shutil
import sqlite3
import threading

import pytest

from poland_transit_mcp import engine
from poland_transit_mcp.city import City
from poland_transit_mcp.plan import plan_trip
from poland_transit_mcp.pool import CityPool


@pytest.fixture()
def two_cities(city_dir, tmp_path):
    for cid in ("alpha", "beta"):
        f = tmp_path / f"{cid}.sqlite"
        shutil.copy(city_dir / "testowo.sqlite", f)
        db = sqlite3.connect(f)
        db.execute("UPDATE meta SET value = ? WHERE key = 'city'", (cid,))
        db.commit()
        db.close()
    return {cid: City.open(tmp_path, cid) for cid in ("alpha", "beta")}


def load(c: City) -> None:
    c.graph
    c.tt.day(20261110)


def test_cities_load_on_first_use(two_cities):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0)
    assert pool.loaded_bytes() == 0
    load(pool.use("alpha"))
    pool.release()
    assert two_cities["alpha"].heavy_bytes() > 0 and two_cities["beta"].heavy_bytes() == 0


def test_least_recently_used_city_is_unloaded_over_budget(two_cities):
    pool = CityPool(two_cities, budget_mb=0.000001, overhead_mb=0)  # room for nothing beyond the city in use
    load(pool.use("alpha"))
    pool.release()
    assert two_cities["alpha"].heavy_bytes() == 0  # over budget once unpinned
    load(pool.use("alpha"))
    load(pool.use("beta"))  # alpha is still pinned by this call, so it stays
    assert two_cities["alpha"].heavy_bytes() > 0
    pool.release()
    assert pool.loaded_bytes() == 0


def test_city_in_use_by_another_call_is_not_unloaded(two_cities):
    pool = CityPool(two_cities, budget_mb=0.000001, overhead_mb=0)
    load(pool.use("alpha"))  # this thread's call holds alpha
    done = threading.Event()

    def other_call():
        load(pool.use("beta"))
        pool.release()
        done.set()

    threading.Thread(target=other_call).start()
    assert done.wait(10)
    assert two_cities["alpha"].heavy_bytes() > 0  # pinned: kept although over budget
    pool.release()
    assert two_cities["alpha"].heavy_bytes() == 0


def test_answers_are_the_same_after_a_reload(two_cities):
    from fixture import at
    pool = CityPool(two_cities, budget_mb=0.000001, overhead_mb=0)
    a = pool.use("alpha")
    first = plan_trip(a, at(2, 1), at(18, 12), depart_at="2026-11-10 08:00")
    pool.release()
    assert a.heavy_bytes() == 0
    again = plan_trip(pool.use("alpha"), at(2, 1), at(18, 12), depart_at="2026-11-10 08:00")
    pool.release()
    assert again["itineraries"] == first["itineraries"]


def test_budget_is_for_the_whole_process(two_cities):
    pool = CityPool(two_cities, budget_mb=480, overhead_mb=150)
    assert pool.budget == 330_000_000  # 480 MB minus what is set aside for Python and queries


def test_budget_to_keep_all_cities(two_cities):
    pool = CityPool(two_cities, budget_mb=480, overhead_mb=230)
    each = CityPool.expected_bytes(two_cities["alpha"], days=2) / 1e6
    assert pool.budget_to_keep_all_mb() == int(-(-(230 + 2 * each) // 50) * 50)
    assert pool.budget_to_keep_all_mb() % 50 == 0


def test_budget_to_keep_all_cities_counts_the_dates_kept(two_cities):
    size = two_cities["alpha"].path.stat().st_size
    first, more = CityPool.size_factors(two_cities["alpha"])
    assert first == 1.3 and more == (0.4 if engine.available() else 0.5)  # a further date costs more without the engine
    assert CityPool.expected_bytes(two_cities["alpha"]) == int(1.3 * size)  # one date
    assert CityPool.expected_bytes(two_cities["alpha"], days=3) == int((1.3 + 2 * more) * size)
    two = CityPool(two_cities, budget_mb=480, overhead_mb=230).budget_to_keep_all_mb()
    seven = CityPool(two_cities, budget_mb=480, overhead_mb=230, days_kept=7).budget_to_keep_all_mb()
    assert seven >= two + int(2 * 5 * more * size / 1e6) // 50 * 50


def build(c: City, *dates: int) -> None:
    """Builds each date with both routing directions, as a trip plan does."""
    for d in dates:
        day = c.tt.day(d)
        c.tt.raptor(day, False)
        c.tt.raptor(day, True)


DATES = [20261110, 20261111, 20261112, 20261113]


def test_a_city_keeps_days_kept_dates_and_drops_the_least_recently_used(two_cities):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0, days_kept=3)
    c = pool.use("alpha")
    build(c, *DATES)
    assert c.cached_days() == DATES[1:]
    build(c, DATES[1])  # used again: now the most recent
    build(c, 20261114)
    assert c.cached_days() == [DATES[3], DATES[1], 20261114]
    pool.release()
    c.unload()
    assert c.tt.days_kept == 3  # also after a reload
    with pytest.raises(ValueError):
        CityPool(two_cities, budget_mb=1000, days_kept=0)


def test_cached_dates_are_dropped_before_a_city_is_unloaded(two_cities):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0, days_kept=3)
    alpha, beta = two_cities["alpha"], two_cities["beta"]
    build(pool.use("alpha"), *DATES[:3])
    pool.release()
    build(pool.use("beta"), *DATES[:3])
    pool.release()  # alpha is now the least recently used
    one_date_less = pool.loaded_bytes() - 1.05 * alpha.tt._days[DATES[0]].nbytes
    pool.budget = int(one_date_less) + 1
    pool._enforce()
    assert alpha.cached_days() == DATES[1:3] and beta.cached_days() == DATES[:3]  # one date of alpha, nothing more
    pool.budget = 1  # room for nothing: dates first, down to one a city, then whole cities
    pool._enforce()
    assert alpha.heavy_bytes() == 0 and beta.heavy_bytes() == 0


def test_dates_go_down_to_one_a_city_before_any_city_is_unloaded(two_cities, capsys):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0, days_kept=3)
    alpha, beta = two_cities["alpha"], two_cities["beta"]
    build(pool.use("alpha"), *DATES[:3])
    pool.release()
    build(pool.use("beta"), *DATES[:3])
    pool.release()
    one_date_each = pool.loaded_bytes() - 1.05 * sum(
        c.tt._days[d].nbytes for c in (alpha, beta) for d in DATES[:2])
    pool.budget = int(one_date_each) + 1
    pool._enforce()
    assert alpha.cached_days() == [DATES[2]] and beta.cached_days() == [DATES[2]]
    assert "dropped cached dates of alpha (2), beta (2)" in capsys.readouterr().err


def test_a_pinned_city_keeps_its_dates(two_cities):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0, days_kept=3)
    alpha, beta = two_cities["alpha"], two_cities["beta"]
    build(pool.use("beta"), *DATES[:3])
    pool.release()
    build(pool.use("alpha"), *DATES[:3])  # this call holds alpha
    pool.budget = 1
    pool._enforce()
    assert alpha.cached_days() == DATES[:3]
    assert beta.heavy_bytes() == 0
    pool.release()
    assert alpha.heavy_bytes() == 0


def builds(monkeypatch) -> list:
    """Records (city, date) of each date built."""
    from poland_transit_mcp.timetable import Timetable
    order, orig = [], Timetable._build_day

    def run(self, d):
        order.append((self.city_id, d))
        return orig(self, d)

    monkeypatch.setattr(Timetable, "_build_day", run)
    return order


def test_warm_up_is_breadth_first_from_its_start_date_with_both_directions(two_cities, monkeypatch):
    from poland_transit_mcp.timeutil import add_days
    order = builds(monkeypatch)
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0)
    start = add_days(DATES[0], 1)  # e.g. --warm-from 1: from tomorrow
    load(pool.use("alpha"))  # a query loaded alpha and built DATES[0]
    pool.release()
    pool.warm(["alpha", "beta"], start, days=2)
    assert order == [("alpha", DATES[0]), ("alpha", DATES[1]), ("beta", DATES[1]),
                     ("alpha", DATES[2]), ("beta", DATES[2])]
    for c in two_cities.values():
        assert all(c.tt._days[d].forward and c.tt._days[d].backward for d in DATES[1:3])


def test_warm_up_dates_are_capped_at_days_kept(two_cities, monkeypatch, capsys):
    order = builds(monkeypatch)
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0, days_kept=2)
    pool.warm(["alpha", "beta"], DATES[0], days=5)
    assert sorted({d for _, d in order}) == DATES[:2]
    assert "warming 2" in capsys.readouterr().err


def test_warm_up_stops_before_a_date_would_pass_its_share_of_the_budget(two_cities, monkeypatch, capsys):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0)
    alpha, beta = two_cities["alpha"], two_cities["beta"]
    pool.warm(["alpha", "beta"], DATES[0], days=1, share=1)
    first, last = pool.loaded_bytes(), alpha.last_day_bytes()  # both cities with one date
    for c in (alpha, beta):
        c.unload()
    monkeypatch.setattr(CityPool, "expected_bytes", staticmethod(lambda c, days=1: 1))  # cities: always room
    pool.budget = int(first + 1.05 * last) - 1  # not quite room for alpha's second date, at the first's size
    pool.warm(["alpha", "beta"], DATES[0], days=2, share=1)
    assert alpha.cached_days() == [DATES[0]] and beta.cached_days() == [DATES[0]]
    assert "warm-up stopped before alpha on 2026-11-11" in capsys.readouterr().err
    assert pool.loaded_bytes() <= pool.budget


def test_warm_up_stops_before_a_city_that_would_not_fit(two_cities, capsys):
    pool = CityPool(two_cities, budget_mb=1000, overhead_mb=0)
    pool.budget = CityPool.expected_bytes(two_cities["alpha"]) - 1
    pool.warm(["alpha", "beta"], DATES[0], days=2, share=1)
    assert pool.loaded_bytes() == 0
    assert "warm-up stopped before alpha on 2026-11-10" in capsys.readouterr().err
