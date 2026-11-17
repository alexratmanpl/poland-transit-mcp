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
- **`--http` has no authentication.** It binds to `127.0.0.1` only, but any local process can call the tools.
  Do not expose it beyond loopback.
- **Tool answers contain third-party text** (OpenStreetMap names, feed descriptions). Clients and models should
  treat it as data, never as instructions.
- **`--live gdansk`** makes outbound requests to ZTM Gdańsk. It is off by default.

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
