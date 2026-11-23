"""Builds <data>/cities/<city>.sqlite from an OSM regional extract and the city's GTFS feeds.

  poland-transit-mcp build gdansk [--download] [--skip-gtfs] [--data-dir data]
  poland-transit-mcp build poland [--download]      (the national layer: timetables only, no OSM)

Pipeline: osmium extract (bbox) → osmium tags-filter → osmium export (GeoJSON lines) → places,
map features and the walking graph; GTFS feeds are streamed from their zips. Needs `osmium` on PATH,
except for the national layer.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .. import datasets
from ..config import OSM_ATTRIBUTION, OSM_SOURCES, CityConfig, osm_url
from ..geo import LatLon, bbox_of, centroid, encode_parts, fast_dist, haversine, ring_area, simplify
from ..schema import SCHEMA
from ..text import fold
from ..timeutil import same_clock
from ..walk import FLAG_BRIDGE, FLAG_STEPS, FLAG_TUNNEL, GraphArrays, WalkGraph
from .. import net
from .gtfs import compute_transfers, import_feed
from .safeguard import EXIT_REJECTED, expiry_warnings, read_feeds, worse_feeds

T0 = time.perf_counter()
FAILED_DOWNLOADS: list[str] = []  # repeated after writing, since the build then uses the previous file, if any


def log(*a) -> None:
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def require_osmium() -> None:
    if shutil.which("osmium") is None:
        sys.exit(
            "osmium-tool is required to build city files but `osmium` was not found on PATH.\n"
            "Install it: brew install osmium-tool (macOS) or apt install osmium-tool (Debian/Ubuntu)."
        )


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def download(url: str, dest: Path) -> bool:
    """Downloads url to dest and writes <dest>.meta.json with the URL, time and Last-Modified header."""
    part = dest.with_name(dest.name + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "poland-transit-mcp-build"})
        with net.urlopen(req, timeout=1800) as r, open(part, "wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
            last_modified = r.headers.get("Last-Modified")
        part.replace(dest)
        meta = {"url": url, "downloaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_modified": last_modified, "bytes": dest.stat().st_size}
        dest.with_name(re.sub(r"(\.zip|\.osm\.pbf)$", "", dest.name) + ".meta.json").write_text(json.dumps(meta, indent=1))
        return True
    except Exception as e:  # noqa: BLE001 - a failed source is reported and the build continues
        log(f"download failed: {url}: {e}{net.tls_note(e)}")
        FAILED_DOWNLOADS.append(url)
        part.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------- OSM tag rules
# "corridor": an indoor walkway, such as the halls through Katowice station from its platforms to the city side
# (without them a walk from the station to the centre went round by the next street west, 1,712 m instead of 1,265 m)
WALK_HW = {"footway", "path", "pedestrian", "steps", "living_street", "residential", "service", "unclassified", "tertiary",
           "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "trunk", "trunk_link", "track",
           "cycleway", "bridleway", "road", "corridor"}
NO = {"no", "private"}
YES = {"yes", "designated", "permissive", "destination"}


def walkable(t: dict) -> bool:
    hw = t.get("highway")
    if hw not in WALK_HW:
        return False
    if t.get("area") == "yes" and hw not in ("pedestrian", "footway"):
        return False
    if t.get("foot") in NO or (t.get("access") in NO and t.get("foot") not in YES):
        return False
    if hw in ("trunk", "trunk_link") and t.get("foot") not in YES and t.get("sidewalk") not in ("both", "left", "right", "yes"):
        return False
    return not (t.get("motorroad") == "yes" and t.get("foot") not in YES)


SMALL_GREEN_M2 = 500
MAP_SKIP_FOOTWAY = {"sidewalk", "crossing", "link"}


def street_class(hw: str | None) -> str | None:
    if not hw:
        return None
    if re.fullmatch(r"(motorway|trunk|primary|secondary)(_link)?", hw):
        return "major"
    if hw in ("tertiary", "tertiary_link", "unclassified", "residential", "living_street", "road"):
        return "minor"
    if hw == "service":
        return "service"
    if hw in ("footway", "path", "pedestrian", "steps", "cycleway", "track", "bridleway"):
        return "path"
    return None


def place_kind(t: dict) -> str | None:
    tour, am, le = t.get("tourism"), t.get("amenity"), t.get("leisure")
    if tour in ("museum", "gallery"):
        return "museum"
    if tour in ("attraction", "viewpoint", "zoo", "theme_park", "aquarium"):
        return "attraction"
    if tour in ("hotel", "hostel", "guest_house", "motel", "apartment"):
        return "hotel"
    if am in ("theatre", "arts_centre") or le == "stadium" or t.get("natural") == "beach":
        return "attraction"
    if le in ("park", "garden", "nature_reserve"):
        return "park"
    if t.get("railway") in ("station", "halt") or t.get("public_transport") == "station" or am == "bus_station":
        return "station"
    if (t.get("historic") and t["historic"] != "no") or am in ("place_of_worship", "university") \
            or t.get("man_made") == "lighthouse" or t.get("place") == "square":
        return "landmark"
    return None


def area_layer(t: dict) -> tuple[str, str] | None:
    if t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") in ("reservoir", "basin"):
        return "water", "area"
    if t.get("leisure") in ("park", "garden", "nature_reserve"):
        return "parks", t["leisure"]
    if t.get("landuse") in ("forest", "grass", "recreation_ground", "cemetery", "meadow"):
        return "parks", t["landuse"]
    if t.get("natural") == "wood":
        return "parks", "forest"
    return None


def line_layer(t: dict) -> tuple[str, str] | None:
    if t.get("waterway") in ("river", "canal", "stream"):
        return "water", t["waterway"]
    if t.get("natural") == "coastline":
        return "water", "coastline"
    if t.get("railway") == "tram":
        return "tram", "tram"
    if t.get("railway") in ("rail", "light_rail", "subway", "narrow_gauge") and not t.get("service"):
        return "rail", t["railway"]
    return None


FEATURE_FILTER = [
    "nwr/tourism=attraction,museum,gallery,viewpoint,zoo,theme_park,aquarium,hotel,hostel,guest_house,motel,apartment",
    "nwr/historic", "nwr/amenity=place_of_worship,theatre,arts_centre,bus_station,university",
    "nwr/leisure=park,garden,stadium,nature_reserve", "nwr/natural=water,wood,beach,coastline",
    "nwr/waterway=river,canal,riverbank,stream,dock", "nwr/landuse=forest,grass,recreation_ground,cemetery,reservoir,basin,meadow",
    "nwr/railway=rail,tram,light_rail,subway,narrow_gauge,station,halt", "nwr/public_transport=station",
    "nwr/place=square", "nwr/man_made=lighthouse",
]


def geojson_lines(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.lstrip("\x1e").strip()
            if line:
                yield json.loads(line)


def ll(c) -> LatLon:
    return c[1], c[0]


class Builder:
    def __init__(self, city: CityConfig, db: sqlite3.Connection):
        self.city, self.db = city, db
        self.feature_id = 0
        self.places: list[tuple[str, str, float, float, str, dict | None]] = []  # kind, name, lat, lon, ref, extra
        self.min_lat, self.min_lon, self.max_lat, self.max_lon = city.bbox

    def in_bbox(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def add_feature(self, layer: str, cls: str, poly: bool, parts: list[list[LatLon]], tol: float = 2) -> None:
        simp = [p for p in (simplify(p, tol) for p in parts) if len(p) >= (4 if poly else 2)]
        if not simp:
            return
        a, b, c, d = bbox_of([q for p in simp for q in p])
        self.feature_id += 1
        self.db.execute("INSERT INTO features(id, layer, class, poly, coords) VALUES (?,?,?,?,?)",
                        (self.feature_id, layer, cls, 1 if poly else 0, encode_parts(simp)))
        self.db.execute("INSERT INTO features_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (?,?,?,?,?)",
                        (self.feature_id, a, c, b, d))

    def highways(self, path: Path) -> GraphArrays:
        node_idx: dict[tuple[float, float], int] = {}
        coords: list[int] = []
        eu: list[int] = []
        ev: list[int] = []
        elen: list[float] = []
        eflag: list[int] = []
        streets: dict[str, list[list[LatLon]]] = {}
        for f in geojson_lines(path):
            if f["geometry"]["type"] != "LineString":
                continue
            t = f["properties"]
            cs = f["geometry"]["coordinates"]
            pts = [ll(c) for c in cs]
            cls = street_class(t.get("highway"))
            # on the map, service roads (no layer draws them) and sidewalks/crossings (drawn already as
            # the street) are left out; all of them stay in the walking graph
            if cls and cls != "service" and (t.get("footway") or t.get("path")) not in MAP_SKIP_FOOTWAY:
                self.add_feature("streets", cls, False, [pts])
                if t.get("name") and cls != "path":
                    streets.setdefault(t["name"], []).append(pts)
            if not walkable(t):
                continue
            fl = (FLAG_BRIDGE if t.get("bridge", "no") != "no" else 0) | (FLAG_TUNNEL if t.get("tunnel", "no") != "no" else 0) \
                | (FLAG_STEPS if t.get("highway") == "steps" else 0)
            prev = -1
            for lon, lat in cs:
                key = (lon, lat)  # identical coordinates = the same OSM node
                i = node_idx.get(key)
                if i is None:
                    i = node_idx[key] = len(coords) // 2
                    coords += (round(lat * 1e6), round(lon * 1e6))
                if prev >= 0 and prev != i:
                    eu.append(prev)
                    ev.append(i)
                    elen.append(haversine(coords[2 * prev] / 1e6, coords[2 * prev + 1] / 1e6, lat, lon))
                    eflag.append(fl)
                prev = i
        log(f"raw graph: {len(coords) // 2} nodes, {len(eu)} edges")
        self._streets(streets)
        return self._graph(np.array(coords, np.int32), np.array(eu, np.int64), np.array(ev, np.int64),
                           np.array(elen, np.float64), np.array(eflag, np.uint8))

    def _graph(self, coords, eu, ev, elen, eflag) -> GraphArrays:
        n = len(coords) // 2
        # undirected, parallel edges collapsed to the shortest one
        u = np.concatenate((eu, ev))
        v = np.concatenate((ev, eu))
        w = np.concatenate((elen, elen))
        f = np.concatenate((eflag, eflag))
        order = np.lexsort((w, v, u))
        u, v, w, f = u[order], v[order], w[order], f[order]
        first = np.ones(len(u), bool)
        first[1:] = (u[1:] != u[:-1]) | (v[1:] != v[:-1])
        u, v, w, f = u[first], v[first], w[first], f[first]
        # keep the largest connected component only
        ncomp, labels = connected_components(coo_matrix((np.ones(len(u)), (u, v)), shape=(n, n)), directed=False)
        big = np.bincount(labels).argmax()
        keep = labels == big
        remap = np.full(n, -1, np.int64)
        remap[keep] = np.arange(keep.sum())
        m = keep[u] & keep[v]
        u, v, w, f = remap[u[m]], remap[v[m]], w[m], f[m]  # still sorted by u
        nn = int(keep.sum())
        off = np.zeros(nn + 1, np.uint32)
        np.add.at(off, u + 1, 1)
        off = np.cumsum(off, dtype=np.uint64).astype(np.uint32)
        log(f"walk graph: {nn} nodes (largest of {ncomp} components, {n - nn} nodes dropped), {len(u)} directed edges")
        cc = coords.reshape(-1, 2)[keep].ravel()
        return GraphArrays(cc.astype(np.int32), off, v.astype(np.uint32), w.astype(np.float32), f.astype(np.uint8))

    def _streets(self, streets: dict[str, list[list[LatLon]]]) -> None:
        """One place per street name cluster (ways of a name more than ~1 km apart are separate streets)."""
        for name, ways in streets.items():
            clusters: list[dict] = []
            for pts in ways:
                b = bbox_of(pts)
                near = next((c for c in clusters if not (b[0] > c["box"][2] + 0.01 or b[2] < c["box"][0] - 0.01
                                                        or b[1] > c["box"][3] + 0.015 or b[3] < c["box"][1] - 0.015)), None)
                if near:
                    cb = near["box"]
                    near["box"] = (min(cb[0], b[0]), min(cb[1], b[1]), max(cb[2], b[2]), max(cb[3], b[3]))
                    if len(pts) > len(near["best"]):
                        near["best"] = pts
                else:
                    clusters.append({"box": b, "best": pts})
            for c in clusters:
                mid = c["best"][len(c["best"]) // 2]
                if self.in_bbox(*mid):
                    self.places.append(("street", name, mid[0], mid[1], "street", None))

    def features(self, path: Path) -> None:
        for f in geojson_lines(path):
            t, g = f["properties"], f["geometry"]
            point = None
            if g["type"] == "Point":
                point = ll(g["coordinates"])
            elif g["type"] == "MultiPolygon":
                rings = [[ll(c) for c in ring] for poly in g["coordinates"] for ring in poly]
                outer = sorted(([ll(c) for c in poly[0]] for poly in g["coordinates"]), key=len, reverse=True)
                point = centroid(outer[0])
                al = area_layer(t)
                # lawns and verges under 500 m² are left off the map (they add rows, not information)
                small_green = al and al[1] in ("grass", "garden") and max(ring_area(o) for o in outer) < SMALL_GREEN_M2
                if al and not small_green:
                    self.add_feature(al[0], al[1], True, rings)
            elif g["type"] == "LineString":
                pts = [ll(c) for c in g["coordinates"]]
                closed = len(pts) > 3 and pts[0] == pts[-1]
                ln = line_layer(t)
                if ln:
                    self.add_feature(ln[0], ln[1], False, [pts])
                if closed and (area_layer(t) or place_kind(t)):
                    continue  # the polygon version of this closed way covers it
                point = pts[len(pts) // 2]
            kind = place_kind(t)
            if kind and t.get("name") and point and self.in_bbox(*point):
                extra = {}
                if t.get("wikipedia"):
                    extra["wikipedia"] = t["wikipedia"]
                for k in ("tourism", "historic", "amenity"):
                    if t.get(k):
                        extra[k] = t[k]
                        break
                self.places.append((kind, t["name"], point[0], point[1], f'{str(t["@type"])[0]}{t["@id"]}', extra))

    def write_places(self) -> int:
        for idx, sid, name, platform, lat, lon in self.db.execute("SELECT stop_idx, stop_id, name, platform, lat, lon FROM stops").fetchall():
            self.places.append(("stop", name, lat, lon, f"stop:{idx}", {"stop_id": sid, "platform": platform}))
        self.places.sort(key=lambda p: (p[0], p[1], 0 if p[4][:1] == "n" else 1))
        pid = 0
        kept: list[tuple] = []
        for p in self.places:
            if kept and (kept[-1][0], kept[-1][1]) != (p[0], p[1]):
                kept = []
            # same kind + name within 150 m = one place (stop poles are kept apart)
            if p[0] != "stop" and any(fast_dist(k[2], k[3], p[2], p[3]) < 150 for k in kept):
                continue
            kept.append(p)
            pid += 1
            extra = {k: v for k, v in (p[5] or {}).items() if v}
            self.db.execute("INSERT INTO places(id, kind, name, name_norm, lat, lon, ref, extra) VALUES (?,?,?,?,?,?,?,?)",
                            (pid, p[0], p[1], fold(p[1]), p[2], p[3], p[4], json.dumps(extra, ensure_ascii=False) if extra else None))
            self.db.execute("INSERT INTO places_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (?,?,?,?,?)", (pid, p[2], p[2], p[3], p[3]))
        return pid


def download_feeds(city: CityConfig, raw: Path) -> None:
    for f in city.feeds:
        if f.local:  # not downloadable: made by a local script
            log(f"{f.name}: made locally ({f.local}), not downloaded")
            continue
        url = f.url
        if f.latest_link:  # dataset page listing dated files, newest first
            try:
                req = urllib.request.Request(f.url, headers={"User-Agent": "poland-transit-mcp-build"})
                page = net.urlopen(req, timeout=60).read().decode("utf-8", "replace")
                m = re.search(f.latest_link, page)
                url = urllib.parse.urljoin(f.url, m[1]) if m else None
            except Exception as e:  # noqa: BLE001
                log(f"could not read {f.url}: {e}{net.tls_note(e)}")
                FAILED_DOWNLOADS.append(f.url)
                url = None
        if url:
            log(f"downloading {url}")
            download(url, raw / city.id / f"{f.key}.zip")


def import_feeds(db: sqlite3.Connection, city: CityConfig, raw: Path, graph: WalkGraph | None, skip: bool = False) -> list[dict]:
    feeds = []
    for f in city.feeds:
        if skip:
            feeds.append({"key": f.key, "name": f.name, "url": f.url, "licence": f.licence, "attribution": f.attribution, "status": "skipped"})
            continue
        meta_file = raw / city.id / f"{f.key}.meta.json"
        src = raw / city.id / (f.key if f.local else f"{f.key}.zip")
        if f.local and not src.exists():
            log(f"GTFS {f.key}: {src} missing; make it with: {f.local}")
        feeds.append(import_feed(db, src, f, graph, log, json.loads(meta_file.read_text()) if meta_file.exists() else None))
    return feeds


def _agency_zones(src: Path) -> set[str]:
    """The agency_timezone values of a feed (a zip or a folder)."""
    try:
        if src.is_dir():
            text = (src / "agency.txt").read_text(encoding="utf-8-sig")
        else:
            with zipfile.ZipFile(src) as z:
                text = z.read("agency.txt").decode("utf-8-sig")
    except (OSError, KeyError, zipfile.BadZipFile):
        return set()
    return {r.get("agency_timezone", "").strip() for r in csv.DictReader(io.StringIO(text))} - {""}


def clock_problems(city: CityConfig, raw: Path, feeds: list[dict]) -> list[str]:
    """Feeds whose times would be read on the wrong clock. The server reads every file on Polish time
    (timeutil.TZ, and gtfs.WARSAW for feeds in UTC), so the data set's zone and each feed's agency_timezone
    must keep Europe/Warsaw's UTC offsets over the feed's dates. Nothing per stop yet."""
    out = []
    ok = [(f, rec) for f, rec in zip(city.feeds, feeds) if rec.get("status") == "ok"]
    today = datetime.now(timezone.utc).date()
    span = ((min(r["start_date"] for _, r in ok), max(r["end_date"] for _, r in ok)) if ok else
            (int(today.strftime("%Y%m%d")), int((today + timedelta(days=400)).strftime("%Y%m%d"))))
    if not same_clock(city.timezone, *span):  # also with no timetable: the file still states its zone
        return [f"{city.id}: time zone {city.timezone!r} is unknown or does not keep Polish time over {span[0]}..{span[1]} "
                "(only zones whose clock agrees with Europe/Warsaw are supported)"]
    for f, rec in ok:
        span = (rec["start_date"], rec["end_date"])
        if f.utc_times:  # converted to Polish time per date at import
            continue
        src = raw / city.id / (f.key if f.local else f"{f.key}.zip")
        for z in sorted(_agency_zones(src)):
            if not same_clock(z, *span):
                out.append(f"GTFS {f.key}: agency_timezone {z!r} is unknown or does not keep Polish time over {span[0]}..{span[1]}")
    return out


def dataset_meta(city: CityConfig) -> dict[str, str]:
    """What the server needs from the data set's definition, kept in the file so it needs no definitions file."""
    return {"timezone": city.timezone, "notes": json.dumps(list(city.notes), ensure_ascii=False),
            "intercity_notes": json.dumps(list(city.intercity_notes), ensure_ascii=False)}


def refuse_wrong_clocks(db: sqlite3.Connection, tmp: Path, city: CityConfig, raw: Path, feeds: list[dict]) -> None:
    problems = clock_problems(city, raw, feeds)
    if problems:
        db.close()
        tmp.unlink(missing_ok=True)
        for line in problems:
            log(f"REFUSED: {line}")
        sys.exit(f"not built: {city.id} would be read on the wrong clock (see above)")


def finish(db: sqlite3.Connection, tmp: Path, out: Path, feeds: list[dict], force: bool = False) -> None:
    """Writes the new file in place of the old one, unless a timetable got worse (build/safeguard.py)."""
    db.execute("COMMIT")
    db.execute("ANALYZE")
    db.execute("VACUUM")
    db.close()
    current = read_feeds(out)
    worse = worse_feeds(current, feeds)
    failed = [f"WARNING: download failed, so this build used the previous file, if any: {u}" for u in FAILED_DOWNLOADS]
    if worse and not force:
        rejected = out.with_name(out.name + ".rejected")
        tmp.replace(rejected)
        for line in worse:
            log(f"REFUSED: {line}")
        log(f"kept the last good file {out}; the new one is {rejected}. Fix the feed, or rerun with --force to "
            "replace it anyway")
        for line in failed + [f"WARNING: kept file: {w}" for w in expiry_warnings(current or [])]:
            log(line)
        sys.exit(EXIT_REJECTED)
    for line in worse:
        log(f"replaced anyway (--force): {line}")
    tmp.replace(out)
    out.with_name(out.name + ".rejected").unlink(missing_ok=True)
    log(f"wrote {out}: {out.stat().st_size / 1e6:.1f} MB")
    for line in failed + [f"WARNING: {w}" for w in expiry_warnings(feeds)]:
        log(line)


def build_national(city: CityConfig, root: Path, fetch: bool, force: bool = False) -> None:
    """The national layer: the feeds' stations, trains and coaches, with foot transfers between
    nearby stations along a straight line × 1.3. No map, no walking graph, no places."""
    raw, out_dir = root / "raw", root / "cities"
    for d in (raw / city.id, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    if fetch:
        download_feeds(city, raw)
    out = out_dir / f"{city.id}.sqlite"
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp, isolation_level=None)
    db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;" + SCHEMA)
    db.execute("BEGIN")
    feeds = import_feeds(db, city, raw, None)
    refuse_wrong_clocks(db, tmp, city, raw, feeds)
    log(f"transfers: {compute_transfers(db, None)}")
    lat0, lat1, lon0, lon1 = db.execute("SELECT MIN(lat), MAX(lat), MIN(lon), MAX(lon) FROM stops").fetchone()
    bbox = [round(lat0, 3), round(lon0, 3), round(lat1, 3), round(lon1, 3)] if lat0 is not None else list(city.bbox)
    for k, v in {
        "city": city.id, "name": city.name, "bbox": json.dumps(bbox), "kind": "national",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "osm": json.dumps({}), "feeds": json.dumps(feeds, ensure_ascii=False), "schema_version": "1",
        **dataset_meta(city),
    }.items():
        db.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, v))
    finish(db, tmp, out, feeds, force)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="poland-transit-mcp build")
    ap.add_argument("city", help="a built-in data set (config.py) or one from --datasets")
    ap.add_argument("--download", action="store_true", help="fetch the OSM extract and GTFS feeds from their sources first")
    ap.add_argument("--skip-gtfs", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="replace the city file even when a timetable is missing, skipped or much smaller than in the current one")
    ap.add_argument("--data-dir", default="data", help="root for raw/, work/ and cities/ (default ./data)")
    ap.add_argument("--datasets", metavar="FILE", default=None,
                    help=f"JSON file defining more data sets (datasets.py); default ${datasets.ENV}")
    a = ap.parse_args(argv)
    try:
        known = datasets.registry(a.datasets)
    except ValueError as e:
        ap.error(str(e))
    if a.city not in known:
        ap.error(f"unknown data set {a.city!r}; known: {', '.join(sorted(known))}")
    city = known[a.city]
    root = Path(a.data_dir).resolve()
    if city.national:
        build_national(city, root, a.download, a.force)
        return
    require_osmium()  # fail fast, before any download, with an install hint
    raw, work, out_dir = root / "raw", root / "work" / city.id, root / "cities"
    for d in (raw / "osm", raw / city.id, work, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    osm_file = raw / "osm" / f"{city.osm_region}.osm.pbf"
    if a.download:
        for tpl in OSM_SOURCES:
            log(f"downloading {osm_url(tpl, city.osm_region)}")
            if download(osm_url(tpl, city.osm_region), osm_file):
                break
        download_feeds(city, raw)
    if not osm_file.exists():
        sys.exit(f"missing {osm_file} (run with --download)")

    min_lat, min_lon, max_lat, max_lon = city.bbox
    city_pbf, hw_pbf, feat_pbf = work / "city.osm.pbf", work / "hw.osm.pbf", work / "feat.osm.pbf"
    hw_json, feat_json = work / "hw.geojsonseq", work / "feat.geojsonseq"
    walk_cfg, feat_cfg = work / "walk-cfg.json", work / "feat-cfg.json"
    walk_cfg.write_text(json.dumps({"attributes": {"type": True, "id": True}, "linear_tags": True, "area_tags": False}))
    feat_cfg.write_text(json.dumps({"attributes": {"type": True, "id": True}, "linear_tags": True, "area_tags": True}))
    log("osmium extract")
    run("osmium", "extract", "-b", f"{min_lon},{min_lat},{max_lon},{max_lat}", "-s", "smart", "--overwrite", "-o", str(city_pbf), str(osm_file))
    ts = re.search(r"osmosis_replication_timestamp=(\S+)", run("osmium", "fileinfo", str(osm_file)))
    log("osmium tags-filter/export highways")
    run("osmium", "tags-filter", str(city_pbf), "w/highway", "--overwrite", "-o", str(hw_pbf))
    run("osmium", "export", str(hw_pbf), "-c", str(walk_cfg), "-f", "geojsonseq", "--overwrite", "-o", str(hw_json))
    log("osmium tags-filter/export features")
    run("osmium", "tags-filter", str(city_pbf), *FEATURE_FILTER, "--overwrite", "-o", str(feat_pbf))
    run("osmium", "export", str(feat_pbf), "-c", str(feat_cfg), "-f", "geojsonseq", "--overwrite", "-o", str(feat_json))

    out = out_dir / f"{city.id}.sqlite"
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp, isolation_level=None)
    db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;" + SCHEMA)
    db.execute("BEGIN")
    b = Builder(city, db)
    log("loading highways")
    ga = b.highways(hw_json)
    for k, v in ga.blobs().items():
        db.execute("INSERT INTO blobs(name, data) VALUES (?, ?)", (k, v))
    graph = WalkGraph(ga, list(city.bbox))
    log("loading features")
    b.features(feat_json)
    log(f"features: {b.feature_id}")
    feeds = import_feeds(db, city, raw, graph, a.skip_gtfs)
    refuse_wrong_clocks(db, tmp, city, raw, feeds)
    log("transfers")
    log(f"transfers: {compute_transfers(db, graph)}")
    log(f"places: {b.write_places()}")
    osm_meta_file = raw / "osm" / f"{city.osm_region}.meta.json"
    osm_dl = json.loads(osm_meta_file.read_text()) if osm_meta_file.exists() else {}
    for k, v in {
        "city": city.id, "name": city.name, "bbox": json.dumps(list(city.bbox)),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "osm": json.dumps({"region": city.osm_region, "source": osm_dl.get("url", osm_url(OSM_SOURCES[1], city.osm_region)),
                           "downloaded_at": osm_dl.get("downloaded_at"), "timestamp": ts[1] if ts else "unknown",
                           "attribution": OSM_ATTRIBUTION}),
        "feeds": json.dumps(feeds, ensure_ascii=False), "schema_version": "1",
        **dataset_meta(city),
    }.items():
        db.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, v))
    finish(db, tmp, out, feeds, a.force)


if __name__ == "__main__":
    main()
