"""The wire: raw HTTP bodies against `--http` on loopback, in a real server process. The argument models never see
what the transport refuses or changes, so this checks what gets that far: NaN and Infinity (valid in Python's JSON
reader, absent from JSON), a body over the limit, deep nesting, a wrong Host or Origin. After all of it the server
still answers."""
import http.client
import json
import os
import socket
import subprocess
import sys
import time

import pytest

TOOL_CALL = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"%s","arguments":%s}}'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def wire(city_dir):
    port = _free_port()
    env = {**os.environ, "POLAND_TRANSIT_NO_WARMUP": "1"}
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    proc = subprocess.Popen([sys.executable, "-m", "poland_transit_mcp", "--data-dir", str(city_dir), "--cities",
                             "testowo", "--no-download", "--http", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if proc.poll() is not None:
                pytest.fail(proc.stderr.read().decode(errors="replace")[-2000:])
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("the server did not open its port in 30 s")
    yield port
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def post(port: int, body: str | bytes, headers: dict | None = None) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    head = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", **(headers or {})}
    conn.request("POST", "/mcp", body=body.encode() if isinstance(body, str) else body, headers=head)
    r = conn.getresponse()
    out = r.status, r.read().decode(errors="replace")
    conn.close()
    return out


def answered(status: int, body: str) -> bool:
    """Whether the server answered the call (a result that is not an error)."""
    if status != 200:
        return False
    try:
        msg = json.loads(body)
    except ValueError:
        return False
    return "result" in msg and not msg["result"].get("isError")


def call(port: int, tool: str, arguments: str) -> tuple[int, str]:
    return post(port, TOOL_CALL % (tool, arguments))


def test_a_normal_call_is_answered(wire):
    assert answered(*call(wire, "list_cities", "{}"))


@pytest.mark.parametrize("tool,arguments,name", [
    ("find_places", '{"city":"testowo","near":[NaN,NaN]}', "near"),
    ("find_places", '{"city":"testowo","near":[50.0,19.0],"radius_m":Infinity}', "radius_m"),
    ("find_places", '{"city":"testowo","near":[50.0,19.0],"radius_m":-Infinity}', "radius_m"),
    ("find_places", '{"city":"testowo","query":"Centrum","limit":NaN}', "limit"),
    ("stops_near", '{"city":"testowo","point":[NaN,19.0]}', "point"),
    ("render_map", '{"city":"testowo","bbox":[49.999,18.999,50.01,Infinity]}', "bbox"),
])
def test_nan_and_infinity_in_the_body_are_refused_by_name(wire, tool, arguments, name):
    status, body = call(wire, tool, arguments)
    assert not answered(status, body), body[:300]
    if status == 200 and "result" in json.loads(body):
        text = json.loads(body)["result"]["content"][0]["text"]
        assert name in text and text != f"Error executing tool {tool}", text


def test_a_body_over_the_limit_is_refused_before_it_is_read(wire):
    status, _ = call(wire, "find_places", json.dumps({"city": "testowo", "query": "a" * 5_000_000}))
    assert status == 413


def test_deep_nesting_is_refused(wire):
    status, body = call(wire, "find_places", '{"city":"testowo","kinds":' + "[" * 100_000 + "]" * 100_000 + "}")
    assert not answered(status, body)


def test_a_wrong_host_is_refused(wire):
    assert post(wire, TOOL_CALL % ("list_cities", "{}"), {"Host": "evil.example"})[0] == 421


def test_a_wrong_origin_is_refused(wire):
    assert post(wire, TOOL_CALL % ("list_cities", "{}"), {"Origin": "http://evil.example"})[0] == 403


def test_the_server_still_answers_after_all_of_it(wire):
    assert answered(*call(wire, "find_places", '{"city":"testowo","query":"Centrum"}'))
