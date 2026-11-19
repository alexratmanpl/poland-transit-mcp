"""The opt-in live feed: a slow upstream holds a call for at most the live timeout in all, and stop ids from the
feed are quoted into the request. No network: the opener live.py calls is replaced."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from poland_transit_mcp import live


class Drip:
    """A reply that sends one byte every `every` seconds."""

    def __init__(self, body: bytes, every: float):
        self.body, self.every = body, every

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read1(self, n: int) -> bytes:
        time.sleep(self.every)
        out, self.body = self.body[:1], self.body[1:]
        return out

    read = read1


@pytest.fixture()
def live_city(city, monkeypatch):
    monkeypatch.setattr(city, "cfg", SimpleNamespace(live="ztm-gdansk"))
    monkeypatch.setattr(live, "_enabled", {city.id})
    monkeypatch.setattr(live, "TIMEOUT_S", 0.5)
    return city


def test_a_reply_that_trickles_in_is_cut_at_the_deadline(monkeypatch):
    monkeypatch.setattr(live, "TIMEOUT_S", 0.3)
    monkeypatch.setattr(live.net, "urlopen", lambda url, timeout: Drip(b'{"departures": []}' * 100, 0.05))
    t = time.monotonic()
    with pytest.raises(TimeoutError):
        live._fetch_ztm("1234")
    assert time.monotonic() - t < 0.3 + 0.2  # the deadline plus at most one read


def test_a_slow_upstream_holds_the_call_for_the_live_timeout_at_most(live_city, monkeypatch):
    stalled = threading.Event()

    def stall(url, timeout):  # the reply never comes (a drip in the headers looks the same from here)
        stalled.wait(5)
        raise OSError("gone")

    monkeypatch.setattr(live.net, "urlopen", stall)
    stops = [s for s in live_city.tt.stops if s][:6]
    t = time.monotonic()
    out = live.live_departures(live_city, stops, None, 5)
    took = time.monotonic() - t
    stalled.set()
    assert out["ok"] is False and out["status"].startswith("unavailable")
    assert took < 0.5 + 0.5, took


def test_a_full_reply_is_read_and_merged(live_city, monkeypatch):
    monkeypatch.setattr(live.net, "urlopen", lambda url, timeout: Drip(json.dumps(
        {"departures": [], "lastUpdate": "2026-11-10T08:00:00Z"}).encode(), 0))
    out = live.live_departures(live_city, [s for s in live_city.tt.stops if s][:2], None, 5)
    assert out["ok"] is True and out["as_of"] == "2026-11-10T08:00:00Z"


def test_stop_ids_from_the_feed_are_quoted_into_the_url(monkeypatch):
    urls = []

    def capture(url, timeout):
        urls.append(url)
        return Drip(b"{}", 0)

    monkeypatch.setattr(live.net, "urlopen", capture)
    live._fetch_ztm("1234")
    live._fetch_ztm("12&stopId=999&x=1")
    live._fetch_ztm("../../admin?")
    assert urls[0] == "https://ckan2.multimediagdansk.pl/departures?stopId=1234"  # digits as before
    assert urls[1] == "https://ckan2.multimediagdansk.pl/departures?stopId=12%26stopId%3D999%26x%3D1"
    assert urls[2] == "https://ckan2.multimediagdansk.pl/departures?stopId=..%2F..%2Fadmin%3F"


def test_when_too_many_fetches_run_the_call_uses_the_schedule(live_city, monkeypatch):
    monkeypatch.setattr(live, "_fetches", threading.BoundedSemaphore(1))
    gate = threading.Event()

    def wait(url, timeout):
        gate.wait(5)
        return Drip(b"{}", 0)

    monkeypatch.setattr(live.net, "urlopen", wait)
    try:
        out = live.live_departures(live_city, [s for s in live_city.tt.stops if s][:2], None, 5)
    finally:
        gate.set()
    assert out["ok"] is False and out["status"].startswith("unavailable")
