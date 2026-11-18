"""Live departure estimates, merged into scheduled departures. Opt-in per city and only for operators
whose terms allow it (SOURCES.md). Live entries are matched to scheduled trips; nothing appears that
the schedule does not contain."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import net
from .city import City
from .timetable import Stop
from .timeutil import fmt_date, fmt_hm, now_local, to_local
from .transit import scheduled_departures

TIMEOUT_S = 2.5
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
    url = f"https://ckan2.multimediagdansk.pl/departures?stopId={stop_num}"
    with net.urlopen(url, timeout=TIMEOUT_S) as r:  # fixed https host
        return json.loads(r.read(2 << 20))  # bounded: a larger reply is not valid JSON and falls back to the schedule


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def live_departures(city: City, stops: list[Stop], line: str | None, limit: int) -> dict | None:
    if not city.cfg or city.cfg.live != "ztm-gdansk" or not live_enabled(city.id):
        return None
    now = now_local()
    # schedule from an hour ago, so that delayed vehicles can still be matched
    sched = scheduled_departures(city, stops, type(now)(now.date, max(0, now.secs - 3600)), line, 400)
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            feeds = list(zip(stops[:6], ex.map(lambda s: _fetch_ztm(s.id.removeprefix("G:")), stops[:6])))
        return _merge(sched, feeds, now, limit)
    except Exception as e:  # network down, timeout, bad or unexpected JSON: fall back to the schedule, labelled
        fresh = scheduled_departures(city, stops, type(now)(now.date, max(0, now.secs - 3600)), line, 400)  # `sched` may be half-merged
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
