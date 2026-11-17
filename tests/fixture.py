"""Synthetic city "testowo" for tests: a walking grid split by a river with one bridge, and a small
GTFS feed (two day lines, a night line, calendar exceptions, a no-pickup stop). No real data."""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np

from poland_transit_mcp.build.gtfs import compute_transfers, import_feed
from poland_transit_mcp.city import City
from poland_transit_mcp.config import Feed
from poland_transit_mcp.geo import encode_parts
from poland_transit_mcp.schema import SCHEMA
from poland_transit_mcp.text import fold
from poland_transit_mcp.walk import FLAG_BRIDGE, GraphArrays, WalkGraph

LAT0, LON0 = 50.0, 19.0
DLAT, DLON = 0.0009, 0.0014  # ≈100 m
ROWS, COLS = 24, 16
RIVER_COL = 7.5  # the river runs between grid columns 7 and 8
BRIDGE_ROW = 20


def node(r: int, c: int) -> int:
    return r * COLS + c


def at(r: int, c: int) -> list[float]:
    return [LAT0 + r * DLAT, LON0 + c * DLON]


def grid_graph() -> GraphArrays:
    n = ROWS * COLS
    adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]

    def link(a: int, b: int, f: int = 0) -> None:
        adj[a].append((b, f))
        adj[b].append((a, f))

    for r in range(ROWS):
        for c in range(COLS):
            if r + 1 < ROWS:
                link(node(r, c), node(r + 1, c))
            if c + 1 < COLS:
                if not (c < RIVER_COL < c + 1):
                    link(node(r, c), node(r, c + 1))
                elif r == BRIDGE_ROW:
                    link(node(r, c), node(r, c + 1), FLAG_BRIDGE)
    coords = np.array([v for r in range(ROWS) for c in range(COLS) for v in (round(at(r, c)[0] * 1e6), round(at(r, c)[1] * 1e6))], np.int32)
    off = np.zeros(n + 1, np.uint32)
    off[1:] = np.cumsum([len(a) for a in adj])
    tgt = np.array([b for a in adj for b, _ in a], np.uint32)
    flags = np.array([f for a in adj for _, f in a], np.uint8)
    src = np.repeat(np.arange(n), [len(a) for a in adj])
    d_lat = (coords[0::2][src] - coords[0::2][tgt]) / 1e6 * 111320
    d_lon = (coords[1::2][src] - coords[1::2][tgt]) / 1e6 * 111320 * math.cos(math.radians(LAT0))
    return GraphArrays(coords, off, tgt, np.sqrt(d_lat ** 2 + d_lon ** 2).astype(np.float32), flags)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}:00"


def write_gtfs(d: Path) -> None:
    """Stops: A(2,1) B(2,5) C(2,12) on tram 1; C2 next to C, D(10,12), E(18,12) on bus 2.
    Tram 1 (weekdays) A→B→C every 10 min 06:00–22:00, 5+5 min; Saturdays/holidays every 20 min from 06:05.
    Trip 1-X (weekday 08:03) is an express that does not pick up at B. Bus 2 (all days) C2→D→E every
    15 min 06:15–22:15. Night line N1 (weekdays) A 24:30 → C 24:45 → E 25:10.
    2026-11-11 (Wednesday) is a holiday: weekday service removed, Saturday service added.
    Metro M (MN → MS): a frequency-only template, every 5 min 06:00–07:00 (12 runs, times not exact), plus
    a 23:00 run published twice, once in WD and once in SAT, which the import stores once.
    attributions.txt credits a converter, as mkuran.pl feeds do."""
    d.mkdir(parents=True, exist_ok=True)

    def st(r: int, c: int, dlat: float = 0, dlon: float = 0) -> list[str]:
        p = at(r, c)
        return [f"{p[0] + dlat:.6f}", f"{p[1] + dlon:.6f}"]

    files: dict[str, list[list]] = {
        "agency.txt": [["agency_id", "agency_name", "agency_url", "agency_timezone"], ["T", "Testowo Transit", "https://example.invalid", "Europe/Warsaw"]],
        "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon", "stop_code"],
                      ["A", '"Aleja Łąkowa"', *st(2, 1), "A1"], ["B", "Brzozowa 01", *st(2, 5), "B1"], ["C", "Centrum", *st(2, 12), "C1"],
                      ["C2", "Centrum", *st(2, 12, 0.0002, 0.0003), "C2"], ["D", "Dworcowa", *st(10, 12), "D1"], ["E", "Ełcka", *st(18, 12), "E1"],
                      ["P", "Pętla 01", *st(22, 1), "P1"], ["Q", "Pętla 02", *st(22, 1, 0, 0.0005), "Q1"], ["R", "Rondo", *st(22, 6), "R1"],
                      ["MN", "Metro Północ", *st(23, 10), "MN"], ["MS", "Metro Południe", *st(23, 14), "MS"]],
        "routes.txt": [["route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_color"],
                       ["R1", "T", "1", "", 0, "CC0000"], ["R2", "T", "2", "", 3, ""], ["RN", "T", "N1", "", 3, "000080"],
                       ["R3", "T", "L3", "", 3, ""], ["RM", "T", "M", "", 1, ""]],
        "calendar.txt": [["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
                         ["WD", 1, 1, 1, 1, 1, 0, 0, 20260101, 20261231], ["SAT", 0, 0, 0, 0, 0, 1, 0, 20260101, 20261231],
                         ["ALL", 1, 1, 1, 1, 1, 1, 1, 20260101, 20261231]],
        "calendar_dates.txt": [["service_id", "date", "exception_type"], ["WD", 20261111, 2], ["SAT", 20261111, 1]],
        "trips.txt": [["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"]],
        # line L3 leaves platform P, loops north and west, and only then passes platform Q (next to P)
        # on its way to R, like a bus turning round at a terminus
        "shapes.txt": [["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
                       ["S3", *st(22, 1), 1], ["S3", *st(23.5, 1), 2], ["S3", *st(23.5, 0), 3], ["S3", *st(22, 0), 4], ["S3", *st(22, 6), 5]],
        "stop_times.txt": [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "pickup_type", "drop_off_type"]],
        "frequencies.txt": [["trip_id", "start_time", "end_time", "headway_secs", "exact_times"], ["M-F", "06:00:00", "07:00:00", 300, 0]],
        "attributions.txt": [["organization_name", "attribution_url", "is_producer", "is_operator", "is_authority", "is_data_source"],
                             ["GTFS provided by: Test Converter", "https://example.invalid/converter", 1, 0, 0, 1]],
    }

    def trip(route: str, svc: str, tid: str, head: str, stops: list[tuple[str, int]], no_pick: str | None = None, shape: str = "") -> None:
        files["trips.txt"].append([route, svc, tid, head, 0, shape])
        for i, (s, m) in enumerate(stops):
            files["stop_times.txt"].append([tid, _hhmm(m), _hhmm(m), s, i + 1, 1 if s == no_pick else 0, 0])

    for m in range(6 * 60, 22 * 60 + 1, 10):
        trip("R1", "WD", f"1-WD-{m}", "Centrum", [("A", m), ("B", m + 5), ("C", m + 10)])
    trip("R1", "WD", "1-X", "Centrum", [("A", 483), ("B", 487), ("C", 491)], no_pick="B")
    for m in range(6 * 60 + 5, 22 * 60 + 1, 20):
        trip("R1", "SAT", f"1-SAT-{m}", "Centrum", [("A", m), ("B", m + 5), ("C", m + 10)])
    for m in range(6 * 60 + 15, 22 * 60 + 16, 15):
        trip("R2", "ALL", f"2-{m}", "Ełcka", [("C2", m), ("D", m + 5), ("E", m + 10)])
    trip("RN", "WD", "N1-1", "Ełcka", [("A", 24 * 60 + 30), ("C", 24 * 60 + 45), ("E", 25 * 60 + 10)])
    trip("R3", "ALL", "L3-1", "Rondo", [("P", 600), ("Q", 604), ("R", 608)], shape="S3")
    trip("RM", "ALL", "M-F", "Metro Południe", [("MN", 360), ("MS", 364)])
    trip("RM", "WD", "M-23-WD", "Metro Południe", [("MN", 1380), ("MS", 1384)])
    trip("RM", "SAT", "M-23-SAT", "Metro Południe", [("MN", 1380), ("MS", 1384)])
    for name, rows in files.items():
        text = "\r\n".join(",".join(str(v) for v in r) for r in rows) + "\r\n"
        (d / name).write_text(("﻿" if name == "stops.txt" else "") + text, encoding="utf-8")


def build_fixture(directory: Path) -> Path:
    """Writes directory/testowo.sqlite and returns the directory."""
    write_gtfs(directory / "gtfs")
    db = sqlite3.connect(directory / "testowo.sqlite", isolation_level=None)
    db.executescript(SCHEMA)
    ga = grid_graph()
    graph = WalkGraph(ga)
    for k, v in ga.blobs().items():
        db.execute("INSERT INTO blobs(name, data) VALUES (?, ?)", (k, v))
    feed = Feed("T", "Testowo GTFS", "https://example.invalid/gtfs.zip", "CC0", "synthetic test data")
    meta = import_feed(db, directory / "gtfs", feed, graph,
                       download_meta={"downloaded_at": "2026-11-01T00:00:00Z", "last_modified": "Sun, 01 Nov 2026 00:00:00 GMT"})
    compute_transfers(db, graph, 400)
    pid = 0

    def place(kind: str, name: str, p: list[float], ref: str, extra: dict | None = None) -> None:
        nonlocal pid
        pid += 1
        db.execute("INSERT INTO places(id, kind, name, name_norm, lat, lon, ref, extra) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, kind, name, fold(name), p[0], p[1], ref, json.dumps(extra) if extra else None))
        db.execute("INSERT INTO places_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (?,?,?,?,?)", (pid, p[0], p[0], p[1], p[1]))

    for idx, sid, name, lat, lon in db.execute("SELECT stop_idx, stop_id, name, lat, lon FROM stops").fetchall():
        place("stop", name, [lat, lon], f"stop:{idx}", {"stop_id": sid})
    place("museum", "Muzeum Żółwia", at(18, 14), "n1")
    place("station", "Testowo Główne", at(2, 0), "n2")
    place("park", "Park Śródrzeczny", at(12, 3), "w3")
    lon_w, lon_e = LON0 + 7.2 * DLON, LON0 + 7.8 * DLON
    top = LAT0 + ROWS * DLAT
    river = [(LAT0 - 0.001, lon_w), (LAT0 - 0.001, lon_e), (top, lon_e), (top, lon_w), (LAT0 - 0.001, lon_w)]
    db.execute("INSERT INTO features(id, layer, class, poly, coords) VALUES (1, 'water', 'area', 1, ?)", (encode_parts([river]),))
    db.execute("INSERT INTO features_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (1, ?, ?, ?, ?)", (LAT0 - 0.001, top, lon_w, lon_e))
    mx = at(ROWS - 1, COLS - 1)
    for k, v in {"city": "testowo", "name": "Testowo (synthetic)",
                 "bbox": json.dumps([LAT0 - 0.002, LON0 - 0.002, mx[0] + 0.002, mx[1] + 0.002]),
                 "built_at": "2026-11-01T00:00:00Z",
                 "osm": json.dumps({"timestamp": "2026-10-31T00:00:00Z", "attribution": "synthetic grid (no OSM data)", "source": "test"}),
                 "feeds": json.dumps([meta])}.items():
        db.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, v))
    db.close()
    return directory


# ---------------------------------------------------------------- two cities and a national layer
# "alfa" is testowo where it is; "beta" is the same grid one degree further north (~111 km). The
# national layer has two trains between them (with names, categories, platforms and tracks) and a
# coach feed in UTC with one stop abroad, like FlixBus's European feed.
NATIONAL_LAT = {"alfa": 50.0, "beta": 51.0}


def build_city_copy(directory: Path, city_id: str, lat0: float) -> City:
    """The synthetic city with its grid moved to lat0, saved as directory/<city_id>.sqlite."""
    global LAT0
    old, LAT0 = LAT0, lat0
    try:
        work = directory / f"_{city_id}"
        build_fixture(work)
    finally:
        LAT0 = old
    db = sqlite3.connect(work / "testowo.sqlite")
    db.execute("UPDATE meta SET value = ? WHERE key = 'city'", (city_id,))
    db.execute("UPDATE meta SET value = ? WHERE key = 'name'", (f"{city_id.title()} (synthetic)",))
    db.commit()
    db.close()
    (work / "testowo.sqlite").replace(directory / f"{city_id}.sqlite")
    return City.open(directory, city_id)


def near(city_id: str, r: float, c: float) -> list[float]:
    return [NATIONAL_LAT[city_id] + r * DLAT, LON0 + c * DLON]


def write_national_gtfs(d: Path) -> tuple[Path, Path]:
    """Trains (PL): IC 101 "Testowy" alfa 09:00 → alfa east 09:10 → beta 11:00 and IC 103 at 13:00, on
    weekdays, with platforms and tracks. Coaches (FX, times in UTC, stops filtered by time zone): every day
    Ausland 05:00 → alfa coach stop 07:30 → beta coach stop 09:30 UTC, and a night run that only reaches
    alfa after midnight UTC (25:30 → beta 27:00)."""
    rail, coach = d / "rail", d / "coach"
    rail.mkdir(parents=True, exist_ok=True)
    coach.mkdir(parents=True, exist_ok=True)

    def ll(city: str, r: float, c: float) -> str:
        p = near(city, r, c)
        return f"{p[0]:.6f},{p[1]:.6f}"

    files = {
        rail: {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nKT,Koleje Testowe,https://example.invalid,Europe/Warsaw\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                         f"AG1,Alfa Główna,{ll('alfa', 2, 0)}\nAG2,Alfa Główna,{ll('alfa', 2, 0)}\n"
                         f"AW,Alfa Wschód,{ll('alfa', 10, 13)}\nBG,Beta Główna,{ll('beta', 18, 13)}\n",
            "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nIC,KT,IC,InterCity,2\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                            "WD,1,1,1,1,1,0,0,20260101,20261231\n",
            "trips.txt": "route_id,service_id,trip_id,trip_headsign,trip_short_name,plk_category_code\n"
                         "IC,WD,IC101,Beta Główna,101 Testowy,IC\nIC,WD,IC103,Beta Główna,103 Testowy,IC\n",
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,platform,track\n"
                              "IC101,09:00:00,09:00:00,AG1,1,II,4\nIC101,09:10:00,09:10:00,AW,2,1,1\nIC101,11:00:00,11:00:00,BG,3,III,7\n"
                              "IC103,13:00:00,13:00:00,AG2,1,I,2\nIC103,13:10:00,13:10:00,AW,2,1,1\nIC103,15:00:00,15:00:00,BG,3,III,7\n",
        },
        coach: {
            "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nFX,FlixBus-eu,https://example.invalid,UTC\n",
            "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,stop_timezone\n"
                         "X,Ausland,52.5,13.4,Europe/Berlin\n"
                         f"AC,Alfa Dworzec Autobusowy,{ll('alfa', 4, 2)},Europe/Warsaw\nBC,Beta Dworzec Autobusowy,{ll('beta', 16, 13)},Europe/Warsaw\n",
            "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nF1,FX,FlixBus 1,Ausland - Beta,3\n",
            "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                            "ALL,1,1,1,1,1,1,1,20260101,20261231\n",
            "trips.txt": "route_id,service_id,trip_id\nF1,ALL,F1-day\nF1,ALL,F1-night\n",
            "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                              "F1-day,05:00:00,05:00:00,X,1\nF1-day,07:30:00,07:30:00,AC,2\nF1-day,09:30:00,09:30:00,BC,3\n"
                              "F1-night,20:00:00,20:00:00,X,1\nF1-night,25:30:00,25:30:00,AC,2\nF1-night,27:00:00,27:00:00,BC,3\n",
        },
    }
    for folder, fs in files.items():
        for name, text in fs.items():
            (folder / name).write_text(text, encoding="utf-8")
    return rail, coach


RAIL_FEED = Feed("PL", "Testowe Koleje GTFS", "https://example.invalid/rail.zip", "CC0", "synthetic", trip_details=True)
COACH_FEED = Feed("FX", "Testowy Flix GTFS", "https://example.invalid/coach.zip", "ODbL 1.0", "synthetic",
                  stop_timezone="Europe/Warsaw", utc_times=True, shapes=False, operator_names=(("FlixBus-eu", "FlixBus"),))


def build_national_fixture(directory: Path) -> tuple[City, City, City]:
    """directory/{alfa,beta,poland}.sqlite; returns (alfa, beta, poland)."""
    directory.mkdir(parents=True, exist_ok=True)
    alfa = build_city_copy(directory, "alfa", NATIONAL_LAT["alfa"])
    beta = build_city_copy(directory, "beta", NATIONAL_LAT["beta"])
    rail, coach = write_national_gtfs(directory / "national")
    db = sqlite3.connect(directory / "poland.sqlite", isolation_level=None)
    db.executescript(SCHEMA)
    feeds = [import_feed(db, rail, RAIL_FEED), import_feed(db, coach, COACH_FEED)]
    compute_transfers(db, None)
    for k, v in {"city": "poland", "name": "Poland (synthetic national layer)", "kind": "national",
                 "bbox": json.dumps([49.9, 18.9, 51.1, 19.1]), "built_at": "2026-11-01T00:00:00Z",
                 "osm": json.dumps({}), "feeds": json.dumps(feeds)}.items():
        db.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, v))
    db.close()
    return alfa, beta, City.open(directory, "poland")
