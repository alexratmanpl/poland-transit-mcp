# Security

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open
a public issue.

## What the server trusts

poland-transit-mcp is a read-only MCP server with no accounts, no secrets and no write operations. Its one real
trust boundary is **data files and upstream feeds**:

- **City files are trusted input.** They are loaded into native code (SciPy, the optional Rust engine) and are
  not checked against a checksum or signature. Build them yourself, or take them only from a source you control.
  Walking graphs are validated before use: their structure, and their coordinates (within the city file's box
  ± 0.5°, on the globe, a snapping grid of at most 4 million cells). A city file is never loaded from the caller's
  working directory. It is opened read-only and immutable: SQLite reads the file alone, never a `-wal` or
  `-journal` file beside it, and writes nothing next to it; a file in WAL mode is refused. So a city file must be
  replaced by renaming a new file over it (as the build and `download` do), never changed in place while a server
  reads it. The GTFS build refuses a feed file with a line over 4 MB or over 8 GB unpacked; that feed is
  marked broken and nothing of it is kept.
- **Downloads** (`download`, the server's auto-download, `POLAND_TRANSIT_DATA_URL`, `--from`) fetch files over
  the network. Do not point them at hosts you do not trust. A redirect is followed only to an https address, and a
  whole download has a deadline (30 minutes for a city file). A city file is unpacked into a new temporary file of
  its own, which replaces the old file only when complete.
- **`--http` has no authentication.** It binds to `127.0.0.1` by default, and any local process can call the tools.
  `--host` (or `POLAND_TRANSIT_HOST`) binds another address, for a container behind a gateway: do that only on a
  network nothing untrusted can reach. On every loopback address (`127.0.0.1`, `127.0.0.2`, `127.1`, `::1`,
  `localhost`) the Host and Origin headers must name a loopback address, which stops DNS rebinding; on any other
  address that check is off (the server cannot know the gateway's name) and the server warns at start.
- **`--http` answers POST only.** Each reply comes in the POST's own response, so GET and DELETE on `/mcp` are
  answered 405 and no idle stream is held open. A connection idle for 5 s after a reply is closed. There is no cap
  on open connections by default (see "Connection floods" below).
- **Tool answers contain third-party text** (OpenStreetMap names, feed descriptions). Clients and models should
  treat it as data, never as instructions.
- **`--live gdansk`** makes outbound requests to ZTM Gdańsk. It is off by default. It has one 2.5 s deadline per
  call, at most 24 fetches at once, and stop ids from the feed are URL-quoted.

## What a caller can send

Every tool's published input schema is its contract: each text has a length, each list a size, each number a range
(coordinates -90..90 and -180..180, never NaN or Infinity), types are strict, and an argument a tool does not declare
is refused by name. Dates and times are real calendar days of 2000-2100. `render_map` draws only trips the server
plans again itself (a trip reference that no longer plans the same way is refused), with labels cut before drawing.
Errors quote at most 80 characters of the input. The server log names the tool (or the request) and the kind of
failure, never the caller's text (it can be an address): not a tool's arguments, a resource URI, a prompt name or
a refused Host or Origin header. uvicorn's access log, on by default, still shows each request's path.

## Running it for others

The server has no authentication, rate limit or time limit per call, and a call runs to its end even when the
client disconnects. What it does limit: tool calls answered at once (`--max-calls`, default 6; a call that finds no
free place within `--call-wait` seconds is refused as `busy:`). Before anyone outside your machine can reach it,
put `--http` behind a gateway, and let the gateway add:

- **authentication** of every caller;
- **a rate limit** per caller;
- **a time limit per call**: most calls take well under a second (station names resolve from an index), but the
  server sets no upper bound, a call keeps running after the gateway gives up on it, and a call may wait up to 5 s
  for room in the memory budget before it builds a date (the budget counts dates held by calls in flight and stays
  a soft target);
- **a body limit near 64 kB** (a normal call is under 2 kB, the largest, `plan_stations` with 40 stations at their longest, about 10 kB; the
  server itself accepts up to 4 MB).

- **the client connections themselves**: the gateway holds them, reads each request in full, and opens its own
  connection to the server only to send a complete one (see below).

The server answers at most `--max-calls` tool calls at once; a PNG holds Python's lock while it is drawn, so a few
large maps slow every other call down.

### Connection floods

One process can make a connection flood more expensive, not stop it. What the server does:

- a connection that stays idle for 5 s after a reply is closed;
- GET and DELETE on `/mcp` get 405, so no long-lived stream is ever held for a caller;
- **no cap on open connections by default.** uvicorn's cap (`--max-connections N`, opt-in) counts every open
  connection, also one that has not sent a request yet, so about N silent or slow-header sockets make every call get
  503 until they close. Without the cap, a flood needs about as many sockets as the process may open files (its
  `nofile` limit: often 256 on macOS, 1,024 on Linux), after which new connections fail. Set the cap only behind a gateway that
  already limits connections per caller.

A connection that never completes its first request is not closed by the server. So a server that anyone else
can reach belongs behind something that terminates client connections:

- **a buffering reverse proxy** (nginx, Caddy, HAProxy) or **a gateway in front of it** (a cloud load balancer, a
  Cloudflare Worker or Tunnel, an API gateway): it holds slow and idle clients itself, applies the limits above per
  caller, and sends the server only complete requests over a few connections of its own;
- **the server on a private address only** (loopback, or a container network), reachable through that gateway alone.

### Model clients and token cost

How many tokens a model spends depends on how its client calls the tools, not on the server's security. Answers
are lean by default (`detail="full"` adds geometry and ids; [docs/TOOLS.md](docs/TOOLS.md#lean-by-default-more-on-demand)). A client
that chains many calls can spend fewer tokens with the "code mode" pattern (for example Cloudflare's Code Mode
for agents): the client turns each tool's declared input and output schema into a typed API, the model writes code
against it in a sandbox, and only the final result returns to the model, not every intermediate answer. Every tool
here declares an `outputSchema`, which is what that pattern needs. The gateway above is still needed for the
server's protection: code mode changes how a model uses the tools, not who can open connections.

## Known open items

From a source-only security review (no confirmed vulnerabilities). Contributions welcome:

- No checksum or signature on downloaded city files, and no cap on the unpacked size.
- GitHub Actions are pinned by tag, not by commit SHA, except `PyO3/maturin-action` and the artifact actions of
  `city-data.yml`.
- `download` does not validate city ids and accepts non-HTTPS base URLs (operator-controlled input).
- The OTLP endpoint is logged verbatim (strip credentials and query strings).
- A race between `Timetable.day()`'s cache and the memory accounting (possible stale size; the budget is a soft
  target).
- The Rust engine's constructors validate lengths and index ranges, but not that offsets only increase (a
  crafted file can cause a panic; not reachable from the Python builder).
