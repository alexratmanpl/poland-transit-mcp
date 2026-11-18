"""The limit on tool calls running at once: memory grows with every call in flight, so a server shared by several
clients works on at most `max_calls` of them; a call that cannot start within `wait_s` is refused at once as busy
(a quick answer the caller can retry), never queued without end."""
import threading
import time

import anyio
from mcp import Client

from poland_transit_mcp.server import CallLimit, create_server


def call(server, name="list_cities", args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args or {})
    return anyio.run(go)


def test_a_call_over_the_limit_waits_then_is_refused_as_busy(city):
    limit = CallLimit(max_calls=1, wait_s=0.2)
    server = create_server({"testowo": city}, calls=limit)
    assert limit.slots.acquire()  # another call holds the only slot
    try:
        t = time.perf_counter()
        r = call(server)
        assert r.is_error and ": busy: " in r.content[0].text, r.content[0].text
        assert 0.15 <= time.perf_counter() - t < 2
    finally:
        limit.slots.release()
    assert not call(server).is_error  # the slot is free again


def test_a_waiting_call_starts_when_a_slot_frees_within_the_wait(city):
    limit = CallLimit(max_calls=1, wait_s=5)
    server = create_server({"testowo": city}, calls=limit)
    assert limit.slots.acquire()
    threading.Timer(0.2, limit.slots.release).start()
    assert not call(server).is_error


def test_every_slot_is_given_back_after_errors_too(city):
    limit = CallLimit(max_calls=1, wait_s=0.2)
    server = create_server({"testowo": city}, calls=limit)
    assert call(server, "find_places", {"city": "nowhere"}).is_error  # a refused call
    assert not call(server).is_error


def test_no_limit_by_default_in_the_library():
    assert CallLimit().max_calls is None
