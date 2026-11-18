"""Cities kept within a memory budget.

Every configured city stays open (a read-only SQLite handle and its metadata, a few MB). Its heavy
parts, the walking graph and the timetable, load on first use; the timetable keeps up to
`days_kept` dates built (default 2). When their estimated total exceeds the budget, the cached
dates of the least recently used cities go first, down to one date each, then whole cities are
unloaded, least recently used first; the next query for such a city reloads them (about 4 s for
Warszawa, measured; a date about 0.5 s). Tool calls run in worker threads, so a call pins the
cities it uses and pinned cities are never touched.

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
from .timeutil import add_days, fmt_date


def log(*a) -> None:
    print("[poland-transit-mcp]", *a, file=sys.stderr, flush=True)


OVERHEAD_MB = 150 if LARGE_FREES_RETURNED else 230


class CityPool:
    def __init__(self, cities: dict[str, City], budget_mb: float, overhead_mb: float = OVERHEAD_MB,
                 days_kept: int = 2):
        if days_kept < 1:
            raise ValueError("days_kept must be at least 1")
        self.cities = cities
        self.process_budget_mb = budget_mb
        self.budget = int(max(budget_mb - overhead_mb, 0) * 1e6)  # for city data
        self.days_kept = days_kept  # dates each city's timetable keeps built
        for c in cities.values():
            c.keep_days(days_kept)
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
        """The --memory-budget at which every configured city stays loaded once used, with `days_kept`
        dates each: the allowance plus each city's expected size, rounded up to 50 MB."""
        need = self.process_budget_mb - self.budget / 1e6 + sum(
            self.expected_bytes(c, self.days_kept) for c in self.cities.values()) / 1e6
        return int(-(-need // 50) * 50)

    @staticmethod
    def expected_bytes(c: City, days: int = 1) -> int:
        """Memory a city will take once loaded with `days` dates built. With one date: ~1.0-1.3x its file
        size (Warszawa 155 MB for a 130 MB file, Katowice 149/113, Kraków 79/77, measured with the Python
        search); each further date ~0.4x (Warszawa about 55 MB a date, Kraków 20, Gdańsk 12 for a 34 MB
        file, measured with the Rust engine). The national layer has no map in its file, only timetables:
        51 MB for a 14.5 MB file with one date (measured), so 4x, and about 50 MB a date for a 19.6 MB
        file, so 2.5x for each further date."""
        first, more = (4.0, 2.5) if c.national else (1.3, 0.4)
        return int((first + more * (max(days, 1) - 1)) * c.path.stat().st_size)

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
        """Back within the budget: first the cached dates of unpinned cities (least recently used city
        first, its least recently used date first, down to one date each), then whole unpinned cities.
        A date costs well under a second to build again, a city seconds."""
        total = self.loaded_bytes() + incoming
        if total <= self.budget:
            return
        trimmed: dict[str, int] = {}
        for cid in list(self._recent):  # least recently used first
            if total <= self.budget:
                break
            if self._pins.get(cid, 0) > 0:
                continue
            c = self.cities[cid]
            while total > self.budget and c.drop_oldest_day() is not None:
                trimmed[cid] = trimmed.get(cid, 0) + 1
                total = self.loaded_bytes() + incoming
        dropped = []
        for cid in list(self._recent):  # least recently used first
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
        if trimmed or dropped:
            gc.collect()
            release_free_memory()
            done = []
            if trimmed:
                done.append("dropped cached dates of " + ", ".join(f"{cid} ({n})" for cid, n in trimmed.items()))
            if dropped:
                done.append(f"unloaded {', '.join(dropped)}")
            log(f"memory budget {self.process_budget_mb:g} MB: {'; '.join(done)} "
                f"(city data now ~{total // 1_000_000} MB of {self.budget // 1_000_000} MB)")

    def warm(self, ids: list[str], start: int, days: int = 1, share: float = 0.7) -> None:
        """Builds `days` dates from `start`, each with both routing directions, breadth-first: every
        city's first date, then every city's second, and so on, while the next date, at its estimated
        size, still fits in a share of the budget (so warming is not meant to drop a date or unload a
        city: queries may already be using them). Runs once: the dates are those of the start-up."""
        if days > self.days_kept:
            log(f"warm-up: {days} dates asked for, but each city keeps {self.days_kept}; warming {self.days_kept}")
            days = self.days_kept
        with telemetry.span("warm_up", **{"poland_transit.cities": len(ids), "poland_transit.days": days}):
            self._warm(ids, start, days, share)
        release_free_memory()

    def _warm(self, ids: list[str], start: int, days: int, share: float) -> None:
        failed: set[str] = set()
        for k in range(days):
            date = add_days(start, k)
            for cid in ids:
                c = self.cities[cid]
                if cid in failed or (k and not c.has_transit):
                    continue  # without timetables, only the walking graph is warmed, with the first date
                if not c.heavy_bytes():  # not loaded (yet, or again): graph, timetable and one date
                    need = self.expected_bytes(c)
                elif not c.has_transit or date in c.cached_days():
                    need = 0
                else:  # one more date, about the size of the last one it built
                    last = c.last_day_bytes()
                    need = (int(1.05 * last) if last is not None else
                            self.expected_bytes(c, 2) - self.expected_bytes(c))
                if need and self.loaded_bytes() + need > self.budget * share:
                    log(f"warm-up stopped before {cid} on {fmt_date(date)}: memory budget {self.process_budget_mb:g} MB")
                    return
                c = self.use(cid)
                try:
                    if not c.national:  # the national layer has no walking graph
                        c.graph
                    if c.has_transit:
                        day = c.tt.day(date)
                        c.tt.raptor(day, False)
                        c.tt.raptor(day, True)
                except Exception as e:  # noqa: BLE001
                    failed.add(cid)
                    log(f"warm-up of {cid} failed: {e}")
                finally:
                    self.release()
