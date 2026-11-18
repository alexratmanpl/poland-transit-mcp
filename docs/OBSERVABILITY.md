# Observability: traces and metrics with OpenTelemetry

The server can send traces and metrics to any OpenTelemetry backend: the OpenTelemetry Collector,
Jaeger, Grafana Tempo and Mimir, Honeycomb, Datadog and others. It is **off unless you turn it on**,
and costs nothing when off. Everything here was measured on 2026-11-16 (`opentelemetry-sdk` 1.45.2,
MCP SDK 2.3.0).

## Turn it on

Install the `otel` extra in your clone and point the standard OpenTelemetry variables at your collector
(city files built as in [AGENTS.md](../AGENTS.md) step 4):

```bash
uv sync --extra dev --extra otel      # add --extra fast if you use the Rust engine; uv sync drops extras you leave out
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run poland-transit-mcp --cities gdansk --no-download
```

In Claude Code (from the clone root):

```bash
claude mcp add poland-transit -e OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 -- \
  "$PWD/.venv/bin/poland-transit-mcp" --cities gdansk --data-dir "$PWD/data/cities" --no-download
```

Once prebuilt city files are published, `uvx --from "poland-transit-mcp[otel] @ git+https://github.com/alexratmanpl/poland-transit-mcp" poland-transit-mcp`
will work the same way.

The start-up log says what happened:

```text
[poland-transit-mcp] telemetry: traces→otlp, metrics→otlp (http://localhost:4318)
```

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Sends traces and metrics over OTLP/HTTP to this collector |
| `OTEL_TRACES_EXPORTER=console` | Prints spans to **stderr** (stdout carries the MCP messages over stdio, so nothing is ever written there) |
| `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER=none` | Turns one of the two off |
| `OTEL_METRIC_EXPORT_INTERVAL` | Milliseconds between metric exports (default 15000) |
| `OTEL_SERVICE_NAME` | Service name (default `poland-transit-mcp`) |
| `OTEL_SDK_DISABLED=true` | Everything off |
| `OTEL_EXPORTER_OTLP_HEADERS` | Headers for a hosted backend's key. Keep it in your MCP client's settings, never in the repository |

Without the extra, the server logs `OTEL_* is set but the OpenTelemetry SDK is not installed` and
runs normally.

## What a trace shows

The MCP SDK opens one span per request (`tools/call plan_trip`). It continues the client's trace
when the request's `_meta` carries a W3C `traceparent`. Everything the server does for that request
nests under it, across the worker thread the tool runs in.

A real trace: the first `plan_trip` in Gdańsk after start, with the Rust engine:

```text
tools/call plan_trip ............................. 819 ms   (MCP SDK)
└─ tool plan_trip ................................ 818 ms   city=gdansk, outcome=ok, response 13.8 kB
   ├─ city.load  timetable ....................... 387 ms   1,627 stops
   ├─ city.load  graph ...........................  79 ms   23 MB walking graph
   ├─ walk.search ×2 .............................   2–3 ms each, radius ~1.2 km
   ├─ city.load  day .............................  146 ms  7,862 trips that day
   ├─ city.load  routing_tables  forward .........  105 ms  570 patterns
   ├─ routing.search  rust  forward ..............  0.7 ms  31 start stops → 2 end stops, 1 journey
   ├─ city.load  routing_tables  backward ........   56 ms
   ├─ routing.search ×5 ..........................  0.1–0.7 ms each
   └─ walk.search ×3 .............................   2–3 ms each
```

This first call spent 773 of its 818 ms loading the city; the trip searches took under 3 ms in all.
The next call in Gdańsk has no `city.load` spans. When the memory budget unloads a city to make
room, the tool span gets a `city.unload` event naming the city and the memory freed.

| Span | Attributes |
|---|---|
| `tool <name>` | `poland_transit.tool`, `.city`, `.search_engine`, `.outcome` (`ok`, `rejected` for bad input or missing data, `busy` when refused by `--max-calls`, `error`), `.response.bytes`; `error.type` on failure |
| `city.load` | `.city`, `.city.part` (`graph`, `timetable`, `day`, `routing_tables`), and its size: `.bytes`, `.stops`, `.trips` or `.patterns` |
| `walk.search` | `.walk.radius_m` |
| `routing.search` | `.search_engine` (`rust` or `python`), `.direction`, `.routing.access_stops`, `.routing.egress_stops`, `.routing.max_rounds`, `.routing.journeys` |
| `warm_up` | the background preload after start; its `city.load` spans nest under it |

## Metrics

| Metric | Type | Attributes | Answers |
|---|---|---|---|
| `poland_transit.tool.duration` (s) | histogram | tool, city, search engine, outcome | p50/p95 per tool and city; the 300 ms target |
| `poland_transit.routing.search.duration` (s) | histogram | search engine, direction | how fast the search itself is, Rust against Python |
| `poland_transit.city.load.duration` (s) | histogram | city, part | what a cold city costs |
| `poland_transit.city.loads` | counter | city, part | how often cities load |
| `poland_transit.city.unloads` | counter | city | whether the memory budget is too small: steady unloads mean cities keep reloading |
| `poland_transit.memory.rss` (By) | gauge | | process memory (Linux) |
| `poland_transit.memory.city_data` (By) | gauge | | the pool's estimate of loaded city data |
| `poland_transit.memory.budget` (By) | gauge | | memory allowed for city data (`--memory-budget` minus the allowance) |
| `poland_transit.cities.in_memory` | gauge | | cities with their graph or timetable loaded |

Histogram buckets are in seconds, from 5 ms to 10 s (0.1 ms to 1 s for the search). The SDK's
defaults are made for milliseconds; with them, every call would have fallen in the first bucket.

**Is the memory budget right?** If `city.unloads` keeps rising while you use the same few cities,
and `memory.city_data` sits at `memory.budget`, raise `--memory-budget`. Over the budget, cached dates
are dropped before cities are unloaded: `city.loads` with part `day` rising for dates already asked
for means dates are built again (raise the budget, or lower `--days-kept`).

## Privacy

Users' trips are locations, so **no place names, stop names, coordinates or other query text are
recorded**: only tool names, city ids, counts, sizes, durations and outcomes.

- Spans never record exception messages, because those can quote the places asked about. They record the error type only. The MCP SDK does the same for tool errors.
- The tests check this. `tests/test_telemetry.py` plans trips by place name and by coordinates, makes one fail with an unknown place, and checks that none of it appears in any span, event or status.
- The end-to-end check below checks it again on the wire.

## What it costs

Measured with `scripts/measure.py` (600 random trips in Gdańsk, Wrocław and Kraków over MCP, Rust
engine, 4-vCPU VM), two runs each, alternating, exporting to a local OTLP receiver:

| | Telemetry off | Exporting over OTLP |
|---|---|---|
| `plan_trip` p50 | 21–22 ms | 23 ms |
| `plan_trip` p95 | 61–66 ms | 64–67 ms |
| Memory after the queries | 349–351 MB | 351–383 MB |
| Cold start (max of 5) | 806–837 ms | 881–899 ms |

So exporting adds 1–2 ms per trip plan, 40–90 ms at start (the SDK and exporter load), and up to
~30 MB of memory. Raise `--memory-budget` by about 30 MB when exporting. Turned off, the
instruments are OpenTelemetry's no-op proxies.

An earlier version counted the walking-graph nodes each search reached. That cost a pass over the
graph per search, +3–4 ms per trip plan, so it was removed; the search's duration tells the same.

**If the collector is down:** every call still succeeds. The exporter logs one
`Transient error … /v1/traces` line per attempt on stderr, and the server takes about 2 s longer to
exit while it gives up on the last batch.

## How it was checked

1. **Unit tests** (`tests/test_telemetry.py`, in-memory exporters): the span tree of a real
   `plan_trip`, the tool and load metrics, unload events with a tiny budget, the memory gauges,
   bucket bounds in seconds, no place names or coordinates, and that it is off without
   configuration.
2. **End to end over stdio:**
   - The real server, with the Gdańsk and Kraków files and the Rust engine, exported over OTLP/HTTP to a local receiver that decodes the protobuf.
   - Three trip plans by place name and one bad request gave four traces, nine metrics, and the unload events the 330 MB budget caused.
   - None of the place names appeared anywhere in the export.
3. **Console exporter over stdio:** the MCP client parsed every reply while spans went to stderr.
4. **Install:**
   - `uvx --from "poland-transit-mcp[otel] @ …"` turned telemetry on.
   - The same install without the extra logged the missing-SDK line and ran normally.
