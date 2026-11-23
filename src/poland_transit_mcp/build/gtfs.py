"""GTFS → SQLite import (stop patterns + packed stop times per trip) and foot-transfer computation.
A feed can be a zip file or an unzipped directory (tests use synthetic directories)."""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

import numpy as np

from ..config import Feed
from ..geo import RAD, R, encode_parts, simplify
from ..text import fold
from ..timeutil import parse_gtfs_time, same_clock
from ..walk import WALK_SPEED_MPS, WalkGraph

Log = Callable[..., None]
DAY = 86400
WARSAW = ZoneInfo("Europe/Warsaw")  # every data set keeps Polish time (build_city.clock_problems checks it)


def mode_of(route_type: int) -> str:
    t = route_type
    if t == 0 or 900 <= t < 1000:
        return "tram"
    if t == 3 or 700 <= t < 800 or t == 200:
        return "bus"
    if t == 11 or t == 800:
        return "trolleybus"
    if t == 1 or 400 <= t < 500:
        return "metro"
    if t == 2 or 100 <= t < 200:
        return "rail"
    if t in (4, 1000, 1200):
        return "ferry"
    return "other"


MAX_LINE = 4 << 20  # bytes in one line of a feed file (csv already refuses a field over 131,072 characters)
MAX_FILE = 8 << 30  # bytes one feed file may unpack to


class FeedTooLarge(ValueError):
    """A feed file with a line or a size no real feed has (a zip bomb, say): the feed is not imported."""


class _Capped(io.RawIOBase):
    """A feed file's bytes, refused past MAX_LINE in one line or MAX_FILE in all. Without this, one endless line
    is read into memory whole before the csv module sees any of it."""

    def __init__(self, raw, name: str):
        self.raw, self.name, self.total, self.run = raw, name, 0, 0  # run: bytes since the last line end

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self.raw.read(min(len(b), 1 << 16))
        n = len(data)
        b[:n] = data
        self.total += n
        if self.total > MAX_FILE:
            raise FeedTooLarge(f"{self.name} unpacks to more than {MAX_FILE >> 30} GB")
        ends = [i for i in (data.find(b"\n"), data.find(b"\r")) if i >= 0]
        if ends:
            first, last = min(ends), max(data.rfind(b"\n"), data.rfind(b"\r"))
            longest = self.run + first
            self.run = n - last - 1
        else:
            longest = self.run = self.run + n
        if max(longest, self.run) > MAX_LINE:
            raise FeedTooLarge(f"{self.name} has a line longer than {MAX_LINE >> 20} MB")
        return n


class Source:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.zip = None if self.path.is_dir() else zipfile.ZipFile(self.path)

    def has(self, name: str) -> bool:
        return (self.path / name).exists() if self.zip is None else name in self.zip.namelist()

    @contextmanager
    def rows(self, name: str) -> Iterator[Iterator[dict[str, str]]]:
        """Streams a CSV file as dicts (BOM, CRLF and quoted fields handled by the csv module)."""
        if not self.has(name):
            yield iter(())
            return
        if self.zip is not None and self.zip.getinfo(name).file_size > MAX_FILE:  # as the zip states it
            raise FeedTooLarge(f"{name} unpacks to more than {MAX_FILE >> 30} GB")
        raw = open(self.path / name, "rb") if self.zip is None else self.zip.open(name)
        with raw, io.TextIOWrapper(io.BufferedReader(_Capped(raw, name)), encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = [h.strip() for h in next(reader, [])]
            yield (dict(zip(header, map(str.strip, row))) for row in reader if row)


_ATTRIBUTION_ROLES = (("is_producer", "GTFS producer"), ("is_operator", "operator"),
                      ("is_authority", "authority"), ("is_data_source", "data source"))


TRAILING_NUMBER = re.compile(r"^(.*\S)\s+(\d{2})$")
ALL_NUMBERED = 0.9  # a feed whose stop names nearly all end in a two-digit post number (ZTM Gdańsk)


def platform_splitter(names: list[str]):
    """How a feed's names carry platforms: name -> re.Match (name, platform) or None.

    In a feed whose names nearly all end in a two-digit number ("Oliwa PKP 02", "Pokładowa 91"), that number is the
    platform. Elsewhere a trailing number is only a platform when it is zero-padded ("PL. JANA PAWŁA II 01") or when
    three or more stops share the name with different numbers ("GALERIA DOMINIKAŃSKA 10"); otherwise it belongs to the
    name, as a house number ("Bierutowska 65", "Opatowicka nr 59") or a school number ("Szkoła Podstawowa nr 11")."""
    tails = [m for m in map(TRAILING_NUMBER.match, names) if m]
    if names and len(tails) / len(names) >= ALL_NUMBERED:
        return TRAILING_NUMBER.match
    variants: dict[str, set[str]] = {}
    for m in tails:
        variants.setdefault(m[1], set()).add(m[2])

    # decided per name, so all numbers of one name split together ("X 01" and "X 12"), or none of them
    platform_names = {base for base, nums in variants.items()
                      if not re.search(r"\bnr\.?$", base, re.IGNORECASE)
                      and (any(n.startswith("0") for n in nums) or len(nums) >= 3)}

    def split(name: str):
        m = TRAILING_NUMBER.match(name)
        return m if m and m[1] in platform_names else None
    return split


def feed_credits(feed: Feed, src: Source) -> list[dict[str, str]]:
    """Credits from the config plus the feed's attributions.txt, one entry per name, each with a link."""
    out: list[dict[str, str]] = [{"role": r, "name": n, "url": u} for r, n, u in feed.credits]
    with src.rows("attributions.txt") as rows:
        for r in rows:
            name = " ".join(r.get("organization_name", "").split())
            if not name:
                continue
            roles = [label for col, label in _ATTRIBUTION_ROLES if r.get(col) == "1"]
            out.append({"role": ", ".join(roles) or "credit", "name": name, "url": r.get("attribution_url", "")})
    seen: set[str] = set()
    unique = []
    for c in out:
        key = c["url"].rstrip("/") or c["name"]
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _next(db: sqlite3.Connection, col: str, table: str) -> int:
    return db.execute(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table}").fetchone()[0]


def import_feed(db: sqlite3.Connection, path: Path, feed: Feed, snapper: WalkGraph | None = None,
                log: Log = lambda *a: None, download_meta: dict | None = None) -> dict:
    """Imports one feed. Ids are namespaced with the feed key so several feeds can be merged.
    Returns the feed's metadata (status, validity, dates) for the city file's meta table. A feed file no real
    feed has (FeedTooLarge) leaves nothing of the feed behind, and the feed is reported as broken."""
    db.execute("SAVEPOINT import_feed")
    try:
        meta = _import_feed(db, path, feed, snapper, log, download_meta)
    except FeedTooLarge as e:
        db.execute("ROLLBACK TO import_feed")
        db.execute("RELEASE import_feed")
        log(f"GTFS {feed.key}: {e}; not imported")
        meta = {"key": feed.key, "name": feed.name, "url": feed.url, "licence": feed.licence,
                "attribution": feed.attribution}
        meta.update(download_meta or {}, url=feed.url, status=f"broken ({e})")
        return meta
    except BaseException:
        db.execute("RELEASE import_feed")  # as before: what was imported stays, and the error goes on
        raise
    db.execute("RELEASE import_feed")
    return meta


def _import_feed(db: sqlite3.Connection, path: Path, feed: Feed, snapper: WalkGraph | None,
                 log: Log, download_meta: dict | None) -> dict:
    meta = {"key": feed.key, "name": feed.name, "url": feed.url, "licence": feed.licence,
            "attribution": feed.attribution, "status": "missing"}
    path = Path(path)
    if not path.exists():
        log(f"GTFS {feed.key}: missing {path}")
        return meta
    meta.update(download_meta or {}, url=feed.url)
    try:
        src = Source(path)
    except zipfile.BadZipFile:
        log(f"GTFS {feed.key}: broken zip")
        meta["status"] = "broken"
        return meta
    if not meta.get("last_modified") and src.zip is not None:
        # No Last-Modified header (e.g. Wrocław's portal): fall back to the newest file date in the zip.
        # ZIP dates carry no time zone; operators write them in local time.
        newest = max(i.date_time for i in src.zip.infolist())
        meta["files_dated"] = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(*newest[:5])
    elif not meta.get("last_modified"):
        # A folder made by a local script (Zakopane's transcription): no download, so no other date; its newest
        # file's modification time, in Polish time as a zip's would be.
        stamps = [f.stat().st_mtime for f in src.path.iterdir() if f.is_file()]
        if stamps:
            meta["files_dated"] = _datetime.fromtimestamp(max(stamps), WARSAW).strftime("%Y-%m-%d %H:%M")
    for f in ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt"):
        if not src.has(f):
            meta["status"] = f"broken (no {f})"
            return meta
    P = feed.key + ":"
    meta["credits"] = feed_credits(feed, src)
    stated_start = stated_end = 0
    with src.rows("feed_info.txt") as rows:
        for r in rows:
            meta["feed_version"] = r.get("feed_version") or r.get("feed_end_date") or ""
            stated_start = int(r.get("feed_start_date") or 0)
            stated_end = int(r.get("feed_end_date") or 0)

    # With feed.stop_timezone, a feed is cut to the trips that serve that zone (D29: whole trips of services that
    # touch Poland). They keep every stop whose clock agrees with Polish time over the feed's dates (e.g. Praha,
    # Berlin); a stop on another clock (e.g. Vilnius) is dropped, and the trip keeps its parts on Polish time.
    today = _date.today()
    clock_lo = stated_start or int(today.strftime("%Y%m%d"))
    clock_hi = stated_end or int((today + _timedelta(days=400)).strftime("%Y%m%d"))
    clocks: dict[str, bool] = {}
    not_stops = re.compile(feed.not_stops) if feed.not_stops else None

    def on_polish_clock(zone: str) -> bool:
        if zone not in clocks:
            clocks[zone] = bool(zone) and same_clock(zone, clock_lo, clock_hi)
        return clocks[zone]
    home: set[int] = set()  # stops in feed.stop_timezone itself

    # stops
    split_platform = None
    if feed.platform_in_name:
        with src.rows("stops.txt") as rows:
            split_platform = platform_splitter([r.get("stop_name", "") for r in rows
                                                if r.get("location_type") in (None, "", "0")])
    stop_idx: dict[str, int] = {}
    si = _next(db, "stop_idx", "stops")
    with src.rows("stops.txt") as rows:
        for r in rows:
            if r.get("location_type") not in (None, "", "0"):
                continue
            if not_stops and not_stops.search(r.get("stop_name", "")):
                continue  # a waypoint, not a stop for passengers: the trip passes it
            zone = r.get("stop_timezone") or ""
            if feed.stop_timezone and zone != feed.stop_timezone and not on_polish_clock(zone):
                continue  # on another clock: trips keep only their stops on Polish time
            try:
                lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
            except (KeyError, ValueError):
                continue
            if lat == 0 and lon == 0:
                continue
            name, platform = r["stop_name"], r.get("platform_code") or r.get("stop_desc") or ""
            m = split_platform(name) if split_platform else None
            if m and not platform:  # "Oliwa PKP 02" → name + platform
                name, platform = m[1], m[2]
            if not re.fullmatch(r"\w{0,4}", platform):
                platform = ""
            snap = snapper.snap(lat, lon, 300) if snapper else None
            db.execute("INSERT INTO stops(stop_idx, feed, stop_id, code, name, platform, name_norm, lat, lon, node, snap_m) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (si, feed.key, P + r["stop_id"], r.get("stop_code") or None, name, platform or None, fold(name), lat, lon,
                        snap.node if snap else None, round(snap.dist) if snap else None))
            db.execute("INSERT INTO stops_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (?,?,?,?,?)", (si, lat, lat, lon, lon))
            stop_idx[r["stop_id"]] = si
            if zone == feed.stop_timezone:
                home.add(si)
            si += 1

    # agencies: route -> operator name (GTFS agency_name; some feeds name the transport authority instead)
    agencies: dict[str, str] = {}
    renamed = dict(feed.operator_names)
    with src.rows("agency.txt") as rows:
        for r in rows:
            name = " ".join(r.get("agency_name", "").split())
            agencies[r.get("agency_id", "")] = renamed.get(name, name)
    only_agency = next(iter(agencies.values())) if len(agencies) == 1 else None

    # routes
    route_idx: dict[str, int] = {}
    ri = _next(db, "route_idx", "routes")
    with src.rows("routes.txt") as rows:
        for r in rows:
            db.execute("INSERT INTO routes(route_idx, feed, route_id, short_name, long_name, mode, color, text_color, agency) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (ri, feed.key, P + r["route_id"], r.get("route_short_name") or r.get("route_long_name") or r["route_id"],
                        r.get("route_long_name") or None, mode_of(int(r.get("route_type") or 3)),
                        (r.get("route_color") or "").upper() or None, (r.get("route_text_color") or "").upper() or None,
                        agencies.get(r.get("agency_id", "")) or only_agency or None))
            route_idx[r["route_id"]] = ri
            ri += 1

    # services (calendar + calendar_dates)
    svc_idx: dict[str, int] = {}
    vi = [_next(db, "service_idx", "calendar")]

    def svc(sid: str) -> int:
        if sid not in svc_idx:
            svc_idx[sid] = vi[0]
            db.execute("INSERT INTO calendar(service_idx, feed, service_id, days, start_date, end_date) VALUES (?,?,?,0,0,0)",
                       (vi[0], feed.key, P + sid))
            vi[0] += 1
        return svc_idx[sid]

    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    with src.rows("calendar.txt") as rows:
        for r in rows:
            mask = sum(1 << i for i, d in enumerate(days) if r.get(d) == "1")
            db.execute("UPDATE calendar SET days = ?, start_date = ?, end_date = ? WHERE service_idx = ?",
                       (mask, int(r["start_date"]), int(r["end_date"]), svc(r["service_id"])))
    with src.rows("calendar_dates.txt") as rows:
        for r in rows:
            db.execute("INSERT OR REPLACE INTO calendar_dates(service_idx, date, exception_type) VALUES (?,?,?)",
                       (svc(r["service_id"]), int(r["date"]), int(r["exception_type"])))

    # shapes, simplified (4 m by default)
    shapes: dict[str, list[tuple[float, float, float]]] = {}
    with src.rows("shapes.txt" if feed.shapes else "-") as rows:
        for r in rows:
            shapes.setdefault(r["shape_id"], []).append((float(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
    shape_idx: dict[str, int] = {}
    hi = _next(db, "shape_idx", "shapes")
    for sid, pts in shapes.items():
        pts.sort()
        db.execute("INSERT INTO shapes(shape_idx, feed, shape_id, coords) VALUES (?,?,?,?)",
                   (hi, feed.key, P + sid, encode_parts([simplify([(p[1], p[2]) for p in pts], feed.shape_tolerance_m)])))
        shape_idx[sid] = hi
        hi += 1
    shapes.clear()

    # trips
    trips: list[tuple[int, int, str, int, int, str]] = []  # route, service, headsign, dir, shape, trip_id
    trip_idx: dict[str, int] = {}
    details: dict[int, tuple[str, str]] = {}  # trip → (train name and number, category), with feed.trip_details
    with src.rows("trips.txt") as rows:
        for r in rows:
            route = route_idx.get(r["route_id"])
            if route is None:
                continue
            if feed.trip_details:
                details[len(trips)] = (" ".join((r.get("trip_short_name") or "").split()),
                                       (r.get("plk_category_code") or "").strip())
            trip_idx[r["trip_id"]] = len(trips)
            d = r.get("direction_id", "")
            trips.append((route, svc(r["service_id"]), r.get("trip_headsign") or "", int(d) if d != "" else -1,
                          shape_idx.get(r.get("shape_id", ""), 0), r["trip_id"]))

    # frequency-based trips (e.g. Warsaw metro): template trip + "every N s between start and end"
    freq: dict[str, list[tuple[int, int, int, bool]]] = {}
    with src.rows("frequencies.txt") as rows:
        for r in rows:
            s0, s1 = parse_gtfs_time(r.get("start_time", "")), parse_gtfs_time(r.get("end_time", ""))
            h = int(r.get("headway_secs") or 0)
            if s0 >= 0 and s1 > s0 and h > 0:
                freq.setdefault(r["trip_id"], []).append((s0, s1, h, r.get("exact_times") == "1"))

    # stop_times → columnar arrays, then grouped per trip
    c_trip, c_seq, c_stop, c_arr, c_dep, c_fl = (np.empty(1 << 16, np.int64) for _ in range(6))
    c_where: list[str] = []  # "platform/track" per stop time, with feed.trip_details
    n = skipped = 0
    with src.rows("stop_times.txt") as rows:
        for r in rows:
            t, s = trip_idx.get(r["trip_id"]), stop_idx.get(r["stop_id"])
            if t is None or s is None:
                skipped += 1
                continue
            if n == len(c_trip):
                c_trip, c_seq, c_stop, c_arr, c_dep, c_fl = (np.resize(a, 2 * n) for a in (c_trip, c_seq, c_stop, c_arr, c_dep, c_fl))
            a, d = parse_gtfs_time(r.get("arrival_time", "")), parse_gtfs_time(r.get("departure_time", ""))
            c_trip[n], c_seq[n], c_stop[n] = t, int(r["stop_sequence"]), s
            c_arr[n], c_dep[n] = (a if a >= 0 else d), (d if d >= 0 else a)
            c_fl[n] = (1 if r.get("pickup_type") == "1" else 0) | (2 if r.get("drop_off_type") == "1" else 0)
            if feed.trip_details:
                where = f'{(r.get("platform") or "").strip()}/{(r.get("track") or "").strip()}'
                c_where.append("" if where == "/" else where)
            n += 1
    log(f"GTFS {feed.key}: {len(stop_idx)} stops, {len(route_idx)} routes, {len(trips)} trips, {n} stop_times ({skipped} skipped)")
    order = np.lexsort((c_seq[:n], c_trip[:n]))
    t_sorted = c_trip[:n][order]
    bounds = np.flatnonzero(np.diff(t_sorted)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [n]))
    pattern_key: dict[tuple, int] = {}
    pi = _next(db, "pattern_idx", "patterns")
    # (pattern, times, headway, details) -> [first trip_id, first_dep, services]: identical trips are stored once
    runs: dict[tuple[int, bytes, int, tuple | None], list] = {}

    def add_run(p: int, tid: str, times: np.ndarray, first_dep: int, service: int, headway: int,
                info: tuple | None = None) -> None:
        k = (p, times.astype("<i4").tobytes(), headway, info)
        run = runs.get(k)
        if run is None:
            runs[k] = [tid, first_dep, {service}]
        else:
            run[2].add(service)

    # A trip whose kept part starts after 24:00 (e.g. a coach from abroad that reaches Poland the next day)
    # or whose times are in UTC runs under derived services: its dates moved by whole days, and split by
    # Poland's UTC offset on each date (+1 h in winter, +2 h in summer). The offset is taken at noon, so
    # runs in the hour of a clock change can be off by an hour on those two nights a year. A trip that starts abroad
    # the evening before takes the offset of the day it reaches Poland (`odays`), not of the day it starts.
    derived: dict[tuple[int, int, int], list[tuple[int, int]]] = {}

    def variants(service: int, days: int, odays: int) -> list[tuple[int, int]]:
        if (service, days, odays) not in derived:
            groups: dict[int, set[int]] = {}
            for d in _dates_of(db, service):
                d0 = _date(d // 10000, d // 100 % 100, d % 100)
                day, oday = d0 + _timedelta(days=days), d0 + _timedelta(days=odays)
                off = int(WARSAW.utcoffset(_datetime(oday.year, oday.month, oday.day, 12)).total_seconds()) if feed.utc_times else 0
                groups.setdefault(off, set()).add(day.year * 10000 + day.month * 100 + day.day)
            out = []
            for off, dates in sorted(groups.items()):
                sid = _next(db, "service_idx", "calendar")
                db.execute("INSERT INTO calendar(service_idx, feed, service_id, days, start_date, end_date) VALUES (?,?,?,0,0,0)",
                           (sid, feed.key, f"{P}derived-{service}{days:+d}d{odays:+d}o{off:+d}s"))
                db.executemany("INSERT INTO calendar_dates(service_idx, date, exception_type) VALUES (?,?,1)",
                               ((sid, d) for d in sorted(dates)))
                out.append((off, sid))
            derived[(service, days, odays)] = out
        return derived[(service, days, odays)]

    for a, b in zip(starts.tolist(), ends.tolist()):
        if b - a < 2:
            continue
        idx = order[a:b]
        if feed.stop_timezone and not home.intersection(c_stop[idx].tolist()):
            continue  # a trip that does not serve the zone (e.g. Paris - Lyon)
        route, service, head, direction, shape, tid = trips[int(c_trip[idx[0]])]
        arr, dep = c_arr[idx].copy(), c_dep[idx].copy()
        missing = dep < 0
        if missing.any():  # interpolate non-timepoints linearly by position
            known = np.flatnonzero(~missing)
            dep[missing] = arr[missing] = np.round(np.interp(np.flatnonzero(missing), known, dep[known])).astype(np.int64)
        if feed.stop_timezone and int(arr[-1]) - int(dep[0]) // DAY * DAY >= 2 * DAY - 2 * 3600:
            # a day's timetable reaches back one day only, so a run must end within 48 h of its day's midnight
            # (2 h kept for the UTC offset): leave out its first stops abroad until it does
            cut = (int(arr[-1]) - 2 * DAY + 2 * 3600) // DAY + 1
            keep = dep >= cut * DAY
            idx, arr, dep = idx[keep], arr[keep], dep[keep]
            if len(idx) < 2 or not home.intersection(c_stop[idx].tolist()):
                continue
        stops = c_stop[idx].astype("<i4")
        fl = c_fl[idx].astype(np.uint8)
        fl[0] |= 2  # no drop-off at the first stop
        fl[-1] |= 1  # no pickup at the last stop
        key = (route, head, direction, shape, stops.tobytes(), fl.tobytes())
        p = pattern_key.get(key)
        if p is None:
            p = pattern_key[key] = pi
            pi += 1
            db.execute("INSERT INTO patterns(pattern_idx, route_idx, headsign, direction_id, shape_idx, stops, flags) VALUES (?,?,?,?,?,?,?)",
                       (p, route, head or None, direction, shape or None, stops.tobytes(), fl.tobytes()))
            db.executemany("INSERT OR IGNORE INTO pattern_stops(stop_idx, pattern_idx, pos) VALUES (?,?,?)",
                           [(int(s), p, k) for k, s in enumerate(stops)])
        times = dep if np.array_equal(arr, dep) else np.column_stack((arr, dep)).ravel()
        info = None
        if feed.trip_details:
            train, category = details.get(int(c_trip[idx[0]]), ("", ""))
            info = (train, category, "|".join(c_where[i] for i in idx.tolist()))
        if tid not in freq and (feed.utc_times or (feed.stop_timezone and dep[0] >= DAY)):
            days = int(dep[0]) // DAY
            first_home = next((k for k, s_ in enumerate(stops.tolist()) if s_ in home), 0) if feed.stop_timezone else 0
            odays = int(dep[first_home]) // DAY if feed.stop_timezone else days
            for off, sv in variants(service, days, odays):
                add_run(p, tid, times - days * DAY + off, int(dep[0]) - days * DAY + off, sv, 0, info)
        elif tid in freq:
            # expand to one run per headway; times are only approximate unless exact_times = 1
            offset = times - int(dep[0])
            for s0, s1, h, exact in freq[tid]:
                for start in range(s0, s1, h):
                    add_run(p, tid, offset + start, start, service, 0 if exact else h)
        else:
            add_run(p, tid, times, int(dep[0]), service, 0, info)
    services_by_set = _merge_services(db, feed.key, P, {frozenset(r[2]) for r in runs.values()})
    t0 = _next(db, "trip_idx", "trips")
    db.executemany("INSERT INTO trips(trip_idx, pattern_idx, service_idx, trip_id, first_dep, times, headway) VALUES (?,?,?,?,?,?,?)",
                   ((t0 + i, k[0], services_by_set[frozenset(r[2])], r[0], r[1], k[1], k[2] or None)
                    for i, (k, r) in enumerate(runs.items())))
    if feed.trip_details:
        db.executemany("INSERT INTO trip_info(trip_idx, train, category, platforms) VALUES (?,?,?,?)",
                       ((t0 + i, k[3][0] or None, k[3][1] or None, k[3][2] if k[3][2].strip("|") else None)
                        for i, k in enumerate(runs) if k[3]))
    if feed.stop_timezone or feed.utc_times:
        _drop_unused(db, feed.key, log)
    log(f"GTFS {feed.key}: {len(runs)} distinct runs stored "
        f"({sum(1 for k in runs if k[2])} from frequencies without exact times)")
    lo = db.execute("SELECT MIN(start_date), MAX(end_date) FROM calendar WHERE feed = ? AND start_date > 0 AND days > 0", (feed.key,)).fetchone()
    cd = db.execute("SELECT MIN(date), MAX(date) FROM calendar_dates JOIN calendar USING(service_idx) "
                    "WHERE feed = ? AND exception_type = 1", (feed.key,)).fetchone()
    meta["start_date"] = min(v for v in (lo[0], cd[0], 99999999) if v)
    # a feed may keep a few services' past dates (PKP PLK's runs back to the timetable year's start):
    # validity starts where the publisher says it does
    meta["start_date"] = max(meta["start_date"], stated_start)
    meta["end_date"] = max(v for v in (lo[1], cd[1], 0) if v)
    meta["status"] = "ok"
    meta["runs"] = len(runs)  # compared with the last good file before it is replaced (build/safeguard.py)
    log(f"GTFS {feed.key}: {len(pattern_key)} patterns, valid {meta['start_date']}..{meta['end_date']}")
    return meta


def _service_dates(db: sqlite3.Connection, feed_key: str) -> dict[int, set[int]]:
    """Every date each of the feed's services runs on: calendar weekdays plus calendar_dates exceptions."""
    out: dict[int, set[int]] = {}
    for sidx, days, start, end in db.execute(
            "SELECT service_idx, days, start_date, end_date FROM calendar WHERE feed = ?", (feed_key,)):
        dates = out.setdefault(sidx, set())
        if days and start and end:
            d, stop = (_date(v // 10000, v // 100 % 100, v % 100) for v in (start, end))
            while d <= stop:
                if days & (1 << d.weekday()):
                    dates.add(d.year * 10000 + d.month * 100 + d.day)
                d += _timedelta(days=1)
    for sidx, date, ex in db.execute("SELECT service_idx, date, exception_type FROM calendar_dates "
                                     "JOIN calendar USING(service_idx) WHERE feed = ?", (feed_key,)):
        (out.setdefault(sidx, set()).add if ex == 1 else out.setdefault(sidx, set()).discard)(date)
    return out


def _merge_services(db: sqlite3.Connection, feed_key: str, prefix: str, groups: set[frozenset[int]]) -> dict[frozenset[int], int]:
    """A service for each set of services that share identical trips: the union of their dates."""
    out = {g: next(iter(g)) for g in groups if len(g) == 1}
    multi = [g for g in groups if len(g) > 1]
    if not multi:
        return out
    dates = _service_dates(db, feed_key)
    nxt = _next(db, "service_idx", "calendar")
    for g in sorted(multi, key=sorted):
        union = set().union(*(dates.get(s, set()) for s in g))
        db.execute("INSERT INTO calendar(service_idx, feed, service_id, days, start_date, end_date) VALUES (?,?,?,0,0,0)",
                   (nxt, feed_key, f"{prefix}merged-{nxt}"))
        db.executemany("INSERT INTO calendar_dates(service_idx, date, exception_type) VALUES (?,?,1)",
                       ((nxt, d) for d in sorted(union)))
        out[g] = nxt
        nxt += 1
    return out


def compute_transfers(db: sqlite3.Connection, graph: WalkGraph | None, max_m: float = 400) -> int:
    """Foot transfers between stops within max_m: along the walking graph, else straight line × 1.3."""
    rows = db.execute("SELECT stop_idx, lat, lon, node, snap_m FROM stops").fetchall()
    out = []
    if graph is not None:
        snapped = [r for r in rows if r[3] is not None]
        nodes = np.array([r[3] for r in snapped], dtype=np.int64)
        extra = np.array([r[4] or 0 for r in snapped], dtype=np.float64)
        for i, (s, _, _, node, snap_m) in enumerate(snapped):
            dist, _ = graph.search(node, max(0.0, max_m - (snap_m or 0)))
            total = dist[nodes] + extra + (snap_m or 0)
            for j in np.flatnonzero(total <= max_m):
                if j != i:
                    out.append((s, snapped[j][0], int(round(total[j] / WALK_SPEED_MPS))))
    elif rows:
        # no walking graph (the national layer): straight line × 1.3, one vectorised pass per stop
        idx = np.array([r[0] for r in rows], dtype=np.int64)
        lat = np.array([r[1] for r in rows], dtype=np.float64)
        lon = np.array([r[2] for r in rows], dtype=np.float64)
        for i in range(len(rows)):  # fast_dist, vectorised
            x = (lon - lon[i]) * RAD * np.cos((lat + lat[i]) / 2 * RAD)
            d = 1.3 * np.sqrt(x * x + ((lat - lat[i]) * RAD) ** 2) * R
            for j in np.flatnonzero(d <= max_m):
                if j != i:
                    out.append((int(idx[i]), int(idx[j]), int(round(d[j] / WALK_SPEED_MPS))))
    db.executemany("INSERT OR REPLACE INTO transfers(from_stop, to_stop, walk_s) VALUES (?,?,?)", out)
    return len(out)


def _dates_of(db: sqlite3.Connection, sidx: int) -> set[int]:
    """Every date one service runs on: its calendar weekdays plus calendar_dates exceptions."""
    dates: set[int] = set()
    row = db.execute("SELECT days, start_date, end_date FROM calendar WHERE service_idx = ?", (sidx,)).fetchone()
    if row and row[0] and row[1] and row[2]:
        d, stop = (_date(v // 10000, v // 100 % 100, v % 100) for v in (row[1], row[2]))
        while d <= stop:
            if row[0] & (1 << d.weekday()):
                dates.add(d.year * 10000 + d.month * 100 + d.day)
            d += _timedelta(days=1)
    for date, ex in db.execute("SELECT date, exception_type FROM calendar_dates WHERE service_idx = ?", (sidx,)):
        (dates.add if ex == 1 else dates.discard)(date)
    return dates


def _drop_unused(db: sqlite3.Connection, feed_key: str, log: Log) -> None:
    """Removes what a cut feed no longer uses: services, routes and stops of trips outside the area."""
    n = {}
    n["services"] = db.execute("DELETE FROM calendar WHERE feed = ? AND service_idx NOT IN (SELECT service_idx FROM trips)",
                               (feed_key,)).rowcount
    db.execute("DELETE FROM calendar_dates WHERE service_idx NOT IN (SELECT service_idx FROM calendar)")
    n["routes"] = db.execute("DELETE FROM routes WHERE feed = ? AND route_idx NOT IN (SELECT route_idx FROM patterns)",
                             (feed_key,)).rowcount
    unused = [r[0] for r in db.execute("SELECT stop_idx FROM stops WHERE feed = ? AND stop_idx NOT IN "
                                       "(SELECT stop_idx FROM pattern_stops)", (feed_key,))]
    db.executemany("DELETE FROM stops WHERE stop_idx = ?", ((i,) for i in unused))
    db.executemany("DELETE FROM stops_rtree WHERE id = ?", ((i,) for i in unused))
    n["stops"] = len(unused)
    log(f"GTFS {feed_key}: dropped unused " + ", ".join(f"{v} {k}" for k, v in n.items()))

