# Publishing to PyPI (optional)

Nothing here is required. The install from GitHub works without PyPI and then uses the built-in Python
search. It becomes one command once prebuilt city data is published; until then, pass the city files you
built with `--data-dir DIR --no-download`. Publishing adds two things: shorter install commands, and the fast Rust
engine without needing a Rust toolchain.

There are two packages:

| Package | What it is | Built by |
|---|---|---|
| `poland-transit-mcp` | the MCP server, tools and city builder (pure Python) | `uv build` |
| `poland-transit-mcp-engine` | the optional Rust search engine (`engine/`). Same answers, 2.3–3.4× faster `plan_trip` (total time on a Mac; 17–21× per search). | `.github/workflows/engine-wheels.yml` |

## How users install, before and after

| | Command | Search |
|---|---|---|
| Now, nothing on PyPI | `uvx --from git+https://github.com/alexratmanpl/poland-transit-mcp poland-transit-mcp --data-dir DIR --no-download` (with city files you built; prebuilt files are not published yet) | Python |
| Now, with a Rust toolchain on the machine | add `--with "poland-transit-mcp-engine @ git+https://github.com/alexratmanpl/poland-transit-mcp#subdirectory=engine"` | Rust (compiled on install, ~1 min) |
| Engine on PyPI | add `--with poland-transit-mcp-engine` | Rust (prebuilt wheel, no toolchain) |
| Both on PyPI | `uvx --from "poland-transit-mcp[fast]" poland-transit-mcp` | Rust |

The server falls back to the Python search whenever the engine is not installed. It names the search
it uses at start-up (`search engine: rust <version>` or `python`) and in `list_cities`.
`POLAND_TRANSIT_ENGINE=python` forces the Python search.

## One-time setup (maintainer)

1. **PyPI account**, with two-factor authentication.
2. **Trusted publisher for the engine.** On PyPI: *Your projects → Publishing → Add a new pending publisher*. Enter:
   - project `poland-transit-mcp-engine`
   - owner `alexratmanpl`
   - repository `poland-transit-mcp`
   - workflow `engine-wheels.yml`
   - environment `pypi`

   This lets that one workflow publish that one project, with no token stored anywhere.
3. **GitHub environment.** In the repository: *Settings → Environments → New environment* `pypi`. Add yourself as a required reviewer if every upload should wait for your approval.

## Publishing the engine

1. **Dry run.** *Actions → engine-wheels → Run workflow* with "publish" **unticked**.
   - It builds wheels for Linux (x86-64, ARM), macOS (Intel, Apple Silicon), Windows (x64), plus the source package.
   - It installs the Linux wheel next to the server and runs the test suite, including the check that Rust and Python give the same plans.
2. **Publish.** If everything is green, run it again with "publish" **ticked**.
3. **Check.** On any machine with city files you built: `uvx --from git+https://github.com/alexratmanpl/poland-transit-mcp --with poland-transit-mcp-engine poland-transit-mcp --data-dir DIR --no-download`. The start-up line should say `search engine: rust`. (Without city files the server stops before that line.)

## Publishing the server (optional)

`uv build`, then `uv publish`. You can use a PyPI token, or add a trusted publisher and a workflow like
the engine's. With both packages on PyPI, the `[fast]` extra in `pyproject.toml` pulls in the engine.

## What was checked before this was written

See [CHECKS.md](CHECKS.md), "Rust engine". In short:
- The wheel builds natively, and also as manylinux2014 (the widely compatible Linux format).
- It installs into a clean environment, and from source with a Rust toolchain.
- `[fast]` resolves when the engine is offered from a package index (simulated with `--find-links`).
- Answers are identical to the Python search on 400 real trip plans and on every acceptance query.
- Uninstalling the engine falls back to the Python search.
- **Not checked:** the macOS and Windows wheels. They can only be built on those systems; the workflow's dry run does that.
