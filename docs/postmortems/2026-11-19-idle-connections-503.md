# Postmortem: idle connections turned every HTTP call away (0.5.4)

**Date:** 2026-11-19 · **Status:** resolved in 0.5.5 · **Impact:** none on users (caught while the repository was
private and before anything was on PyPI) · **Format:** blameless: what happened, why our process let it through, what changed.

## Summary

A security fix in 0.5.4 set a default cap of 100 HTTP connections (`--max-connections`). uvicorn counts every open
connection against that cap, also one that has not sent a request yet. So about 100 sockets that connect and stay
silent made the server answer every real call with `503 Service Unavailable`. An independent retest of 0.5.4 found
it the same day. 0.5.5 removed the default cap and documented that a shared `--http` server belongs behind a
gateway.

The regression was cheaper to trigger than the problem it was meant to fix: about 100 sockets, where idle GET
streams needed about 1,000 to exhaust file descriptors.

## Timeline (2026-11-19, CET)

| Time | Event |
|---|---|
| 13:31 | A security audit of 0.5.3 reports, among others, that idle `GET /mcp` streams are held forever and can exhaust file descriptors |
| 14:14 | 0.5.4 released with the audit fixes: GET and DELETE answered 405, and uvicorn's `limit_concurrency=100` with a 5 s keep-alive as a default cap |
| 14:18 | An independent retest of 0.5.4 confirms the earlier findings closed, and reports a new one: 150 bare or slow-header connections held for 7 s make a normal call get an immediate 503 |
| afternoon | Triage confirms it in uvicorn's code: the cap compares the number of open connections (idle ones included) with the limit, and the keep-alive timer starts only after a first response |
| 15:51 | Fix: no cap by default; `--max-connections` opt-in; SECURITY.md "Connection floods" |
| 16:08 | 0.5.5 released |

## Root cause

uvicorn's `limit_concurrency` answers 503 once *open connections* (or running tasks) reach the limit. A connection
counts from the moment it is accepted, before it sends anything. Its `timeout_keep_alive` closes a connection only
after a response, so a connection that never completes a request is never closed by it. A default cap of 100 therefore
gave anyone who could open 100 sockets a way to turn every caller away until the sockets closed.

## Why our process let it through

- **The test could not see it.** The new HTTP tests used an in-process ASGI client (Starlette's `TestClient`). It
  calls the app directly and never goes through uvicorn's connection layer, where the cap lives. The 405 behaviour
  was tested; the cap was not.
- **Review read our code, not the library's semantics.** The change was a single keyword argument. Its meaning (what
  counts against the limit, when the idle timer starts) is in uvicorn's protocol code, which nobody read until the
  retest pointed at it.
- **The fix worked at the wrong layer.** A connection flood is a network-layer problem. One process can make it more
  expensive, not stop it; the real control is a gateway or buffering proxy in front. The cap was a cheap-looking
  default chosen without measuring the attack it was meant to stop.
- **Many fixes in one batch.** 0.5.4 carried about fifteen fixes from one audit. Each was reviewed, but the batch
  made it easy to treat a small one as obviously safe.

## What went well

- An independent retest ran while the repository was private and before anything was on PyPI, so no user ever
  had the regression.
- The finding was precise (counts, timings), so triage took minutes.
- Releases were ordered so nothing had been uploaded yet.

## What changed

| Action | Status |
|---|---|
| No connection cap by default; `--max-connections` stays opt-in, documented with this caveat | done (0.5.5) |
| SECURITY.md "Connection floods": what one process can and cannot do; put a shared `--http` behind a gateway or buffering reverse proxy | done (0.5.5) |
| A regression test against a real uvicorn server: 110 idle sockets, then a call must succeed; the capped case shows the 503 (`tests/test_http_connections.py`) | done |
| CONTRIBUTING.md: a change to the HTTP transport is tested against a real server, not only with an in-process client | done |

## Lessons

1. Test a transport change at the layer where it acts.
2. Read the semantics of a third-party limit before choosing its default.
3. A security fix is a change like any other: it can add the risk it removes. Retest after fixing.
