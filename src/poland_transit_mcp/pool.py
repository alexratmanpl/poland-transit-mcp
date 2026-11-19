"""Cities kept within a memory budget.

Every configured city stays open (a read-only SQLite handle and its metadata, a few MB). Its heavy
parts, the walking graph and the timetable, load on first use; the timetable keeps up to
`days_kept` dates built (default 2). When their estimated total exceeds the budget, the cached
dates of the least recently used cities go first, down to one date each, then whole cities are
unloaded, least recently used first; the next query for such a city reloads them (about 4 s for
Warszawa, measured; a date about 0.5 s). Tool calls run in worker threads, so a call pins the
cities it uses and pinned cities are never unloaded; their older dates go, after those of unpinned
cities, down to the one most recently used.

The budget is for the whole process. A fixed overhead is set aside first for Python and its
libraries (~70 MB) and for what queries leave behind before it is returned to the OS. Measured on
Warszawa, Katowice and Kraków under back-to-back queries (2026-11-16): ~230 MB in all with glibc's
default allocation, ~150 MB when large frees go straight back to the OS (memory.py, opt-in,
about 25 % slower trip planning). City data gets the rest. Estimates come from what is loaded:
the walking graph's arrays and the timetable's counted structures, plus 5% (they came out 2–9 %
under the measured memory for Warszawa, Katowice, Kraków and Gdańsk). A date's own estimate is
within 2 % of what it holds, with either search (timetable.py, docs/SCALING.md). On macOS the
server returns freed memory by starting itself with MallocLargeCache=0 (memory.py); without that,
macOS keeps freed large blocks and resident memory passes the budget.
"""
from __future__ import annotations

import gc
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from functools import partial

from . import engine, telemetry
from .city import City
from .memory import LARGE_FREES_RETURNED, release_free_memory
from .timeutil import add_days, fmt_date


def log(*a) -> None:
    print("[poland-transit-mcp]", *a, file=sys.stderr, flush=True)


OVERHEAD_MB = 150 if LARGE_FREES_RETURNED else 230
DAY_WAIT_S = 5.0  # the longest a call waits for room before it builds a date anyway (CityPool._reserving_day)


class CityPool:
    def __init__(self, cities: dict[str, City], budget_mb: float, overhead_mb: float = OVERHEAD_MB,
                 days_kept: int = 2, day_wait_s: float = DAY_WAIT_S):
        if days_kept < 1:
            raise ValueError("days_kept must be at least 1")
        self.cities = cities
        self.process_budget_mb = budget_mb
        self.budget = int(max(budget_mb - overhead_mb, 0) * 1e6)  # for city data
        self.days_kept = days_kept  # dates each city's timetable keeps built
        for c in cities.values():
            c.keep_days(days_kept)
            c.reserve_day = partial(self._reserving_day, c)
        self.day_wait_s = day_wait_s
        self._reserved = 0  # expected size of the dates being built
        self._builders = 0  # calls in flight that have built a date (or are building one)
        self._recent: OrderedDict[str, None] = OrderedDict()  # least recently used first
        self._pins: dict[str, int] = {}
        self._lock = threading.RLock()
        self._room = threading.Condition(self._lock)  # notified when a call ends
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

    @contextmanager
    def _reserving_day(self, c: City):
        """Around the build of a date of city c: its expected size (as large as the last date it built, else
        from its file size) is counted while it is built, after making room for it as for a city that loads.

        A date a call holds stays in memory until the call ends, even once the cache has dropped it, so calls
        on several new dates at once would each hold one, past the budget. So when the date does not fit and
        other calls in flight have built dates, this call waits until they end (at most day_wait_s), then
        builds it anyway: the budget stays a target, and one call at a time never waits. It does not wait when
        the budget cannot hold even the cities this call uses, each with one date. A call waits only before
        its first date, so a call that holds one never waits for another that does."""
        last = c.last_day_bytes()
        need = int(1.05 * last) if last is not None else self.expected_bytes(c, 2) - self.expected_bytes(c)
        with self._room:
            pinned = set(self.pinned_here())
            if getattr(self._local, "building", False) or not pinned:  # (not pinned: outside a call)
                self._enforce(incoming=need)
            else:
                floor = sum(self.expected_bytes(self.cities[cid]) for cid in pinned)
                deadline = time.monotonic() + (self.day_wait_s if floor <= self.budget else 0)
                while True:
                    self._enforce(incoming=need)
                    left = deadline - time.monotonic()
                    if not self._builders or left <= 0 or self.loaded_bytes() + self._reserved + need <= self.budget:
                        break
                    self._room.wait(left)
                self._local.building = True
                self._builders += 1
            self._reserved += need
        try:
            yield
        finally:
            with self._room:
                self._reserved -= need

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
    def size_factors(c: City) -> tuple[float, float]:
        """Memory per byte of a city's file: (loaded with one date, each further date), as the pool counts it
        (its estimate plus 5 %), measured 2026-11-18 on data built that day. A city with one date: 1.0-1.3x
        (Warszawa 153-169 MB for a 130 MB file, Kraków 82-88 for 77, Gdańsk 39-43 for 34, the lower with the
        Rust engine). Each further date with the Rust engine: Warszawa 49 MB, Kraków 19, Gdańsk 10, so
        0.25-0.38x; with the Python search 65, 25 and 14, so 0.32-0.5x. The national layer has no map in its file, only timetables: with the
        Rust engine 73 MB with one date for a 19.6 MB file (3.7x) and 32 MB a further date (1.6x); with the
        Python search 103 MB (5.2x) and 61 MB a date (3.1x)."""
        if c.national:
            return (4.0, 1.7) if engine.available() else (5.5, 3.2)
        return (1.3, 0.4) if engine.available() else (1.3, 0.5)

    @classmethod
    def expected_bytes(cls, c: City, days: int = 1) -> int:
        """Memory a city will take once loaded with `days` dates built (size_factors x its file size)."""
        first, more = cls.size_factors(c)
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
            if getattr(self._local, "building", False):
                self._local.building = False
                self._builders -= 1
            self._enforce()
            self._room.notify_all()

    def _enforce(self, incoming: int = 0) -> None:
        """Back within the budget: first the cached dates of unpinned cities (least recently used city
        first, its least recently used date first, down to one date each), then those of pinned cities
        (down to their most recently used date, the one calls in flight are using), then whole unpinned
        cities. A date costs well under a second to build again, a city seconds. A pinned city's dates go
        too because overlapping calls can keep a city pinned for as long as they keep coming: it would
        otherwise grow to `days_kept` dates whatever the budget. A call holding a dropped date finishes on it."""
        incoming += self._reserved
        total = self.loaded_bytes() + incoming
        if total <= self.budget:
            return
        trimmed: dict[str, int] = {}
        for pinned in (False, True):
            for cid in list(self._recent):  # least recently used first
                if total <= self.budget:
                    break
                if (self._pins.get(cid, 0) > 0) != pinned:
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
