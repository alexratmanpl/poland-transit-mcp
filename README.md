<div align="center">

# poland-transit-mcp

**An MCP server for getting around Polish cities: places, walking routes, timetables and door-to-door trip plans.**<br>
Built on OpenStreetMap and the operators' official GTFS timetables. Every answer names its sources and data dates.

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-2a78d6)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![MCP server](https://img.shields.io/badge/MCP-server-6250d6)](https://modelcontextprotocol.io)

<img src="docs/demo-plan-trip.svg" alt="plan_trip answering a trip in Gdańsk, in a terminal" width="760">

</div>

## What it does

- **Finds places:** attractions, museums, parks, stations, stops, hotels and streets, by name or near a point.
- **Walks:** routes along mapped streets and paths that never cross water except on a bridge.
- **Reads timetables:** departures and lines from each operator's GTFS feed, live estimates in Gdańsk (opt-in).
- **Plans trips:** up to 3 door-to-door options, leaving at or arriving by a time, also between cities by train or FlixBus.
- **Draws maps:** a PNG of an area or of a planned trip.
- **Answers lean and typed:** every tool declares its answer shape (`outputSchema`); `detail: "full"` adds what only code needs.

## Cities and data

| | |
|---|---|
| **Cities** | Warszawa · Kraków · Katowice (GZM) · Wrocław · Poznań · Gdańsk · Lublin · Toruń · Zakopane |
| **Between cities** | `poland`: trains of 13 rail operators and FlixBus coaches |
| **Data** | one SQLite file per city, built by you from the official sources (not in git; no prebuilt download yet) |

## Quick start

Needs [uv](https://docs.astral.sh/uv/), `osmium-tool` and a Rust toolchain (macOS: `brew install uv osmium-tool rust`).
No Rust? Leave out `--extra fast`: the built-in Python search gives the same answers, more slowly.

```bash
git clone https://github.com/alexratmanpl/poland-transit-mcp.git && cd poland-transit-mcp
uv sync --extra dev --extra fast                      # --extra fast: the optional Rust engine
uv run poland-transit-mcp build gdansk --download     # about 2 min; writes data/cities/gdansk.sqlite
uv run python scripts/smoke.py                        # expect: PASS
claude mcp add poland-transit -- "$PWD/.venv/bin/poland-transit-mcp" --cities gdansk --data-dir "$PWD/data/cities" --no-download
```

More cities, trips between cities, other MCP clients, memory, every flag and troubleshooting: **[AGENTS.md](AGENTS.md)**.

## Tools

| Tool | Answers |
|---|---|
| `list_cities` | cities and national layers, their features and timetable dates |
| `find_places` | places by name or near a point |
| `stops_near` | stops by walking time, with the lines serving each |
| `departures` | the next departures from a stop; with `to`, only the runs that go on to another stop |
| `line_info` | a line's stops, first and last run and frequency; on `poland`, a train |
| `walk_route` | a walking route between two points |
| `plan_trip` | door-to-door itineraries; with `to_city`, between cities |
| `plan_stations` | trains and coaches between stations, for a caller that brings the traveller to a station |
| `map_extract` | map geometry of an area, for code that draws its own map |
| `render_map` | a map picture of an area or of a planned trip |
| `credits` | the full credit and licence text of each data source |

Arguments, declared answer fields, errors and answer sizes: **[docs/TOOLS.md](docs/TOOLS.md)**.

> **Serving it to others?** `--http` has no authentication, rate limit or time limit per call: put it behind a
> gateway or a buffering reverse proxy ([SECURITY.md](SECURITY.md#running-it-for-others)). Cutting a model
> client's token cost with "code mode": [SECURITY.md](SECURITY.md#model-clients-and-token-cost).

## Documentation

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | from an empty machine to a working MCP server; code map, troubleshooting, flags |
| [docs/TOOLS.md](docs/TOOLS.md) | every tool: arguments, answer fields (`outputSchema`), lean-by-default rule, errors, sizes |
| [docs/ENGINE.md](docs/ENGINE.md) | the optional Rust engine: why, how fast, how to install |
| [docs/INTERCITY.md](docs/INTERCITY.md) | trips between cities: how they are planned, measurements, limits |
| [docs/SCALING.md](docs/SCALING.md) | what each city costs, memory budget, what runs on what hardware |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | opt-in OpenTelemetry traces and metrics |
| [docs/CHECKS.md](docs/CHECKS.md) | accuracy against operators' timetables, speed and memory measurements |
| [docs/PUBLISHING.md](docs/PUBLISHING.md) | releasing both packages to PyPI (one workflow, Trusted Publishing) |
| [SECURITY.md](SECURITY.md) | what the server trusts, running it for others, reporting a vulnerability |
| [docs/postmortems/](docs/postmortems/) | what went wrong, why our process let it through, what changed |
| [SOURCES.md](SOURCES.md) · [DATA-LICENSE.md](DATA-LICENSE.md) | every feed with its credits · data licences and redistribution |
| [DECISIONS.md](DECISIONS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) | design choices · contributing · releases |

## Licence and credits

Code: MIT. City files: ODbL 1.0 (© OpenStreetMap contributors) for the map, and each timetable keeps its publisher's
terms; Zakopane's timetable and `poland` are not cleared for redistribution ([DATA-LICENSE.md](DATA-LICENSE.md)).
Timetables from the cities' transport authorities, PKP PLK, Koleje Mazowieckie and FlixBus; GTFS conversions by
Mikołaj Kuranowski ([full credits](SOURCES.md#credits-at-a-glance)).
