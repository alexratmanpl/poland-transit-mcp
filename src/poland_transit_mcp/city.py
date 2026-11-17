"""One loaded city: read-only SQLite handle, metadata, lazily-built walking graph and timetable."""
from __future__ import annotations

import json
import sqlite3
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np

from . import telemetry
from .config import CITIES, CityConfig
from .timetable import Stop, Timetable
from .timeutil import fmt_date
from .walk import GraphArrays, WalkGraph


class City:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        meta = dict(self.db.execute("SELECT key, value FROM meta"))
        self.id: str = meta["city"]
        self.cfg: CityConfig | None = CITIES.get(self.id)
        self.name: str = meta["name"]
        self.bbox: list[float] = json.loads(meta["bbox"])
        self.osm: dict = json.loads(meta["osm"])
        self.feeds: list[dict] = json.loads(meta["feeds"])
        self.built_at: str = meta.get("built_at", "")
        # the national layer (trains and coaches between cities): timetables only, no map or walking graph
        self.national: bool = meta.get("kind") == "national"
        self.gateways: dict = {}  # intercity.py: this layer's stations inside each city, per city id
        self._graph: WalkGraph | None = None
        self._tt: Timetable | None = None
        self._stop_nodes: dict[int, list[Stop]] | None = None

    @staticmethod
    def open(data_dir: Path, city_id: str) -> "City":
        f = Path(data_dir) / f"{city_id}.sqlite"
        if not f.exists():
            raise FileNotFoundError(f'no data file for "{city_id}" at {f} '
                                    f"(download it: poland-transit-mcp download {city_id}, or build it: poland-transit-mcp build {city_id} --download)")
        return City(f)

    @property
    def graph(self) -> WalkGraph:
        if self._graph is None:
            if self.national:
                raise ValueError(f'"{self.id}" is the national layer of trains and coaches: it has stations and '
                                 "timetables but no map. Use departures or line_info on it, or plan_trip with "
                                 "city and to_city for a trip between cities")
            with telemetry.city_load(self.id, "graph") as s:
                rows = dict(self.db.execute("SELECT name, data FROM blobs WHERE name LIKE 'walk_%'"))
                self._graph = WalkGraph(GraphArrays.from_blobs(rows))
                s.set_attribute("poland_transit.bytes", self._graph.nbytes())
        return self._graph

    @property
    def tt(self) -> Timetable:
        if self._tt is None:
            with telemetry.city_load(self.id, "timetable") as s:
                self._tt = Timetable(self.db, city_id=self.id)
                s.set_attribute("poland_transit.stops", self._tt.n_stops)
        return self._tt

    def heavy_bytes(self) -> int:
        """Estimated memory of the parts loaded on demand (walking graph, timetable)."""
        return (self._graph.nbytes() if self._graph is not None else 0) + (self._tt.approx_bytes() if self._tt is not None else 0)

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

    def provenance(self, parts: tuple[str, ...]) -> dict:
        source: list[str] = []
        as_of: dict[str, str] = {}
        if "osm" in parts and self.osm.get("attribution"):
            source.append(self.osm["attribution"])
            as_of["osm"] = self.osm["timestamp"]
        if "gtfs" in parts:
            if self.has_transit:
                # some publishers (e.g. ZTM Poznań) require saying that their data was processed
                source.append("Timetables processed by poland-transit-mcp (feeds merged, shapes simplified); "
                              "check the operator for changes")
            for f in self.feeds:
                if f["status"] != "ok":
                    source.append(f'{f["name"]}: not available ({f["status"]})')
                    continue
                credits = "".join(f'; {c["role"]}: {c["name"]}' + (f' <{c["url"]}>' if c.get("url") else "")
                                  for c in f.get("credits", ()))
                source.append(f'{f["name"]} — {f["attribution"]} ({f["licence"]}){credits}')
                # an explicit publication note (e.g. a PDF's date), else the server's Last-Modified
                published = f.get("published") or f.get("last_modified")
                if published:
                    try:
                        published = parsedate_to_datetime(published).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (TypeError, ValueError):
                        pass
                if not published and f.get("files_dated"):
                    published = f'{f["files_dated"]} local (file dates in the zip)'
                as_of[f'gtfs_{f["key"]}'] = (f'published {published or "unknown"}, '
                                             f'retrieved {f.get("downloaded_at") or "unknown"}, '
                                             f'valid {fmt_date(f["start_date"])}..{fmt_date(f["end_date"])}')
        return {"source": source, "as_of": as_of}
