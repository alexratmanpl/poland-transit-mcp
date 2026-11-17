"""On-demand loading within a memory budget (pool.py), on two copies of the synthetic city."""
import shutil
import sqlite3
import threading

import pytest

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
    each = CityPool.expected_bytes(two_cities["alpha"]) / 1e6
    assert pool.budget_to_keep_all_mb() == int(-(-(230 + 2 * each) // 50) * 50)
    assert pool.budget_to_keep_all_mb() % 50 == 0
