"""Streamable HTTP (web.py): the Host/Origin check on every loopback address, POST only, and a quiet log."""
import logging

import pytest
from starlette.testclient import TestClient

from poland_transit_mcp import web
from poland_transit_mcp.server import create_server

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}


@pytest.fixture
def client(city):
    def make(host: str, base: str | None = None):
        # a server of its own each time: the SDK's session manager runs once per app
        return TestClient(web.app(create_server({"testowo": city}), host), base_url=base or f"http://{host}:8080")
    return make


@pytest.mark.parametrize("host, loopback", [
    ("127.0.0.1", True), ("127.0.0.2", True), ("127.1", True), ("127.255.255.254", True), ("localhost", True),
    ("LOCALHOST", True), ("::1", True), ("[::1]", True),
    ("0.0.0.0", False), ("192.168.1.5", False), ("10.0.0.1", False), ("::", False), ("example.com", False),
])
def test_every_loopback_address_is_loopback(host, loopback):
    assert web.is_loopback(host) is loopback
    assert web.transport_security(host).enable_dns_rebinding_protection is loopback


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "127.1"])
def test_a_rebinding_page_is_refused_on_every_loopback_address(client, host):
    with client(host) as c:
        assert c.post("/mcp", json=INIT, headers=HEADERS).status_code == 200  # the address itself is served
        evil = c.post("/mcp", json=INIT, headers={**HEADERS, "Host": "evil.example:8080"})
        assert evil.status_code == 421, evil.text
        evil = c.post("/mcp", json=INIT, headers={**HEADERS, "Origin": "http://evil.example"})
        assert evil.status_code == 403, evil.text


def test_another_address_has_no_host_check(client):
    """Behind a gateway the Host header names the gateway, which the server cannot know: the check is off (and
    the server warns at start)."""
    with client("0.0.0.0", "http://gateway.example") as c:
        assert c.post("/mcp", json=INIT, headers=HEADERS).status_code == 200


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_get_and_delete_are_refused_with_405(client, method, path):
    """A stateless server with JSON replies never pushes: a GET stream would only hold a file descriptor."""
    with client("127.0.0.1") as c:
        r = c.request(method, path, headers={"Accept": "text/event-stream"})
        assert r.status_code == 405 and r.headers["allow"] == "POST", (r.status_code, r.text)
        assert c.post("/mcp", json=INIT, headers=HEADERS).status_code == 200  # POST still answers


def test_an_unknown_prompt_or_resource_over_http_never_reaches_the_log(client, caplog, capsys):
    secret = "SECRET-HOME-ADDRESS-12"
    with client("127.0.0.1") as c, caplog.at_level(logging.DEBUG):
        for n, (method, params) in enumerate([("prompts/get", {"name": secret}),
                                              ("resources/read", {"uri": f"credits://{secret}"})], 2):
            r = c.post("/mcp", json={"jsonrpc": "2.0", "id": n, "method": method, "params": params}, headers=HEADERS)
            assert r.status_code == 200 and "error" in r.json(), r.text
        c.post("/mcp", json=INIT, headers={**HEADERS, "Host": f"{secret}:8080"})
    logged = caplog.text + capsys.readouterr().err
    assert "prompts/get" in logged and "ValueError at" in logged, logged[:800]  # still logged, by kind and place
    assert "Invalid Host header" in logged, logged[:800]
    assert secret not in logged and "Traceback" not in logged, logged[:800]
