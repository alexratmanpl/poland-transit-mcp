# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## 0.2.0 - 2026-11-17

The first public release.

### Added
- Optional Rust search engine (`poland-transit-mcp-engine`, the `fast` extra). Same answers as the built-in Python search; 17–21× faster per search and 2.3–3.4× per whole trip plan (total time, Apple Silicon Mac). Without it, the server falls back to Python.
- Cities load on demand within a memory budget (`--memory-budget`, default 480 MB), and the least recently used city is unloaded.
- More cities: Warszawa, Poznań, Toruń, Katowice (GZM) and Zakopane; timetables for Wrocław. Credits with links in every answer.
- Trips between cities: the national layer `poland` (PKP PLK train data and FlixBus in Poland) and `plan_trip` with `to_city`.
- Opt-in OpenTelemetry traces and metrics (the `otel` extra and the standard `OTEL_*` variables).
- `AGENTS.md` and `scripts/smoke.py`: from an empty machine to a working MCP entry.

### Changed
- Smaller city files with the same answers: identical trips are stored once, and unused map features are left out.

### Security
- The walking graph is validated before scipy's compiled code reads it.
- City files are never loaded from the caller's working directory.
- The MCP SDK must be 2.3 or newer (`mcp>=2.3,<3`), the version the code is written for.
- Tools check their input (repeated layers, malformed points and boxes). A malformed live-feed reply falls back to the timetable, and the read is capped at 2 MiB.
- The data workflow (`city-data.yml`, run by hand until the data release exists) keeps its write token off disk.

## 0.1.0 - never released

- MCP server for Polish cities: `list_cities`, `find_places`, `stops_near`, `walk_route`, `departures`, `line_info`, `plan_trip`, `map_extract` and `render_map`.
- Gdańsk and Kraków with timetables; Wrocław with map and walking only. Opt-in live departures for Gdańsk.
- One SQLite file per city, built from OpenStreetMap and official GTFS feeds (`poland-transit-mcp build`).
