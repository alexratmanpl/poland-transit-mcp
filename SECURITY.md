# Security

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open
a public issue.

## What the server trusts

poland-transit-mcp is a read-only MCP server with no accounts, no secrets and no write operations. Its one real
trust boundary is **data files and upstream feeds**:

- **City files are trusted input.** They are loaded into native code (SciPy, the optional Rust engine) and are
  not checked against a checksum or signature. Build them yourself, or take them only from a source you control.
  Walking graphs are validated before use, and a city file is never loaded from the caller's working directory.
- **Downloads** (`download`, the server's auto-download, `POLAND_TRANSIT_DATA_URL`, `--from`) fetch files over
  the network. Do not point them at hosts you do not trust.
- **`--http` has no authentication.** It binds to `127.0.0.1` by default, and any local process can call the tools.
  `--host` (or `POLAND_TRANSIT_HOST`) binds another address, for a container behind a gateway: do that only on a
  network nothing untrusted can reach. The server then warns at start, and the MCP SDK's DNS-rebinding check (on for
  loopback) is off.
- **Tool answers contain third-party text** (OpenStreetMap names, feed descriptions). Clients and models should
  treat it as data, never as instructions.
- **`--live gdansk`** makes outbound requests to ZTM Gdańsk. It is off by default.

## What a caller can send

Every tool's published input schema is its contract: each text has a length, each list a size, each number a range
(coordinates -90..90 and -180..180, never NaN or Infinity), types are strict, and an argument a tool does not declare
is refused by name. Dates and times are real calendar days of 2000-2100. `render_map` draws at most 30 legs of at
most 5,000 points, with labels cut before drawing. Errors quote at most 80 characters of the input, and the server
log names the tool and the kind of failure, never the caller's text (it can be an address).

## Running it for others

The server has no authentication, rate limit or time limit per call, and a call runs to its end even when the
client disconnects. Before anyone outside your machine can reach it (`--http` behind a gateway), the gateway must
add:

- **authentication** of every caller;
- **a rate limit** per caller;
- **a time limit per call** (the slowest real calls measured, a long trip between cities or a large PNG, took under
  0.6 s on a laptop);
- **a body limit near 64 kB** (a normal call is under 2 kB, the largest, `plan_stations` with 40 stations at their longest, about 10 kB; the
  server itself accepts up to 4 MB).

The server takes at most about 40 calls at once (the SDK's thread pool); a PNG holds Python's lock while it is
drawn, so a few large maps slow every other call down.

## Known open items

From a source-only security review (no confirmed vulnerabilities). Contributions welcome:

- No checksum or signature on downloaded city files, and no cap on the unpacked size.
- `city-data.yml` builds and uploads in one job that holds a write token; split it into a read-only build job and
  a minimal upload job.
- GitHub Actions are pinned by tag, not by commit SHA.
- `download` does not validate city ids and accepts non-HTTPS base URLs (operator-controlled input).
- The SQLite URI is built from an unescaped path.
- The OTLP endpoint is logged verbatim (strip credentials and query strings).
- A race between `Timetable.day()`'s cache and the memory accounting (possible stale size; the budget is a soft
  target).
- The Rust engine's constructors validate lengths and index ranges, but not that offsets only increase (a
  crafted file can cause a panic; not reachable from the Python builder).
