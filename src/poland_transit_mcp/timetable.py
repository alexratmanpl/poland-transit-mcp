"""GTFS timetable access: services per date, a per-day trip index (lazy, cached) and the
RAPTOR-ready arrays built from it."""
from __future__ import annotations

import math
import threading
import weakref
from array import array
from bisect import bisect_left
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import nullcontext
from dataclasses import dataclass, field

import numpy as np

from . import engine, telemetry
from .geo import LatLon, decode_parts
from .shared_db import SharedConnection
from .text import fold
from .timeutil import add_days, weekday

NO_PICKUP = 1
NO_DROPOFF = 2
DAY = 86400
NEXT_DAY_HORIZON = 6 * 3600  # next-day trips starting before this are part of a day's window


@dataclass(slots=True)
class Route:
    idx: int
    feed: str
    id: str
    short: str
    long: str | None
    mode: str
    color: str | None
    text_color: str | None
    agency: str | None = None  # operator as named in the feed's agency.txt


@dataclass(slots=True)
class Pattern:
    idx: int
    route: Route
    headsign: str
    dir: int
    shape: int | None
    stops: tuple[int, ...]
    flags: bytes


@dataclass(slots=True)
class Stop:
    idx: int
    id: str
    code: str | None
    name: str
    platform: str | None
    lat: float
    lon: float
    node: int | None
    snap: float


@dataclass(slots=True)
class DayTrip:
    trip_idx: int
    trip_id: str
    pattern: Pattern
    service_date: int  # the service day this trip belongs to
    shift: int  # added to GTFS times to put them on the window's clock (-86400, 0, +86400)
    arr: array
    dep: array
    headway: int = 0  # seconds; > 0 when the feed only says "every N s" (times are estimated)


def headway_fields(headway: int, key: str) -> dict:
    """Extra answer fields for a run whose feed gives only a frequency, not exact times (e.g. Warsaw metro)."""
    if not headway:
        return {}
    return {key: "frequency-based", "frequency": f"every {round(headway / 60, 1):g} min",
            "note": "The timetable only gives a frequency for this line; this time is estimated from it."}


@dataclass(slots=True)
class RPattern:
    """A FIFO group of trips of one pattern, laid out by stop position for RAPTOR."""
    pattern: Pattern
    stops: list[int]
    pick: bytes  # 1 = boarding allowed at that position
    drop: bytes  # 1 = alighting allowed
    n_trips: int
    arr_cols: list[array]  # per position: arrival of each trip (sorted, FIFO)
    dep_cols: list[array]
    trips: list[DayTrip]
    reversed: bool


@dataclass(slots=True)
class RaptorData:
    n_stops: int
    patterns: list[RPattern]
    stop_routes: list[list[tuple[int, int]]]  # stop → [(pattern index, position)]
    engine: object = None  # the Rust engine's copy of these tables, when it is installed (engine.py)
    engine_transfers: object = None
    backward: bool = False


@dataclass
class Day:
    date: int
    trips: list[DayTrip]
    by_pattern: dict[int, list[DayTrip]]
    forward: RaptorData | None = None
    backward: RaptorData | None = None
    nbytes: int = 0  # estimated memory of the trips and of each routing table built (Timetable.approx_bytes)
    # one lock per direction (forward, backward): calls arriving together build each routing table once
    build_locks: tuple = field(default_factory=lambda: (threading.Lock(), threading.Lock()), repr=False, compare=False)


def trips_bytes(trips: list[DayTrip]) -> int:
    """Estimated memory of a day's trips: per trip ~200 B of objects (the trip, its index, its slots in the day's
    lists) plus its id, and each time array (~100 B and 4 B a stop). Within 6 % of tracemalloc on Warszawa,
    Kraków, Gdańsk and the national layer (docs/SCALING.md)."""
    return sum(200 + len(t.trip_id) + (100 + 4 * len(t.dep)) * (1 if t.arr is t.dep else 2) for t in trips)


def raptor_bytes(rd: RaptorData) -> int:
    """Estimated memory of one routing direction, from what it holds: per stop pattern ~400 B of objects, 14 B a
    stop position (its stop and flags) and 8 B a trip (its list); per stop a list of (pattern, position) pairs,
    72 B a pair; and the times: in the engine (its own count), or for the Python search two int32 columns per
    stop position (~80 B each, plus 16 B for their slots). Within 5 % of tracemalloc plus the engine's count on
    Warszawa, Kraków, Gdańsk and the national layer, with either search (docs/SCALING.md)."""
    n = 56 * len(rd.stop_routes) + 72 * sum(len(x) for x in rd.stop_routes)
    for rp in rd.patterns:
        k = len(rp.stops)
        n += 400 + 14 * k + 8 * rp.n_trips
        if rd.engine is None:
            n += k * (2 * 80 + 16 + 8 * rp.n_trips)
    return n + (rd.engine.nbytes() if rd.engine is not None else 0)


@dataclass
class Timetable:
    db: SharedConnection  # shared by the threads answering calls, like everything here
    city_id: str = ""  # for telemetry only
    routes: dict[int, Route] = field(default_factory=dict)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    stops: list[Stop | None] = field(default_factory=list)
    stop_by_id: dict[str, Stop] = field(default_factory=dict)
    stop_patterns: dict[int, list[tuple[Pattern, int]]] = field(default_factory=dict)
    transfers: list[list[tuple[int, int]]] = field(default_factory=list)  # stop → [(to stop, walk secs)]
    days_kept: int = 2  # dates kept in memory, least recently used dropped first (pool.py sets it)
    _days: OrderedDict = field(default_factory=OrderedDict)  # date → Day, least recently used first
    _building: dict = field(default_factory=dict)  # date → Future of a Day another thread is building
    # every Day still in memory: the cached ones and those that calls in flight hold after the cache dropped them
    _alive: weakref.WeakValueDictionary = field(default_factory=weakref.WeakValueDictionary, repr=False, compare=False)
    # set by the memory pool (pool.py): a context manager around each date build that makes room for it first
    # (and counts it while it is built)
    reserve_day: object = field(default=None, repr=False, compare=False)
    _shapes: dict = field(default_factory=dict)
    _headway_col: bool = False
    _base_bytes: int = 0  # estimated memory of the structures loaded with the timetable (approx_bytes)
    _by_name: dict = field(default_factory=dict)  # folded stop name → its stops, by index (stops_named)
    _name_keys: list = field(default_factory=list)  # the folded names, sorted (prefix lookups)
    _engine_transfers: object = None  # foot transfers in the Rust engine, built once per city
    has_trip_details: bool = False  # rail feeds: train names, categories, platforms (trip_info table)
    _days_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _transfers_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        db = self.db
        self._headway_col = "headway" in {c[1] for c in db.execute("PRAGMA table_info(trips)")}
        self.has_trip_details = bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'trip_info'").fetchone()) and bool(
            db.execute("SELECT 1 FROM trip_info LIMIT 1").fetchone())
        cols = {c[1] for c in db.execute("PRAGMA table_info(routes)")}
        agency = "agency" if "agency" in cols else "NULL"  # city files built before operators were stored
        for r in db.execute(f"SELECT route_idx, feed, route_id, short_name, long_name, mode, color, text_color, {agency} FROM routes"):
            self.routes[r[0]] = Route(*r)
        for idx, ridx, head, d, shape, stops, flags in db.execute(
                "SELECT pattern_idx, route_idx, headsign, direction_id, shape_idx, stops, flags FROM patterns"):
            self.patterns[idx] = Pattern(idx, self.routes[ridx], head or "", d, shape,
                                         tuple(np.frombuffer(stops, "<i4").tolist()), bytes(flags))
        rows = db.execute("SELECT stop_idx, stop_id, code, name, platform, lat, lon, node, snap_m FROM stops").fetchall()
        max_stop = max((r[0] for r in rows), default=0)
        self.stops = [None] * (max_stop + 1)
        for r in rows:
            s = Stop(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], float(r[8] or 0))
            self.stops[s.idx] = s
            self.stop_by_id[s.id] = s
        for s in self.stops:  # in index order, so each name's stops come out in it
            if s is not None:
                self._by_name.setdefault(fold(s.name), []).append(s)
        self._name_keys = sorted(self._by_name)
        for p in self.patterns.values():
            for pos, s in enumerate(p.stops):
                self.stop_patterns.setdefault(s, []).append((p, pos))
        self.transfers = [[] for _ in range(max_stop + 1)]
        for a, b, w in db.execute("SELECT from_stop, to_stop, walk_s FROM transfers"):
            self.transfers[a].append((b, w))
        self._base_bytes = 1900 * (len(self.stops) + len(self.patterns))

    @property
    def n_stops(self) -> int:
        return len(self.stops)

    def stops_named(self, folded: str) -> list[Stop]:
        """The stops whose folded name (text.fold) is `folded`, by index."""
        return list(self._by_name.get(folded, ()))

    def stops_named_from(self, prefix: str) -> list[Stop]:
        """The stops whose folded name starts with `prefix`, by index: a range of the sorted names, not a scan."""
        keys = self._name_keys
        out: list[Stop] = []
        for i in range(bisect_left(keys, prefix), len(keys)):
            if not keys[i].startswith(prefix):
                break
            out.extend(self._by_name[keys[i]])
        out.sort(key=lambda s: s.idx)
        return out

    def names_containing(self, part: str) -> set[str]:
        """The stop names (as stored) whose folded name contains `part`: one pass over the distinct names."""
        return {s.name for k in self._name_keys if part in k for s in self._by_name[k]}

    def first_stops_containing(self, part: str) -> dict[str, tuple[str, Stop]]:
        """Each stop name (as stored) whose folded name contains `part`: {name: (folded name, its first stop by
        index)}. One pass over the distinct folded names, none folded again (find_places on a national layer)."""
        out: dict[str, tuple[str, Stop]] = {}
        for k in self._name_keys:
            if part in k:
                for s in self._by_name[k]:  # by index: the first stop of each name is kept
                    out.setdefault(s.name, (k, s))
        return out

    def approx_bytes(self) -> int:
        """Estimated memory, counted from what is loaded: ~1.9 KB per stop and per stop pattern for the
        base structures, the engine's foot transfers, and each date's own size (Day.nbytes: its trips and the
        routing tables built for it, counted once when each is built). A date counts while it is in memory:
        cached, or dropped from the cache but still held by a call in flight."""
        transfers = self._engine_transfers
        with self._days_lock:
            days = sum(day.nbytes for day in self._alive.values())
        return self._base_bytes + days + (transfers.nbytes() if transfers is not None else 0)

    def days_cached(self) -> list[int]:
        """The cached dates, least recently used first."""
        with self._days_lock:
            return list(self._days)

    def last_day_bytes(self) -> int | None:
        """Estimated size of the most recently used cached date, or None with none cached."""
        with self._days_lock:
            return next(reversed(self._days.values())).nbytes if self._days else None

    def drop_oldest_day(self, keep: int = 1) -> int | None:
        """Drops the least recently used cached date while more than `keep` are cached; returns its
        estimated size, or None when nothing was dropped. A call still holding that Day finishes on it."""
        with self._days_lock:
            if len(self._days) <= keep:
                return None
            return self._days.popitem(last=False)[1].nbytes

    def services(self, d: int) -> set[int]:
        """Service indexes active on a date (calendar + calendar_dates exceptions)."""
        bit = 1 << weekday(d)
        out = {r[0] for r in self.db.execute(
            "SELECT service_idx FROM calendar WHERE start_date <= ? AND end_date >= ? AND (days & ?) != 0", (d, d, bit))}
        for sidx, ex in self.db.execute("SELECT service_idx, exception_type FROM calendar_dates WHERE date = ?", (d,)):
            if ex == 1:
                out.add(sidx)
            else:
                out.discard(sidx)
        return out

    def _load_trips(self, d: int, shift: int, keep, out: list[DayTrip]) -> None:
        svc = list(self.services(d))
        if not svc:
            return
        hw = "headway" if self._headway_col else "NULL"
        q = f"SELECT trip_idx, pattern_idx, trip_id, times, {hw} FROM trips WHERE service_idx IN ({','.join('?' * len(svc))})"
        for tidx, pidx, tid, blob, headway in self.db.execute(q, svc):
            p = self.patterns[pidx]
            n = len(p.stops)
            t = np.frombuffer(blob, "<i4")
            if len(t) == 2 * n:
                a_np, d_np = t[0::2] + shift, t[1::2] + shift
            else:
                a_np = d_np = t + shift
            if not keep(int(d_np[0]), int(a_np[-1])):
                continue
            dep = array("i", d_np.astype("<i4").tobytes())  # compact int32 arrays, not lists of Python ints
            arr = dep if a_np is d_np else array("i", a_np.astype("<i4").tobytes())
            out.append(DayTrip(tidx, tid, p, d, shift, arr, dep, headway or 0))

    def day(self, d: int) -> Day:
        """Trips relevant to date d on its clock: its own service, the previous day's trips running
        past midnight (GTFS times > 24:00) and the next day's early-morning trips.

        Built once: a call asking for a date that another call is building waits for that build (and
        gets its error, if it fails). The build runs outside the cache's lock, so other dates are not held up,
        after the memory pool has made room for it (reserve_day, which may wait for other calls building dates
        to finish; it waits before this date is marked as being built, so no call waits for a waiting one)."""
        with self._days_lock:
            day = self._cached(d)
            pending = self._building.get(d) if day is None else None
        if day is not None:
            return day
        if pending is not None:
            return pending.result()  # the Day itself: it may already have been dropped from the cache
        with self.reserve_day() if self.reserve_day else nullcontext():
            with self._days_lock:
                day = self._cached(d)  # built by another call while this one waited for room
                pending = self._building.get(d) if day is None else None
                building = day is None and pending is None
                if building:
                    pending = self._building[d] = Future()
            if day is not None:
                return day
            if not building:
                return pending.result()
            try:
                day = self._build_day(d)
            except BaseException as e:
                with self._days_lock:
                    del self._building[d]
                pending.set_exception(e)
                raise
            with self._days_lock:
                self._days[d] = day
                self._alive[id(day)] = day
                del self._building[d]
                while len(self._days) > max(self.days_kept, 1):
                    self._days.popitem(last=False)
        pending.set_result(day)
        return day

    def _cached(self, d: int) -> Day | None:
        """The cached Day of date d, marked most recently used (call with _days_lock held)."""
        day = self._days.get(d)
        if day is not None:
            self._days.move_to_end(d)
        return day

    def _build_day(self, d: int) -> Day:
        with telemetry.city_load(self.city_id, "day") as span:
            trips: list[DayTrip] = []
            self._load_trips(add_days(d, -1), -DAY, lambda first, last: last >= 0, trips)
            self._load_trips(d, 0, lambda first, last: True, trips)
            self._load_trips(add_days(d, 1), DAY, lambda first, last: first < DAY + NEXT_DAY_HORIZON, trips)
            by_pattern: dict[int, list[DayTrip]] = {}
            for t in trips:
                by_pattern.setdefault(t.pattern.idx, []).append(t)
            for lst in by_pattern.values():
                lst.sort(key=lambda t: t.dep[0])
            span.set_attribute("poland_transit.trips", len(trips))
        return Day(d, trips, by_pattern, nbytes=trips_bytes(trips))

    def raptor(self, day: Day, backward: bool) -> RaptorData:
        """The day's routing table in one direction, built once: a call arriving while another builds
        the same direction waits for it (the other direction has its own lock)."""
        side = "backward" if backward else "forward"
        rd = getattr(day, side)
        if rd is not None:
            return rd
        with day.build_locks[backward]:
            rd = getattr(day, side)
            if rd is None:
                with telemetry.city_load(self.city_id, "routing_tables") as span:
                    rd = self._build_raptor(day, backward)
                    span.set_attributes({"poland_transit.direction": side, "poland_transit.patterns": len(rd.patterns)})
                size = raptor_bytes(rd)
                with self._days_lock:  # with the size, so approx_bytes never counts one without the other
                    setattr(day, side, rd)
                    day.nbytes += size
        return rd

    def _build_raptor(self, day: Day, backward: bool) -> RaptorData:
        out: list[RPattern] = []
        # with the Rust engine, times go straight into one flat array per direction (engine.py);
        # the Python search instead keeps one small array per stop position
        use_engine = engine.available()
        flat_arr, flat_dep, time_off = array("i"), array("i"), [0]
        for trips in day.by_pattern.values():
            p = trips[0].pattern
            n = len(p.stops)
            groups: list[list[DayTrip]] = []  # FIFO groups: no overtaking inside a group
            for t in trips:
                for g in groups:
                    last = g[-1]
                    if all(t.dep[k] >= last.dep[k] and t.arr[k] >= last.arr[k] for k in range(n)):
                        g.append(t)
                        break
                else:
                    groups.append([t])
            for g in groups:
                order = g[::-1] if backward else g
                src = range(n - 1, -1, -1) if backward else range(n)
                stops, pick, drop, arr_cols, dep_cols = [], bytearray(), bytearray(), [], []
                for k in src:
                    stops.append(p.stops[k])
                    f = p.flags[k]
                    if backward:  # boarding in reversed time = alighting in real time
                        pick.append(0 if f & NO_DROPOFF else 1)
                        drop.append(0 if f & NO_PICKUP else 1)
                        a_col, d_col = [-t.dep[k] for t in order], [-t.arr[k] for t in order]
                    else:
                        pick.append(0 if f & NO_PICKUP else 1)
                        drop.append(0 if f & NO_DROPOFF else 1)
                        a_col, d_col = [t.arr[k] for t in order], [t.dep[k] for t in order]
                    if use_engine:
                        flat_arr.extend(a_col)
                        flat_dep.extend(d_col)
                    else:
                        arr_cols.append(array("i", a_col))
                        dep_cols.append(array("i", d_col))
                time_off.append(len(flat_arr))
                out.append(RPattern(p, stops, bytes(pick), bytes(drop), len(order), arr_cols, dep_cols, order, backward))
        stop_routes: list[list[tuple[int, int]]] = [[] for _ in range(self.n_stops)]
        for i, rp in enumerate(out):
            for pos, s in enumerate(rp.stops):
                stop_routes[s].append((i, pos))
        rd = RaptorData(self.n_stops, out, stop_routes, backward=backward)
        if use_engine:
            with self._transfers_lock:  # both directions may be built at once: one copy per city
                if self._engine_transfers is None:
                    self._engine_transfers = engine.transfers(self.transfers, self.n_stops)
            rd.engine = engine.tables(self.n_stops, out, stop_routes, flat_arr, flat_dep, time_off)
            rd.engine_transfers = self._engine_transfers
        return rd

    def trip_details(self, trip_idx: int) -> dict | None:
        """A train's name and number as published, its category, and (platform, track) per stop position,
        for rail feeds; None for other trips. Read on demand: only legs and departures being shown need it."""
        if not self.has_trip_details:
            return None
        row = self.db.execute("SELECT train, category, platforms FROM trip_info WHERE trip_idx = ?", (trip_idx,)).fetchone()
        if row is None:
            return None
        where = [tuple(w.split("/", 1)) if "/" in w else (w or "", "") for w in (row[2] or "").split("|")] if row[2] else []
        return {"train": row[0], "category": row[1], "where": where}

    def lines_at(self, stop_idx: int, d: int) -> list[Route]:
        """Routes serving a stop on a date (from that date's own trips)."""
        day = self.day(d)
        seen: dict[int, Route] = {}
        for p, _ in self.stop_patterns.get(stop_idx, []):
            if p.route.idx not in seen and any(t.service_date == d for t in day.by_pattern.get(p.idx, [])):
                seen[p.route.idx] = p.route
        return sorted(seen.values(), key=lambda r: (len(r.short), r.short))

    def shape(self, idx: int) -> list[LatLon]:
        coords = self._shapes.get(idx)  # a local: another thread may clear the cache meanwhile
        if coords is None:
            row = self.db.execute("SELECT coords FROM shapes WHERE shape_idx = ?", (idx,)).fetchone()
            if len(self._shapes) > 500:
                self._shapes.clear()
            coords = self._shapes[idx] = decode_parts(row[0])[0] if row else []
        return coords

    def _stop_positions(self, p: Pattern) -> list[tuple[int, float, LatLon]] | None:
        """Places every stop of a pattern on its shape, in order: (segment, fraction, point) per stop.

        Each stop is projected onto the shape's segments; a dynamic programme then picks one candidate
        per stop so that positions never go backwards along the shape and the total stop-to-shape
        distance is smallest. Nearest-point matching alone fails where a shape passes a stop twice
        (e.g. a bus leaving a terminus, looping, and coming back past the next platform)."""
        key = ("pos", p.idx)
        try:
            return self._shapes[key]
        except KeyError:  # not placed yet, or another thread cleared the cache
            pass
        shape = self.shape(p.shape) if p.shape else []
        result = None
        if len(shape) >= 2:
            lat0 = shape[0][0]
            kx, ky = 111320 * math.cos(math.radians(lat0)), 110574.0
            xs = [q[1] * kx for q in shape]
            ys = [q[0] * ky for q in shape]
            cands: list[list[tuple[float, float, int, float]]] = []  # per stop: (along, dist, seg, frac)
            for sidx in p.stops:
                st = self.stops[sidx]
                sx, sy = st.lon * kx, st.lat * ky
                opts = []
                for i in range(len(shape) - 1):
                    dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
                    l2 = dx * dx + dy * dy
                    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((sx - xs[i]) * dx + (sy - ys[i]) * dy) / l2))
                    d = math.hypot(xs[i] + t * dx - sx, ys[i] + t * dy - sy)
                    opts.append((i + t, d, i, t))
                best = min(o[1] for o in opts)
                opts = sorted((o for o in opts if o[1] <= max(150.0, 2 * best + 30)), key=lambda o: o[1])[:12]
                cands.append(sorted(opts))
            # DP over candidates, non-decreasing along the shape
            cost = [[c[1] for c in cands[0]]]
            back: list[list[int]] = [[-1] * len(cands[0])]
            for k in range(1, len(cands)):
                row, brow = [], []
                for c in cands[k]:
                    prev = [(cost[-1][j], j) for j, pc in enumerate(cands[k - 1]) if pc[0] <= c[0] + 1e-9]
                    if prev:
                        v, j = min(prev)
                        row.append(v + c[1])
                        brow.append(j)
                    else:
                        row.append(math.inf)
                        brow.append(-1)
                cost.append(row)
                back.append(brow)
            j = min(range(len(cost[-1])), key=lambda i: cost[-1][i])
            if math.isfinite(cost[-1][j]) and cost[-1][j] / len(cands) < 150:
                picks = [0] * len(cands)
                for k in range(len(cands) - 1, -1, -1):
                    picks[k] = j
                    j = back[k][j]
                result = []
                for k, ci in enumerate(picks):
                    _, _, seg, t = cands[k][ci]
                    a, b = shape[seg], shape[seg + 1]
                    result.append((seg, t, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))))
        if len(self._shapes) > 1000:
            self._shapes.clear()
        self._shapes[key] = result
        return result

    def leg_path(self, p: Pattern, a: int, b: int) -> list[LatLon]:
        """Geometry between two stop positions: the GTFS shape between them, else straight stop-to-stop."""
        pts = [(self.stops[s].lat, self.stops[s].lon) for s in p.stops[a:b + 1]]
        pos = self._stop_positions(p)
        if not pos:
            return pts
        shape = self.shape(p.shape)
        (sa, _, qa), (sb, _, qb) = pos[a], pos[b]
        return [pts[0], qa, *shape[sa + 1:sb + 1], qb, pts[-1]]


def route_color(r: Route) -> str:
    """GTFS route colour; white or missing falls back to a mode default (e.g. Kraków buses are all FFFFFF)."""
    c = (r.color or "").upper()
    if len(c) == 6 and all(ch in "0123456789ABCDEF" for ch in c) and c not in ("FFFFFF", "FEFEFE", "FAFAFA"):
        return "#" + c
    return {"tram": "#D7263D", "rail": "#555555"}.get(r.mode, "#1F5FAD")
