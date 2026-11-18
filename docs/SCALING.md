# Scaling: more and bigger cities

Measured on 2026-11-16 on the 4-vCPU cloud VM used for [CHECKS.md](CHECKS.md). Run-to-run noise on this VM
is about ±15 % for latency. Numbers are *measured* unless marked *estimated*.

## What a city costs

Each city is one SQLite file. In memory, a loaded city holds its walking graph and the timetable of
the days it was asked about (both measured, with freed memory returned to the OS):

| City | Stop extent | File | Walking graph | Timetable, one day + routing tables | Trips that day |
|---|---|---|---|---|---|
| Zakopane | 441 km² | 5.7 MB | 6 MB | 1 MB | 181 |
| Toruń | 866 km² | 22.9 MB | 15 MB | 8 MB | 3,154 |
| Gdańsk | 1,117 km² | 35.6 MB | 24 MB | 21 MB | 7,862 |
| Wrocław | 1,306 km² | 45.7 MB | 31 MB | 35 MB | 14,175 |
| Poznań | 3,895 km² | 55.9 MB | 37 MB | 38 MB | 13,320 |
| Kraków | 2,178 km² | 77.0 MB | 48 MB | 35 MB | 17,997 |
| **Katowice (GZM)** | **5,292 km²** | **112.7 MB** | **76 MB** | **76 MB** | **25,104** |
| Warszawa | 3,978 km² | 129.9 MB | 80 MB | 79 MB | 39,114 |

Rules of thumb that fit all eight:
- **Walking graph:** about 44 bytes per node in memory.
- **Timetable:** about 2 KB per trip running that day.
- **File:** mostly map features and their spatial index.

Katowice, the largest network by area, came in at 112.7 MB, under the 150 MB file budget. The earlier
*estimate* was 130–180 MB.

## How the server stays within memory

Before 2026-11-16, every city given with `--cities` was loaded at start. Memory grew with every
city: Warszawa, Kraków and Poznań together reached 535 MB.

Now (`pool.py`):
- Every city is open, costing a few MB. Its graph and timetable load on its first query.
- `--memory-budget` (default 480 MB) is a target for the whole process. A measured allowance is set aside for Python and for memory that queries leave behind before it is returned to the OS. City data gets the rest:
  - **Default:** 230 MB set aside, so city data gets 250 MB of a 480 MB budget.
  - **Opt-in memory mode** (`POLAND_TRANSIT_MMAP_THRESHOLD=1048576`): large frees go straight back to the OS. Only 150 MB is set aside, so city data gets 330 MB, but trip planning is about 25 % slower (Gdańsk: 88 vs 69 ms on average, three alternating runs).
- Each city keeps `--days-kept` dates built (default 2; see [Dates kept per city](#dates-kept-per-city)).
- When the estimate passes the budget, the cached dates of the least recently used cities are dropped first (their least recently used date first, down to one date each); then, when a city has to load and still doesn't fit, the least recently used city is unloaded. It reloads on its next query: Warszawa takes about 4 s (graph 2.1 s, timetable 1.7 s), smaller cities 1 s or less; a dropped date takes 0.1-0.55 s to build again.
- A query pins the cities it uses, so none is unloaded, and none of its dates dropped, in the middle of a call.
- To see whether the budget fits your cities, turn on OpenTelemetry: a `poland_transit.city.unloads` count that keeps rising while you use the same cities means they keep reloading ([docs/OBSERVABILITY.md](OBSERVABILITY.md)).

Measured with `--memory-budget 480`:

| Cities in one process | Peak memory | `plan_trip` p95 | First query when a city has to load |
|---|---|---|---|
| Gdańsk, Wrocław, Kraków | 368 MB | 169 ms | 120–183 ms |
| Warszawa, Katowice, Kraków (the largest) | 438 MB; Warszawa and Katowice unloaded in turn | Kraków 166 · Warszawa 370 · Katowice 417 ms | up to 1.9 s (Katowice) |
| All eight cities | **420 MB** | 16–200 ms for six cities · Warszawa 345 · Katowice 460 ms | up to 1.8 s (Katowice) |

Every run: 0 errors.

### Dates kept per city

A date is a day's trips plus the routing tables built for it (forward, and backward for `arrive_by`). Each city
keeps `--days-kept` dates (default 2), so a client planning many trips over a week can keep all seven instead of
building a date again a few calls later. Memory for one date with both routing directions, and the time to build it
(engine v0.4.0, Rust engine, Apple Silicon, measured 2026-11-18):

| Data set | Memory per date | Build |
|---|---|---|
| Warszawa | about 55 MB | 0.52-0.56 s |
| `poland` (national layer) | about 50 MB | 0.22-0.27 s |
| Kraków | about 20 MB | 0.20-0.22 s |
| Gdańsk | about 12 MB | 0.08-0.10 s |

- Seven Warszawa dates take about 385 MB, besides its walking graph and timetable structures: the number of dates kept is a hosting choice. The start-up line counts them (`expected_bytes`: each further date about 0.4 × the file size, 2.5 × for the national layer).
- The pool's estimate of a date (from its counted structures) runs under these measured numbers (Warszawa 41 MB, `poland` 23 MB): the 5 % margin of the whole-city estimate does not cover many dates, so leave room in `--memory-budget` when keeping more than a few.
- A date is built once even when several calls ask for it at the same time: the later calls wait for that build.
- `--warm-days` and `--warm-from` build dates at start: breadth-first over the cities, each date with both routing directions, stopping before a date would pass 70 % of the budget. Measured with Gdańsk, Kraków, `poland` and Warszawa, `--memory-budget 1000 --days-kept 3 --warm-days 3`: all twelve dates in 3.2 s. The warm-up is not repeated at midnight.

### Trips between cities

A trip between cities pins both cities and the national layer `poland` for the whole call. Measured
with Gdańsk, Kraków and Warszawa plus `poland` (`scripts/measure_intercity.py`, 120 random trips
between them, Rust engine):

| `--memory-budget` | Peak memory | `plan_trip` p50 / p95 | |
|---|---|---|---|
| 480 (default) | 532 MB | 234 / 2,583 ms | Warszawa and Kraków unloaded and reloaded throughout |
| 700 | 573 MB | 114 / 372 ms | everything stays loaded |

The server now says at start when the chosen cities need more than the budget to stay loaded
together, and what `--memory-budget` would keep them.

## What runs where

| Setting | What fits |
|---|---|
| Laptop (Claude Desktop / Claude Code, from a clone, or via `uvx` once data is published) | Everything. Raise `--memory-budget` to keep more cities loaded. |
| Container with 512 MB | One process for all cities at the default 480 MB budget. By default, one very large metro (Warszawa or Katowice, ~155 MB each) plus smaller cities stay loaded; switching between the two large ones reloads (~2–4 s). In memory mode, both large ones fit together, at ~25 % slower trip planning. |
| Container with 1 GB | `--memory-budget 900`: about four large cities stay loaded at once, so reloads become rare. |
| Building city files | Not the server's job. The largest build (Katowice) peaked at **3.9 GB** RAM and took 107 s; GitHub's standard Linux runners (7 GB RAM on private repositories, 16 GB on public ones, per GitHub's published runner specs; not measured) handle it. A 2 GB machine could not. |

All of Poland's ~12–15 metro areas would be about 1–1.2 GB of files (*estimated* from the table
above). That is fine on disk, and Releases serve one file per city.

## Limits found, and what would lift them

- **Trip planning speed on the largest networks: lifted by the optional Rust engine.**
  - With the Python search, `plan_trip` p95 was ~350–420 ms on Warszawa and ~400–470 ms on Katowice, over the 300 ms target. In a profile of 40 Warszawa plans, 85 % of the time was the pure-Python RAPTOR search.
  - The search now has a Rust implementation (`engine/`) with the same answers. Over MCP, `plan_trip` p95 is 91 ms on Warszawa and on Katowice, and 77 ms across all eight cities ([CHECKS.md](CHECKS.md), "Rust engine").
  - Without the engine, the Python numbers above still apply.
- **Build memory (3.9 GB).**
  - The build reads all ways into Python objects.
  - Streaming the graph build would bring it down if builds must run on small machines.
- **Walking graph size.**
  - If memory gets tighter, nodes along unbranched paths can be merged (*estimated* ~60 % fewer nodes).
  - That needs snapping to edges instead of nodes, so it is left until needed.
