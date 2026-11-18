# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed
- **One answer rule on every tool**, stated in the server's instructions and in [AGENTS.md](AGENTS.md#lean-by-default-more-on-demand): the default answer holds what a person asking needs and every id another tool takes; `detail: "full"` adds every field code needs, `limit` more rows, filters narrow the question; an answer that was cut says so in `note`, with how to get the rest.
- Each tool's `detail` says what `full` adds there and about how much larger the answer gets (measured on real data: `departures` about 1.4 times, `line_info` 3 to 6, `walk_route` 3 to 4, `plan_trip` 2 to 3, `plan_stations` 5 to 9), instead of one sentence shared by five tools that was wrong for two of them.
- Every argument has a description: `limit`, `max_transfers`, `max_walk_min` and a station's `minutes` had none. `render_map`'s `size` says a picture's cost grows with the square of its side.
- `find_places` says in `note` when more places matched than it lists, and how to narrow the question or raise `limit`; `stops_near` says when more platforms than its 20 are within the walk, and points to `find_places`.
- **Breaking:** `render_map` takes a trip one way, its `trip` reference: `itinerary` is gone. A call that passes it is refused with an error that says to pass the option's `trip`. What a model loads of the tool list is about 100 characters smaller although every argument now has a description.

### Added
- `departures` takes `mode` (`tram`, `bus`, `trolleybus`, `metro`, `rail`, `ferry`): "the next trams" from a stop that buses also serve is one call.

### Fixed
- Several calls at the same time (over `--http`) no longer fail with "could not answer this request". Each city file had one SQLite connection that every worker thread used at once; their statements interleaved and raised `InterfaceError` (32 of 60 `departures` calls, fired 20 at a time on a national rail layer). Each statement now runs to its last row under the connection's lock. Calls that arrive together at an unloaded city load it once instead of once each. Caches of loaded days and shapes, and a city's timetable while the memory budget unloads it, are read safely from several threads too.

## 0.3.0 - 2026-11-18

### Changed
- **Answers carry credit ids, not credit text:** `source` (the full credits, repeated in every answer) is replaced by `credits`, a list of ids (`osm`, `<city>/<feed key>`, `<city>/live`). The full text comes from the new `credits` tool, the `credits://all` resource, or `poland-transit-mcp credits`. A feed that is not available shows in `as_of`.
- Unknown arguments are refused with an error that names them (they were ignored: `departures` with `date` answered for now).
- `line_info` leaves out line geometry unless `detail: "full"` and keeps the 10 directions with the most trips.
- `render_map` draws a trip longer than 18 km from its start, with an arrow to its end, instead of failing; the PNG now follows the text block and is at most 2,576 px on its longest side.
- `walk_route` gives `from`/`to` as `{name, at}` also when no route is found, and its path only with `detail: "full"`.
- Trip references read like a URL query (`t2?c=…`), half the tokens of the base64 form, and trips between cities have one too.
- `render_map`: `size` (the longer side) replaces `width`; a PNG stays within 4,784 patches of 28 px; a bbox outside the city, unknown `style` keys and values that are not colours are refused.
- `map_extract`: shapes are cut at the box and detail is reduced until the layers fit `max_points` (default 1,000, about 20,000 characters); a layer that still does not fit is left out whole and named.
- `list_cities`: each feed names its `credit` id instead of its licence and credit text; `in_memory` and `search_engine` are gone.
- Tools have titles; argument errors are one line; `null` means "not given" for every argument.
- The server's instructions name the loaded cities (the `city` ids) and say which tool answers which question, so a session needs no `list_cities` call first. Tool descriptions are shorter; every bound and every field left out by default is still described.
- A name the data does not have is refused with the names that are close: places (`closest: Kopiec Kościuszki (landmark), …`), stops (`stops with that in their name: Teatr Bagatela`) and lines (`it has tram: 1, 3, 5, …`).
- The `credits` tool gives each id's `text` and the notice only; `name`, `licence`, `city` and `about` stay in the `credits://all` resource and the published file. It also takes city ids (`["krakow"]`: every source of that city), so it can be called alongside the first data call. Its note about unknown ids names at most five, each cut to 80 characters.
- `plan_stations` says that its options are the best connections, not every train.
- `find_places` on a national layer no longer repeats a `use` sentence in every station.

### Fixed
- Two cities with a feed of the same key (e.g. Kraków's and Toruń's `T`) reported one data date for both; such keys are now per city (`gtfs_<city>_<key>`).

### Added
- `departures` takes `to` (another stop or station): only the runs that go on there, each with its arrival time (`arrive`). "Which trains go from Kraków to Warszawa between 6 and 9" is one call instead of a board and one `line_info` per train.
- `list_cities` gives each data file's build time (`built_at`, UTC), so a caller can tell how old the loaded data is.
- Smaller answers for agents: `plan_trip` and `plan_stations` leave out line geometry unless `detail: "full"` (Praha → Kraków on `plan_stations`: about 1,050 tokens instead of 5,750); each itinerary in one city carries a short `trip` reference; `render_map` takes it (`trip`) and returns a PNG image by default (a trip map: about 1,060 image tokens and 235 text tokens instead of about 32,000 tokens of SVG text). `output: "svg"` keeps the SVG. PNG rendering uses `resvg` (MIT), now a dependency.
- Every tool declares the shape of its answer (`outputSchema`) and returns it as `structuredContent`, with the same compact JSON as text as before. Clients that generate code from tool definitions (for example Cloudflare's Code Mode) get typed results.
- `--host ADDR` (or `POLAND_TRANSIT_HOST`) for `--http`: bind another address than `127.0.0.1`, e.g. `0.0.0.0` in a container. There is still no authentication; the server warns at start when it is not on loopback.
- FlixBus trips that serve Poland are kept whole as far as their stops keep Polish time (Czechia, Germany, Austria, …), so Praha → Wrocław and Brno → Wrocław coaches appear as one leg. Stops on another clock are still dropped. The feed's name is now "FlixBus Europe GTFS (trips serving Poland)", which changes its credit text.
- Data sets from a definitions file: `build ID --datasets FILE` (or `POLAND_TRANSIT_DATASETS`) builds a data set that is not in `config.py`, for example another country's trains as a second national layer. The file states its time zone; the build refuses one whose clock differs from Polish time (e.g. `Europe/Prague` is accepted), and the server refuses such a file. Name, time zone, notes and credits are written into the data file, so the server needs no definitions file.
- National layers are found by their kind, not by the id `poland`. `plan_trip` (with `to_city`) and `plan_stations` take `layer` to choose one when several are loaded; `list_cities` shows each layer's time zone.
- `plan_stations`: trains and coaches of the national layer between stations, from several start stops to several end stops, each with its own minutes (from `depart_at` until the traveller is ready to board there; from arriving there until the trip is over). It adds no walk or city transport, and each option names the stops and minutes it used. For a caller that brings the traveller to a station itself, for example from another country's timetable.
- Build safeguards: a rebuild keeps the current city file when a feed that was `ok` there is now missing, broken or
  skipped, or stores less than half its runs (the new file is kept as `<city>.sqlite.rejected`, exit code 3; the new
  `--force` replaces it anyway). After writing, it names feeds that are not `ok` or end within 14 days, and repeats
  failed downloads. The `city-data` workflow starts from the published file, so a worse build is never uploaded. Each
  feed's record in the city file now holds its number of stored runs (`runs`).
- Lublin: ZDiTM Lublin buses and trolleybuses, from the GTFS conversion at mkuran.pl (CC0 1.0), with the `lubelskie` map. In the `city-data` workflow's build matrix.

### Fixed
- A plan's `depart_at`/`arrive_by` after midnight reads as one date and time (`2026-11-19 01:10`, was `2026-11-18 2026-11-19 01:10`).
- Error messages keep their own leading quote (`"Alfa" matches several stops`, was `Alfa" matches…`).
- Stop names with a house or school number lost it as a "platform" ("Bierutowska 65" in Wrocław, "Gołonóg Szkoła
  Podstawowa nr 11" in GZM). A trailing two-digit number is now a platform only in a feed whose names nearly all end
  in one (ZTM Gdańsk), or when it is zero-padded or shared by three or more stops of the same name ("GALERIA
  DOMINIKAŃSKA 10"), and never after "nr".
- Gdańsk's credit gives the publisher's address over https (`https://www.ztm.gda.pl/otwarty_ztm`; its terms print the
  same address with http).
- Downloads failed with `CERTIFICATE_VERIFY_FAILED` on some publishers' servers (Wrocław's and Poznań's open-data
  portals) with Python builds whose default trust store lacks their root (for example uv's standalone Python on macOS).
  Every download now trusts the system's roots plus Mozilla's root list from `certifi` (a new dependency); nothing is
  removed, so a root added to the system (a company proxy) keeps working. When a missing root is the likely cause, the
  build and `download` say how to fix it; an expired or wrong-host certificate is reported as rejected.
- FlixBus stop names keep their trailing numbers ("Kostrzyn nad Odra, Dworcowa Street 22 and 17" was stored as "… 22 and" with platform 17). The split of a trailing platform number off a stop name is now a per-feed setting (`platform_in_name`), on by default and off for FlixBus.

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
