# Decisions

One line each: what was chosen, and why.

## Data and build
- **OSM source:** Geofabrik first, OSM France mirror as automatic fallback. A second mirror keeps builds working when one is down; the mirror carries the same OSM data.
- **Extract size:** voivodeship extracts (128–219 MB) instead of `poland-latest.osm.pbf` (~1.9 GB), clipped per city with `osmium extract -s smart`. Same result, about a tenth of the download.
- **City areas:** each city's bounding box is the extent of its feed's stops. Kraków's covers the agglomeration bus network (~39 × 55 km).
- **Walking topology from GeoJSON:** `osmium export` drops node ids, so two ways connect where they share an identical coordinate (osmium writes 7 decimals). Bridges don't share nodes with what they cross.
- **Walking graph storage:** CSR arrays stored as five BLOBs, not an edge table. They load straight into a scipy sparse matrix: Gdańsk's 1.15 M directed edges in ~0.3 s. Only the largest connected component is kept, so a point can't snap to an island path.
- **Stop times storage:** packed per trip and grouped into stop patterns, instead of one row per stop time. Gdańsk's 2.2 M stop times take 13.6 MB; an estimated 60–80 MB as rows would push Kraków past the 150 MB budget.
- **Feeds without a fixed URL:** Wrocław's portal gives each snapshot its own download link. The build opens the dataset page and takes the first `/hdb/download/<n>/` link, which is the newest (`latest_link` in `config.py`).
- **Publication date without a Last-Modified header:** the newest file date inside the zip is used and labelled as such in `as_of` (zip dates have no time zone).
- **Identical trips are stored once:** many feeds repeat the same trip for every week (Warszawa: 271,683 trip records, 55,911 distinct) or every day (Gdańsk). The build keys each trip by its stop pattern and times, and stores it once under a service whose dates are the union of the originals. Answers are unchanged (acceptance output identical); files shrank: Gdańsk 52.8 → 41.2 MB, Kraków 106.9 → 98.3 MB. `trip_id` in answers is the first of the merged GTFS ids.
- **Frequency-only trips** (`frequencies.txt` without `exact_times`, e.g. Warsaw metro) are expanded into one run per headway so routing can use them. Those runs keep the headway, and every answer that uses one says `frequency-based`, gives "every N min" and notes that the time is estimated. Without this, the metro would have had 16 runs a day.
- **Credits:** each feed carries credits with links: from the config (e.g. the GTFS converter) and from the feed's own `attributions.txt`, repeated in every answer's `source` and in `list_cities`.
- **Cities without a timetable** (Zakopane) are still built for places, walking and maps, and say plainly that no machine-readable timetable is published.
- **Zakopane's PDF timetables are transcribed, not OCR'd:** the pages are images. Two separate readers transcribed all 16 pages independently and were compared cell by cell (4,513 cells, no differences) before `scripts/zakopane_gtfs.py` turns the result into GTFS. Misprinted cells are dropped and interpolated, never "corrected". The run dates come from the printed legends, inside an assumed window, because the PDFs print no validity dates; every answer says so. Stops without an OpenStreetMap stop are placed at their landmark (approximate) or on their street between their neighbours (estimated), and each stop's method is recorded.
- **What the map layer keeps:** service roads (no map layer draws them), sidewalks, crossings and lawns or gardens under 500 m² are not stored as map features. All of them stay in the walking graph, so routes don't change. This took Warszawa from 169.1 MB to 129.9 MB, under the 150 MB budget, and made every other city file smaller.
- **Stop names:** a trailing two-digit platform number (`Oliwa PKP 02`) is split off, so poles group by name.
- **Places:** only named objects are kept. Same kind + same name within 150 m count as one place. Streets are one point per name cluster.
- **Data stays out of git:** test fixtures are synthetic (`tests/fixture.py`), and real-data tests skip when no city file is present.

## Walking
- **Speed:** 4.8 km/h. Steps cost ×1.5. Snapping goes to the nearest graph node within 400 m, not to the nearest edge (simpler; nodes are dense in cities).
- **What is walkable:** footways, paths, steps, pedestrian zones, tracks, cycleways, and all roads up to primary. Trunk roads only with a sidewalk or `foot=yes`. Motorways, `foot=no` and private access are excluded. Ferries are never part of the walking graph.
- **Numeric precision:** coordinates and distances are float32 in memory, accurate to about 0.4 m.

## Timetables and routing
- **Service-day window:** a day's trips plus the previous day's trips running past midnight (GTFS times over 24:00) plus the next day's trips starting before 06:00.
- **Day cache:** each city's timetable is loaded lazily per service day and cached for 2 dates.
- **Direct lines only:** RAPTOR with one round (`max_transfers: 0`), so there is one code path instead of a separate direct-line search.
- **arrive_by:** the same RAPTOR code runs on a time-reversed copy of the day's timetable.
- **Alternatives:** each option is the earliest arrival, tightened by a reverse search to the latest departure that still makes it. The next option searches from just after that departure. This gives up to 3 distinct options with no needless waiting.
- **Overtaking trips** are split into separate FIFO groups, as RAPTOR requires.
- **Transfers:** precomputed foot transfers between stops up to 400 m along the walking graph, plus a 60 s minimum change time. Access and egress walks: up to 15 min, widened to 30 min if no stop is reachable.
- **Waiting cap:** options leaving more than 4 h after `depart_at` (or arriving more than 4 h before `arrive_by`) are dropped and reported as "no connection".
- **Walking option:** a walk-only itinerary is offered when the walk is ≤1.2 km or faster than every transit option.
- **Leg geometry:** each stop is placed on the GTFS shape in stop order. It is projected onto the shape's segments, and a small dynamic programme keeps positions from going backwards. Snapping each stop to its nearest shape vertex drew a terminus loop into a one-stop M32 ride (found by a manual test trip; regression test in `tests/test_timetable.py`).
- **Walk first when transit barely helps:** a walk of up to 2.5 km is listed first when the fastest transit option saves less than 5 minutes, with a note saying so.
- **Line colours:** GTFS `route_color`. White or missing falls back to a mode default. OSM route colours are not used: simpler, and they go stale.
- **Pickup/drop-off:** `pickup_type=1` / `drop_off_type=1` are respected in departures and routing. The first stop never allows alighting and the last never allows boarding.

## Live data
- **Opt-in per city** (`--live gdansk` or `POLAND_TRANSIT_LIVE`). It is never on by default, never cached, never proxied.
- **Never invents departures:** a live estimate is attached only to a matching scheduled departure (same stop, line and scheduled minute). Delayed vehicles are matched against the past hour of the schedule.
- **Kraków GTFS-RT skipped:** decoding protobuf needs a third runtime dependency or a hand-written decoder, and live data is optional.

## Output
- **Coordinates:** `[lat, lon]` rounded to 5 decimals. Times are local Europe/Warsaw wall-clock `HH:MM`, with the date added when it differs from the query's date.
- **Provenance:** every response carries `source` (attributions and licences) and `as_of` (OSM replication timestamp; each feed's publication, retrieval and validity dates; live `lastUpdate`).
- **Operator:** each departure, line and transit leg carries `operator`, the feed's `agency_name` with whitespace normalised. It is reported as published: some feeds name the transport authority (ZTM Gdańsk, ZTP Kraków trams) rather than the company that drives the vehicle, and the server doesn't fill that in from elsewhere.
- **"Processed" notice:** answers with timetables say the feeds were merged and simplified by this project and that the operator should be checked for changes. Some feed terms ask for this (ZTM Poznań's do), and it is true for all of them.
- **Stop names ending in a number:** a query matches the full name first ("Dworzec st. 17"). Only if nothing matches is a trailing two-digit number treated as a platform ("Oliwa PKP 02").
- **Name folding:** case, diacritics and punctuation are dropped, and common Polish abbreviations are unified (`plac`/`pl.`, `aleja`/`al.`, `osiedle`/`os.`, `ulica`/`ul.`, `świętego`/`św.`), so "Plac Grunwaldzki" matches Wrocław's `PL. GRUNWALDZKI`.
- **find_places search:** `LIKE` over a folded-name column. With ~8–16 k places per city a scan takes under 5 ms, so there is no FTS index. Places with a Wikipedia tag rank higher.
- **Stop grouping:** `find_places` merges poles of the same name within 400 m into one result listing all `stop_ids`. `departures` accepts a stop id or an exact stop name (all poles).
- **map_extract size:** simplification is ~1/400 of the bbox diagonal (min 3 m). Polygons smaller than 3× that are dropped, footpaths are left out unless asked for, and lines and polygons are grouped per layer. At most 4,000 points, filled in priority order: water, major streets, rail, tram, stops, parks, minor streets, paths. Cut layers are named in `note`. Measured: 43 KB for a 1 km box, ~83 KB at the cap.
- **render_map labels:** trip endpoints are placed first. Each label tries four positions around its point and takes the first that stays in the frame and overlaps no earlier label.
- **render_map size:** detail scales with the scale. Footpaths below 3 m/px, minor streets below 5 m/px, tiny polygons dropped, integer pixel coordinates. A city-wide trip renders to ~100 KB of SVG. Transit legs follow the GTFS shape between the boarding and alighting stops.

## Runtime and packaging
- **Python:** the MCP Python SDK, numpy and scipy cover the server and the build.
- **Runtime dependencies:** the official MCP Python SDK (`mcp`), plus `numpy` and `scipy`, plus `sqlite3` from the standard library.
- **Why scipy:** pure-Python Dijkstra over a million-node walking graph is too slow for the 300 ms budget. scipy's C implementation handles every walking search, and a grid index (sorted cell keys) snaps points to the graph. RAPTOR stays in plain Python over compact `array('i')` columns, which is fast enough.
- **A vector database was considered and rejected:** routing needs exact graph search, and spatial lookups need exact R*Tree queries. Embeddings would add a model and its download or API key for no gain.
- **Graph search:** single-source Dijkstra with a radius that starts at about twice the straight-line distance and doubles until the target is reached.
- **Memory:** walking-graph coordinates are float32; trip times are int32 arrays rather than lists of Python ints; a city keeps at most 2 service days cached. Together these cut RSS with 3 cities from 576 MB to 401 MB (measured).
- **Packaging:** `pyproject.toml` with hatchling; dependencies are lower-bounded, not locked, and `uv.lock` is not committed. `uvx` will be the one-command start (the Python equivalent of `npx`) once prebuilt data is published.
- **Cities load on demand, within a memory budget** (`pool.py`, `--memory-budget`, default 480 MB):
  - Every city stays open, which costs a few MB. Its walking graph and timetable load on its first query.
  - When the estimate passes the budget, the least recently used city is unloaded, and it reloads when asked again (about 4 s for Warszawa).
  - Before a city loads, room is made for its expected size (1.3 × its file size).
  - A call pins the cities it uses, so a city is never unloaded during a call.
  - The budget is for the whole process. A measured allowance is set aside for Python and for memory that queries leave behind before it is returned: 230 MB by default, 150 MB in the opt-in mode below. City data gets the rest.
  - Chosen over contracting the walking graph or splitting cities, because memory then depends on the cities in use, not on how many exist. Intercity trips need exactly this: two cities plus the national layer.
- **The default budget stays 480 MB; the server names the budget the cities need instead.** With Gdańsk, Warszawa and Katowice interleaved, 480 MB reloaded a city on two thirds of the queries (p95 1.7 s) and 600 MB on none (p95 93 ms). Raising the default would break a 512 MB container; sizing it from `--cities` silently would do the same for `--cities all`. A one-line hint at start (`To keep them all: --memory-budget 600`) leaves the choice visible.
- **Returning large frees immediately is opt-in, not the default** (`POLAND_TRANSIT_MMAP_THRESHOLD=1048576`, which sets `mallopt(M_MMAP_THRESHOLD)`):
  - With it, a walking search's ~20 MB arrays go back to the OS when freed. Memory with Warszawa and Katowice loaded fell from 498 to 386 MB.
  - But every search then gets fresh pages from the OS. Gdańsk trip planning averaged 88 ms with it against 69 ms without, measured three times alternately.
  - Speed won the default; the larger allowance (230 MB instead of 150) keeps the budget honest.
- **Freed memory goes back to the OS:** after a burst of tool calls, a background thread calls glibc's `malloc_trim` (`memory.py`, a no-op elsewhere). Without it, RSS with the three largest cities reached 535 MB, although the data in use was smaller. Calling it before each answer would have added ~40 ms to Warszawa's p95, so it runs off the response path.
- **Rust for the search only, as an optional package** (`engine/`, `poland-transit-mcp-engine`):
  - Measured, the RAPTOR search was 85 % of `plan_trip` time in a profile of 40 Warszawa plans with the Python search. A line-for-line Rust port was 17–21× faster per search with the same answers; a whole `plan_trip` is 2.3–3.4× faster (total time over 100 plans, Apple Silicon Mac, [docs/CHECKS.md](docs/CHECKS.md)).
  - The MCP server, tools and data pipeline stay Python. The built-in Python search remains the fallback, so a plain install never needs Rust.
  - PyO3 with the abi3 ABI gives one wheel per platform for all CPython ≥ 3.11. The search releases the GIL.
  - The engine gets each day's tables once, as flat little-endian arrays, and validates their lengths and index ranges.
- **Ties broken in reach order:** the Python search keeps stops improved in a round in insertion order (a dict) instead of a set. Equally fast connections are then chosen the same way every time, and the same way as the engine, so answers match exactly.
- **The engine is a local uv source until it is on PyPI** (`[tool.uv.sources]`). Otherwise the `[fast]` extra breaks `uv sync`, because uv resolves every extra. PyPI installs ignore it; uv installs from git or a local path build the engine from `engine/` (needs Rust).
- **Publishing is manual** (`engine-wheels.yml`, run by hand with "publish" ticked; PyPI Trusted Publishing, no secrets). A dry run builds every platform and tests the Linux wheel against the server first.
- **Trips between cities: a national layer joined at stations** (`intercity.py`, [docs/INTERCITY.md](docs/INTERCITY.md)):
  - Trains and coaches are one more timetable file, `poland.sqlite`, in the same format as the cities. City files stay unchanged, and one process holds both ends and the national layer.
  - The cities are joined at the national layer's stations inside each city's area, reached on that city's walking graph. One search with no target in each layer seeds the next, so a trip costs a handful of RAPTOR runs, not one per station.
  - Change margins are estimates: 5 min at a station between city transport and a train, and 5 min between trains. No minimum connection times are published. Answers list them under `assumptions`.
  - FlixBus's European feed is cut to its Polish stops by `stop_timezone`, not by a drawn border: the feed states each stop's time zone. Its UTC times are converted per date.
  - Platform and track are kept per stop time (98 % and 96 % of the trains feed), in a table read only for the legs shown. Each platform is also its own stop, so a change of platform inside a station is a transfer of 0 m.
  - `poland.sqlite` is not published yet: Koleje Mazowieckie's reuse terms are still to be read. PKP PLK's terms allow it with credit and without resale.
- **OpenTelemetry, opt-in, on the standard variables** (`telemetry.py`, the `otel` extra):
  - The MCP SDK already opens a span per request and continues a client's trace. The server adds spans inside it rather than a second, separate trace.
  - Only `opentelemetry-api` is a dependency. It is a no-op until the SDK is installed and an `OTEL_*` variable asks for export, so a plain install pays nothing.
  - Configured only through the standard `OTEL_*` variables, so every collector and hosted backend works without project-specific flags.
  - OTLP over HTTP, not gRPC: one small dependency and no compiled `grpcio`.
  - No place names, coordinates or query text, and no exception messages, which can quote them. Trips are locations.
  - Durations in seconds, as OpenTelemetry's conventions ask, with explicit buckets around the 300 ms and 1 s targets.
  - Logs stay plain stderr lines: the start-up and memory-budget lines are few, and traces carry the per-call detail.
- **Missing city files** are downloaded from the `data-latest` release on first start (`--no-download` turns this off), so a new user needs a single command once the data release is published.
- **Streamable HTTP** (`--http PORT`) is stateless and returns JSON responses, bound to 127.0.0.1.
- **Warm-up:** walking graphs and today's timetable load in a background thread right after the server reports ready, so the first query doesn't pay for them.
