"""One loaded city: read-only SQLite handle (shared by threads, shared_db.py), metadata, lazily-built walking graph
and timetable."""
from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np

from .errors import Refused
from . import telemetry
from .config import CITIES, CityConfig
from .shared_db import SharedConnection
from .timetable import Stop, Timetable
from .timeutil import fmt_date, same_clock
from .walk import GraphArrays, WalkGraph


class City:
    def __init__(self, path: Path):
        self.path = path
        self.db = SharedConnection.read_only(path)
        meta = dict(self.db.execute("SELECT key, value FROM meta"))
        self.id: str = meta["city"]
        self.cfg: CityConfig | None = CITIES.get(self.id)
        self.name: str = meta["name"]
        self.bbox: list[float] = json.loads(meta["bbox"])
        self.osm: dict = json.loads(meta["osm"])
        self.feeds: list[dict] = json.loads(meta["feeds"])
        self.built_at: str = meta.get("built_at", "")
        # written by the build from the data set's definition; files built before that fall back to config.py
        self.timezone: str = meta.get("timezone", "Europe/Warsaw")
        self.notes: tuple[str, ...] = (tuple(json.loads(meta["notes"])) if "notes" in meta
                                       else self.cfg.notes if self.cfg else ())
        self.intercity_notes: tuple[str, ...] = (tuple(json.loads(meta["intercity_notes"])) if "intercity_notes" in meta
                                                 else self.cfg.intercity_notes if self.cfg else ())
        ok = [f for f in self.feeds if f.get("status") == "ok" and f.get("start_date") and f.get("end_date")]
        if self.timezone != "Europe/Warsaw":
            today = date.today()
            lo, hi = ((min(f["start_date"] for f in ok), max(f["end_date"] for f in ok)) if ok else
                      (int(today.strftime("%Y%m%d")), int((today + timedelta(days=400)).strftime("%Y%m%d"))))
            if not same_clock(self.timezone, lo, hi):
                self.db.close()
                raise Refused(f'"{self.id}" keeps its times in {self.timezone}, whose clock differs from Polish time '
                                 f"between {fmt_date(lo)} and {fmt_date(hi)}; the server reads every file on Polish time")
        # the national layer (trains and coaches between cities): timetables only, no map or walking graph
        self.national: bool = meta.get("kind") == "national"
        self.gateways: dict = {}  # intercity.py: this layer's stations inside each city, per city id
        self._graph: WalkGraph | None = None
        self._tt: Timetable | None = None
        self._stop_nodes: dict[int, list[Stop]] | None = None
        self.days_kept = 2  # dates each timetable keeps built (CityPool sets it; it holds across unloads)
        # calls arriving together at an unloaded city load each part once, not once per call
        self._graph_lock = threading.Lock()
        self._tt_lock = threading.Lock()

    @staticmethod
    def open(data_dir: Path, city_id: str) -> "City":
        f = Path(data_dir) / f"{city_id}.sqlite"
        if not f.exists():
            raise FileNotFoundError(f'no data file for "{city_id}" at {f} '
                                    f"(download it: poland-transit-mcp download {city_id}, or build it: poland-transit-mcp build {city_id} --download)")
        return City(f)

    @property
    def graph(self) -> WalkGraph:
        graph = self._graph  # read once: another thread may unload the city meanwhile
        if graph is not None:
            return graph
        if self.national:
            raise Refused(f'"{self.id}" is the national layer of trains and coaches: it has stations and '
                             "timetables but no map. Use departures or line_info on it, or plan_trip with "
                             "city and to_city for a trip between cities")
        with self._graph_lock:
            graph = self._graph
            if graph is None:
                with telemetry.city_load(self.id, "graph") as s:
                    rows = dict(self.db.execute("SELECT name, data FROM blobs WHERE name LIKE 'walk_%'"))
                    graph = self._graph = WalkGraph(GraphArrays.from_blobs(rows), self.bbox)
                    s.set_attribute("poland_transit.bytes", graph.nbytes())
        return graph

    @property
    def tt(self) -> Timetable:
        tt = self._tt  # read once: another thread may unload the city meanwhile
        if tt is not None:
            return tt
        with self._tt_lock:
            tt = self._tt
            if tt is None:
                with telemetry.city_load(self.id, "timetable") as s:
                    tt = self._tt = Timetable(self.db, city_id=self.id, days_kept=self.days_kept)
                    s.set_attribute("poland_transit.stops", tt.n_stops)
        return tt

    def heavy_bytes(self) -> int:
        """Estimated memory of the parts loaded on demand (walking graph, timetable)."""
        graph, tt = self._graph, self._tt  # read once, as above
        return (graph.nbytes() if graph is not None else 0) + (tt.approx_bytes() if tt is not None else 0)

    def keep_days(self, n: int) -> None:
        """How many dates the timetable keeps built, now and after every reload."""
        self.days_kept = n
        tt = self._tt
        if tt is not None:
            tt.days_kept = n

    def cached_days(self) -> list[int]:
        """The dates the loaded timetable has built, least recently used first (none when it is not loaded)."""
        tt = self._tt
        return tt.days_cached() if tt is not None else []

    def last_day_bytes(self) -> int | None:
        """Estimated size of the most recently used built date, or None."""
        tt = self._tt
        return tt.last_day_bytes() if tt is not None else None

    def drop_oldest_day(self) -> int | None:
        """Drops the least recently used built date while more than one is kept; returns its estimated
        size, or None when there was none to drop."""
        tt = self._tt
        return tt.drop_oldest_day(keep=1) if tt is not None else None

    def unload(self) -> None:
        """Drops the walking graph and the timetable; they reload on next use."""
        self._graph = None
        self._tt = None
        self._stop_nodes = None

    @property
    def has_transit(self) -> bool:
        return any(f["status"] == "ok" for f in self.feeds)

    def stop_nodes(self) -> tuple[np.ndarray, list[Stop]]:
        """Arrays for vectorised stop lookups after a walking search: (graph node per stop, stops)."""
        stops = [s for s in self.tt.stops if s is not None and s.node is not None]
        return np.array([s.node for s in stops], dtype=np.int64), stops

    @property
    def validity(self) -> tuple[int, int] | None:
        ok = [f for f in self.feeds if f["status"] == "ok" and f.get("start_date") and f.get("end_date")]
        if not ok:
            return None
        return min(f["start_date"] for f in ok), max(f["end_date"] for f in ok)

    @property
    def valid_from(self) -> str | None:
        """First date (YYYY-MM-DD) on which every timetable feed of this file is valid, or None without
        timetables. The scripts query this date by default, so they test real departures."""
        starts = [f["start_date"] for f in self.feeds if f["status"] == "ok" and f.get("start_date")]
        return fmt_date(max(starts)) if starts else None

    def transit_note(self, date: int | None = None) -> str | None:
        if not self.has_transit:
            why = "; ".join(f'{f["name"]}: {f["status"]}' for f in self.feeds) or "no machine-readable timetable is published"
            return f"No timetable data for {self.id} ({why}). Only map and walking data are available."
        v = self.validity
        if date and v and not (v[0] <= date <= v[1]):
            return (f"Requested date {fmt_date(date)} is outside the loaded timetable "
                    f"({fmt_date(v[0])}..{fmt_date(v[1])}); no departures are shown for it.")
        return None

    def partial_note(self, date: int) -> str | None:
        """When some of this file's timetables cover the date and others do not (e.g. the national layer's
        trains end before its coaches do)."""
        ok = [f for f in self.feeds if f["status"] == "ok" and f.get("start_date") and f.get("end_date")]
        out = [f for f in ok if not (f["start_date"] <= date <= f["end_date"])]
        if not out or len(out) == len(ok):
            return None
        return "; ".join(f'{f["name"]} covers {fmt_date(f["start_date"])}..{fmt_date(f["end_date"])}, not {fmt_date(date)}: '
                         "its trips are not shown" for f in out)

    @staticmethod
    def feed_dates(f: dict) -> dict:
        """A feed's dates: published (ISO, from an explicit publication note, else the server's Last-Modified, else
        the newest file date in the zip), retrieved, valid_from and valid_to. None where unknown."""
        published = f.get("published") or f.get("last_modified")
        if published:
            try:
                published = parsedate_to_datetime(published).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                pass
        if not published and f.get("files_dated"):
            published = f'{f["files_dated"]} local (file dates in the zip)'
        return {"published": published, "retrieved": f.get("downloaded_at"),
                "valid_from": fmt_date(f["start_date"]) if f.get("start_date") else None,
                "valid_to": fmt_date(f["end_date"]) if f.get("end_date") else None}

    def provenance(self, parts: tuple[str, ...], full: bool = False) -> dict:
        """The credit ids (credits.py has their text) and data dates of the parts of this file an answer used: each
        source's publication date (YYYY-MM-DD), or with `full` the OSM timestamp and each feed's feed_dates."""
        ids: list[str] = []
        as_of: dict[str, str | dict] = {}
        if "osm" in parts and self.osm.get("attribution"):
            ids.append("osm")
            as_of["osm"] = self.osm["timestamp"] if full else self.osm["timestamp"][:10]
        if "gtfs" in parts:
            for f in self.feeds:
                if f["status"] != "ok":  # no data from it in the answer; list_cities shows its status
                    continue
                ids.append(f'{self.id}/{f["key"]}')
                dates = self.feed_dates(f)
                as_of[f'gtfs_{f["key"]}'] = ({k: v for k, v in dates.items() if v} if full else
                                             (dates["published"] or "unknown")[:10])
        return {"credits": ids, "as_of": as_of}
