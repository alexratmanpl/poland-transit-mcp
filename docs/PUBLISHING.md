# Publishing to PyPI (optional)

Nothing here is required. The install from GitHub works without PyPI and then uses the built-in Python
search. It becomes one command once prebuilt city data is published; until then, pass the city files you
built with `--data-dir DIR --no-download`. Publishing adds two things: shorter install commands, and the fast Rust
engine without needing a Rust toolchain.

There are two packages:

| Package | What it is | Built by |
|---|---|---|
| `poland-transit-mcp` | the MCP server, tools and city builder (pure Python) | `.github/workflows/release.yml` |
| `poland-transit-mcp-engine` | the optional Rust search engine (`engine/`). Same answers, 2.3–3.4× faster `plan_trip` (total time on a Mac; 17–21× per search). | `.github/workflows/release.yml` |

## How users install, before and after

| | Command | Search |
|---|---|---|
| Now, nothing on PyPI | `uvx --from git+https://github.com/alexratmanpl/poland-transit-mcp poland-transit-mcp --data-dir DIR --no-download` (with city files you built; prebuilt files are not published yet) | Python |
| Now, with a Rust toolchain on the machine | add `--with "poland-transit-mcp-engine @ git+https://github.com/alexratmanpl/poland-transit-mcp#subdirectory=engine"` | Rust (compiled on install, ~1 min) |
| Engine source on PyPI (the first upload) | add `--with poland-transit-mcp-engine` | Rust (built from source on install: needs a Rust toolchain) |
| Engine wheels on PyPI | add `--with poland-transit-mcp-engine` | Rust (prebuilt wheel, no toolchain) |
| Both on PyPI | `uvx --from "poland-transit-mcp[fast]" poland-transit-mcp` | Rust |

The server falls back to the Python search whenever the engine is not installed. It names the search
it uses at start-up (`search engine: rust <version>` or `python`) and in `list_cities`.
`POLAND_TRANSIT_ENGINE=python` forces the Python search.

## One-time setup (maintainer)

1. **PyPI account**, with two-factor authentication.
2. **A trusted publisher for each package.** On PyPI: *Your account → Publishing → Add a new pending publisher*,
   once with project `poland-transit-mcp` and once with `poland-transit-mcp-engine`, each with:
   - owner `alexratmanpl`, repository `poland-transit-mcp`
   - workflow `release.yml`, environment `pypi`

   This lets that one workflow publish those two projects, with no token stored anywhere. A pending publisher
   does not reserve a name: the first upload does.
3. **GitHub environment.** In the repository: *Settings → Environments → New environment* `pypi`, with yourself
   as a required reviewer, so every upload waits for your approval.

## Publishing a release

Both packages come from one workflow, run on a release tag (`vX.Y.Z`, the version in `pyproject.toml`).

1. **Dry run.** *Actions → release → Run workflow*, pick the tag, "publish" **unticked**.
   - It builds the server (wheel and source) and the engine: wheels for Linux (x86-64, ARM), macOS (Intel, Apple
     Silicon), Windows (x64), plus its source. The engine's wheels are abi3: one per platform for every CPython
     from 3.11 on.
   - It installs the Linux engine wheel next to the server and runs the test suite, including the check that Rust
     and Python give the same plans.
2. **Publish.** If everything is green, run it again on the tag with "publish" **ticked**, and approve the `pypi`
   environment. It refuses to publish from anything but a tag whose name matches the server's version. Files PyPI
   already has for that version are skipped, so a release first uploaded by hand gets the engine's wheels added.
3. **Check.** On any machine with city files you built: `uvx --from "poland-transit-mcp[fast]" poland-transit-mcp
   --data-dir DIR --no-download`. The start-up line should say `search engine: rust`. (Without city files the
   server stops before that line.)

**By hand, if the workflow cannot run:** from a clean clone of the tag, `uv build --sdist --wheel` for the server
and `uvx maturin sdist` in `engine/` for the engine's source, then `uv publish` with a PyPI token, naming only the
`*.whl` and `*.tar.gz` files. Installing the engine from that source upload needs a Rust toolchain until the
workflow adds its wheels.

## What was checked before this was written

See [CHECKS.md](CHECKS.md), "Rust engine". In short:
- The wheel builds natively, and also as manylinux2014 (the widely compatible Linux format).
- It installs into a clean environment, and from source with a Rust toolchain.
- `[fast]` resolves when the engine is offered from a package index (simulated with `--find-links`).
- Answers are identical to the Python search on 400 real trip plans and on every acceptance query.
- Uninstalling the engine falls back to the Python search.
- **Not checked:** the macOS and Windows wheels. They can only be built on those systems; the release workflow's dry run does that.
