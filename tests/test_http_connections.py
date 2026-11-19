"""Idle connections must not turn real calls away (postmortem 2026-11-19, docs/postmortems/). A real uvicorn server
on a free port, as `--http` runs it: about a hundred sockets that connect and never send a request, then one real
call. 0.5.4's default cap of 100 counted those sockets and answered the call 503; the default has no cap. The capped
case is kept to show what the cap does, so the test fails if the default ever gets one again."""
import json
import resource
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from poland_transit_mcp import web
from poland_transit_mcp.server import create_server

IDLE = 110  # more than 0.5.4's cap of 100; each costs two file descriptors in this one process


@pytest.fixture(autouse=True)
def enough_files():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = min(hard if hard != resource.RLIM_INFINITY else 4096, 4096)
    if soft < want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    yield
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port: int) -> int:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.mark.parametrize("cap, expected", [(web.DEFAULT_MAX_CONNECTIONS, 200), (100, 503)])
def test_idle_connections_do_not_turn_a_call_away_by_default(city, cap, expected):
    port = free_port()
    server = uvicorn.Server(web.config(create_server({"testowo": city}), "127.0.0.1", port, cap))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    idle: list[socket.socket] = []
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.05)
        assert call(port) == 200  # a call on a quiet server
        for _ in range(IDLE):  # connect, send nothing
            idle.append(socket.create_connection(("127.0.0.1", port)))
        time.sleep(0.5)  # let the server accept them all
        assert call(port) == expected
    finally:
        for s in idle:
            s.close()
        server.should_exit = True
        thread.join(timeout=10)
