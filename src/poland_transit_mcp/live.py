"""Live departure estimates, merged into scheduled departures. Opt-in per city and only for operators
whose terms allow it (SOURCES.md). Live entries are matched to scheduled trips; nothing appears that
the schedule does not contain."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from urllib.parse import quote

from . import net
from .city import City
from .timetable import Stop
from .timeutil import fmt_date, fmt_hm, now_local, to_local
from .transit import scheduled_departures

TIMEOUT_S = 2.5  # for the whole live fetch of a call: connecting, every read, all its stops together
MAX_REPLY = 2 << 20  # a larger reply is not valid JSON and falls back to the schedule
MAX_FETCHES = 24  # live fetches running at once across calls; when all are busy a call uses the schedule
_fetches = threading.BoundedSemaphore(MAX_FETCHES)
# the credit of each live source (config.py names the source per city); answers name it as "<city>/live"
LIVE_CREDITS = {
    "ztm-gdansk": "ZTM Gdańsk real-time departures (ckan2.multimediagdansk.pl/departures), "
                  "Zarząd Transportu Miejskiego w Gdańsku, https://www.ztm.gda.pl/otwarty_ztm",
}
_enabled: set[str] = set()


def enable_live(ids) -> None:
    _enabled.update(i.strip() for i in ids if i.strip())


def live_enabled(city_id: str) -> bool:
    return city_id in _enabled


def _fetch_ztm(stop_num: str) -> dict:
    """One stop's live departures, read within TIMEOUT_S in all: a reply that trickles in is cut off."""
    deadline = time.monotonic() + TIMEOUT_S
    url = f"https://ckan2.multimediagdansk.pl/departures?stopId={quote(stop_num, safe='')}"  # a feed's id, quoted
    with net.urlopen(url, timeout=TIMEOUT_S) as r:  # fixed https host
        body = bytearray()
        while len(body) < MAX_REPLY:
            if time.monotonic() > deadline:
                raise TimeoutError(f"live feed slower than {TIMEOUT_S:g} s")
            read = getattr(r, "read1", None) or r.read  # read1: what has arrived, without waiting for more
            chunk = read(min(1 << 16, MAX_REPLY - len(body)))
            if not chunk:
                break
            body += chunk
        return json.loads(bytes(body))


def _fetch_all(stop_nums: list[str]) -> list[dict]:
    """The live departures of each stop, fetched at once, within TIMEOUT_S in all: past it, the fetches still
    running are left to end on their own (each stops at its own deadline) and the call goes on without them."""
    deadline = time.monotonic() + TIMEOUT_S
    futures: list[Future] = []
    sem = _fetches
    for num in stop_nums:
        if not sem.acquire(blocking=False):
            raise TimeoutError("too many live fetches at once")
        fut: Future = Future()
        futures.append(fut)

        def run(num=num, fut=fut) -> None:
            try:
                fut.set_result(_fetch_ztm(num))
            except BaseException as e:  # noqa: BLE001 - handed to the waiting call
                fut.set_exception(e)
            finally:
                sem.release()
        threading.Thread(target=run, name="live-fetch", daemon=True).start()
    return [f.result(timeout=max(0.0, deadline - time.monotonic())) for f in futures]


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live_departures(city: City, stops: list[Stop], line: str | None, limit: int, mode: str | None = None) -> dict | None:
    if not city.cfg or city.cfg.live != "ztm-gdansk" or not live_enabled(city.id):
        return None
    now = now_local()
    # schedule from an hour ago, so that delayed vehicles can still be matched
    sched = scheduled_departures(city, stops, type(now)(now.date, max(0, now.secs - 3600)), line, 400, mode=mode)
    try:
        feeds = list(zip(stops[:6], _fetch_all([s.id.removeprefix("G:") for s in stops[:6]])))
        return _merge(sched, feeds, now, limit)
    except Exception as e:  # network down, timeout, bad or unexpected JSON: fall back to the schedule, labelled
        fresh = scheduled_departures(city, stops, type(now)(now.date, max(0, now.secs - 3600)), line, 400, mode=mode)  # `sched` may be half-merged
        return {"ok": False, "status": f"unavailable ({type(e).__name__})",
                "departures": [d for d in fresh if d["_secs"] >= now.secs][:limit]}


def _merge(sched: list[dict], feeds: list, now, limit: int) -> dict:
    matched = 0
    for stop, data in feeds:
        for l in data.get("departures", []):
            if l.get("status") != "REALTIME":
                continue
            th = to_local(_iso(l["theoreticalTime"]))
            th_secs = th.secs + (86400 if th.date != now.date else 0)
            d = next((x for x in sched if x["stop_id"] == stop.id and x["line"] == l.get("routeShortName")
                      and x["status"] == "scheduled" and abs(x["_secs"] - th_secs) < 60), None)
            if d is None:
                continue
            est = to_local(_iso(l["estimatedTime"]))
            est_secs = est.secs + (86400 if est.date != now.date else 0)
            d.update(status="live", scheduled=d["time"], time=fmt_hm(est_secs % 86400),
                     delay_min=round((l.get("delayInSeconds") or (est_secs - th_secs)) / 60), _secs=est_secs)
            matched += 1
    deps = sorted((d for d in sched if d["_secs"] >= now.secs - 30), key=lambda d: d["_secs"])[:limit]
    today = fmt_date(now.date)
    for d in deps:
        if d["date"] == today:
            d["date"] = None
    return {"ok": True, "status": f"merged: {matched} scheduled departures matched to real-time estimates",
            "departures": deps,
            "as_of": max((f.get("lastUpdate", "") for _, f in feeds), default="")}
