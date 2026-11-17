"""Cities kept within a memory budget.

Every configured city stays open (a read-only SQLite handle and its metadata, a few MB). Its heavy
parts, the walking graph and the timetable, load on first use. When their estimated total exceeds
the budget, the least recently used cities drop them again; the next query for such a city reloads
them (about 4 s for Warszawa, measured). Tool calls run in worker threads, so a call pins the
cities it uses and pinned cities are never dropped.

The budget is for the whole process. A fixed overhead is set aside first for Python and its
libraries (~70 MB) and for what queries leave behind before it is returned to the OS. Measured on
Warszawa, Katowice and Kraków under back-to-back queries (2026-11-16): ~230 MB in all with glibc's
default allocation, ~150 MB when large frees go straight back to the OS (memory.py, opt-in,
about 25 % slower trip planning). City data gets the rest. Estimates come from what is loaded:
the walking graph's arrays and the timetable's counted structures, plus 5% (they came out 2–9 %
under the measured memory for Warszawa, Katowice, Kraków and Gdańsk).
"""
from __future__ import annotations

import gc
import sys
import threading
from collections import OrderedDict

from . import telemetry
from .city import City
from .memory import LARGE_FREES_RETURNED, release_free_memory


def log(*a) -> None:
    print("[poland-transit-mcp]", *a, file=sys.stderr, flush=True)


OVERHEAD_MB = 150 if LARGE_FREES_RETURNED else 230


class CityPool:
    def __init__(self, cities: dict[str, City], budget_mb: float, overhead_mb: float = OVERHEAD_MB):
        self.cities = cities
        self.process_budget_mb = budget_mb
        self.budget = int(max(budget_mb - overhead_mb, 0) * 1e6)  # for city data
        self._recent: OrderedDict[str, None] = OrderedDict()  # least recently used first
        self._pins: dict[str, int] = {}
        self._lock = threading.RLock()
        self._local = threading.local()

    # dict-like access to the open cities
    def __contains__(self, cid: str) -> bool:
        return cid in self.cities

    def __iter__(self):
        return iter(self.cities)

    def __len__(self) -> int:
        return len(self.cities)

    def values(self):
        return self.cities.values()

    def loaded_bytes(self) -> int:
        return int(1.05 * sum(c.heavy_bytes() for c in self.cities.values()))

    def use(self, cid: str) -> City:
        """The city for this call: marked most recently used and pinned until release()."""
        with self._lock:
            self._recent.pop(cid, None)
            self._recent[cid] = None
            self._pins[cid] = self._pins.get(cid, 0) + 1
            pinned = getattr(self._local, "pinned", None)
            if pinned is None:
                pinned = self._local.pinned = []
            pinned.append(cid)
            c = self.cities[cid]
            self._enforce(incoming=0 if c.heavy_bytes() else self.expected_bytes(c))  # make room first
        return c

    def budget_to_keep_all_mb(self) -> int:
        """The --memory-budget at which every configured city stays loaded once used: the allowance plus
        each city's expected size, rounded up to 50 MB."""
        need = self.process_budget_mb - self.budget / 1e6 + sum(self.expected_bytes(c) for c in self.cities.values()) / 1e6
        return int(-(-need // 50) * 50)

    @staticmethod
    def expected_bytes(c: City) -> int:
        """Memory a city will take once loaded: ~1.0-1.3x its file size (Warszawa 155 MB for a
        130 MB file, Katowice 149/113, Kraków 79/77, measured with the Python search). The national
        layer has no map in its file, only timetables: 51 MB for a 14.5 MB file (measured), so 4x."""
        return int((4.0 if c.national else 1.3) * c.path.stat().st_size)

    def pinned_here(self) -> list[str]:
        """The cities this thread's current call has used so far."""
        return list(getattr(self._local, "pinned", None) or [])

    def release(self) -> None:
        """Unpins what this thread's call used, then trims back to the budget."""
        with self._lock:
            for cid in getattr(self._local, "pinned", None) or []:
                self._pins[cid] -= 1
            self._local.pinned = []
            self._enforce()

    def _enforce(self, incoming: int = 0) -> None:
        total = self.loaded_bytes() + incoming
        if total <= self.budget:
            return
        dropped = []
        for cid in list(self._recent):  # oldest first
            if total <= self.budget:
                break
            c = self.cities[cid]
            if self._pins.get(cid, 0) > 0 or not c.heavy_bytes():
                continue
            freed = int(1.05 * c.heavy_bytes())
            total -= freed
            c.unload()
            del self._recent[cid]
            dropped.append(cid)
            telemetry.unloads.add(1, {"poland_transit.city": cid})
            telemetry.trace.get_current_span().add_event("city.unload", {
                "poland_transit.city": cid, "poland_transit.bytes": freed,
                "poland_transit.memory.budget": self.budget})
        if dropped:
            gc.collect()
            release_free_memory()
            log(f"memory budget {self.process_budget_mb:g} MB: unloaded {', '.join(dropped)} "
                f"(city data now ~{total // 1_000_000} MB of {self.budget // 1_000_000} MB)")

    def warm(self, ids: list[str], date: int, share: float = 0.7) -> None:
        """Preloads cities in order while the next one, at its expected size, still fits in a share of
        the budget. Warming never unloads a city: queries may already be using them."""
        with telemetry.span("warm_up", **{"poland_transit.cities": len(ids)}):
            self._warm(ids, date, share)
        release_free_memory()

    def _warm(self, ids: list[str], date: int, share: float) -> None:
        for cid in ids:
            c = self.cities[cid]
            if c.heavy_bytes():
                continue  # already loaded by a query
            if self.loaded_bytes() + self.expected_bytes(c) > self.budget * share:
                log(f"warm-up stopped before {cid}: memory budget {self.process_budget_mb:g} MB")
                break
            c = self.use(cid)
            try:
                if not c.national:  # the national layer has no walking graph
                    c.graph
                if c.has_transit:
                    day = c.tt.day(date)
                    c.tt.raptor(day, False)
                    c.tt.raptor(day, True)
            except Exception as e:  # noqa: BLE001
                log(f"warm-up of {cid} failed: {e}")
            finally:
                self.release()
