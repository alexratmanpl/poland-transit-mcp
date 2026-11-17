"""SQLite schema of one city file: built offline, opened read-only by the server."""

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE blobs(name TEXT PRIMARY KEY, data BLOB);

CREATE TABLE places(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, name_norm TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, ref TEXT, extra TEXT);
CREATE INDEX places_kind ON places(kind);
CREATE VIRTUAL TABLE places_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);

CREATE TABLE features(id INTEGER PRIMARY KEY, layer TEXT NOT NULL, class TEXT, poly INTEGER NOT NULL, coords BLOB NOT NULL);
CREATE VIRTUAL TABLE features_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);

CREATE TABLE stops(stop_idx INTEGER PRIMARY KEY, feed TEXT NOT NULL, stop_id TEXT NOT NULL UNIQUE, code TEXT, name TEXT NOT NULL,
  platform TEXT, name_norm TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, node INTEGER, snap_m INTEGER);
CREATE INDEX stops_name ON stops(name_norm);
CREATE VIRTUAL TABLE stops_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE routes(route_idx INTEGER PRIMARY KEY, feed TEXT NOT NULL, route_id TEXT NOT NULL, short_name TEXT NOT NULL,
  long_name TEXT, mode TEXT NOT NULL, color TEXT, text_color TEXT, agency TEXT);
CREATE INDEX routes_short ON routes(short_name);
CREATE TABLE calendar(service_idx INTEGER PRIMARY KEY, feed TEXT NOT NULL, service_id TEXT NOT NULL,
  days INTEGER NOT NULL, start_date INTEGER NOT NULL, end_date INTEGER NOT NULL);
CREATE TABLE calendar_dates(service_idx INTEGER NOT NULL, date INTEGER NOT NULL, exception_type INTEGER NOT NULL,
  PRIMARY KEY(service_idx, date)) WITHOUT ROWID;
CREATE INDEX calendar_dates_date ON calendar_dates(date);
CREATE TABLE shapes(shape_idx INTEGER PRIMARY KEY, feed TEXT NOT NULL, shape_id TEXT NOT NULL, coords BLOB NOT NULL);
-- A pattern is one stop sequence of one route (+ headsign/shape); stops/flags are packed arrays
-- (Int32 stop_idx; Uint8 bit0 = no pickup, bit1 = no drop-off).
CREATE TABLE patterns(pattern_idx INTEGER PRIMARY KEY, route_idx INTEGER NOT NULL, headsign TEXT, direction_id INTEGER,
  shape_idx INTEGER, stops BLOB NOT NULL, flags BLOB NOT NULL);
CREATE TABLE pattern_stops(stop_idx INTEGER NOT NULL, pattern_idx INTEGER NOT NULL, pos INTEGER NOT NULL,
  PRIMARY KEY(stop_idx, pattern_idx, pos)) WITHOUT ROWID;
-- stop_times, packed per trip: Int32 seconds, either [dep...] (arrival = departure) or [arr, dep, arr, dep...].
-- Identical trips of several services are stored once, under a service covering all their dates.
-- headway: set for runs expanded from frequencies.txt without exact times (the feed only gives "every N s").
CREATE TABLE trips(trip_idx INTEGER PRIMARY KEY, pattern_idx INTEGER NOT NULL, service_idx INTEGER NOT NULL,
  trip_id TEXT NOT NULL, first_dep INTEGER NOT NULL, times BLOB NOT NULL, headway INTEGER);
CREATE INDEX trips_service ON trips(service_idx);
CREATE TABLE transfers(from_stop INTEGER NOT NULL, to_stop INTEGER NOT NULL, walk_s INTEGER NOT NULL,
  PRIMARY KEY(from_stop, to_stop)) WITHOUT ROWID;
-- Rail feeds only: the train's name and number as published (e.g. "3120/1 Karłowicz"), its category
-- (IC, EIP, R...), and "platform/track" at each stop position, "|"-separated ("" where unknown).
CREATE TABLE trip_info(trip_idx INTEGER PRIMARY KEY, train TEXT, category TEXT, platforms TEXT);
"""
