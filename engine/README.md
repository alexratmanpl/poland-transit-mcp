# poland-transit-mcp-engine

An optional Rust search engine for [poland-transit-mcp](https://github.com/alexratmanpl/poland-transit-mcp).

- **What it is:** the RAPTOR trip-planning search, ported line for line from the package's Python code. Given the same timetable, it returns the same journeys.
- **Speed:** measured on Warszawa and Katowice, it is about 20× faster per search.
- **How it is used:** poland-transit-mcp uses it automatically when it is installed, and falls back to its own Python search when it is not. Set `POLAND_TRANSIT_ENGINE=python` to force the Python search.

The engine package is not published on PyPI yet, so `uvx --from "poland-transit-mcp[fast]"` from PyPI does not resolve: build it from source (a git URL or a local path, with a Rust toolchain). From a clone (needs a Rust toolchain, e.g. `brew install rust`) this works:

```bash
uvx --from "/path/to/poland-transit-mcp[fast]" poland-transit-mcp --data-dir /path/to/poland-transit-mcp/data/cities --no-download
# or, for development:  uv sync --extra dev --extra fast
```

Whole `plan_trip` calls are 2.3–3.4× faster (total time over 100 plans on an Apple Silicon Mac), not 20×: the 20× is the search step alone. See the main README.

Licence: MIT.
