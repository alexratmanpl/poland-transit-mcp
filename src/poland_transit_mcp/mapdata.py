"""map_extract: simplified geometry of map layers inside a bounding box."""
from __future__ import annotations

from dataclasses import dataclass

from .city import City
from .geo import LatLon, decode_parts, haversine, pt, ring_area, simplify

LAYERS = ("water", "parks", "streets_major", "streets_minor", "paths", "rail", "tram", "stops")
DEFAULT_LAYERS = ("water", "parks", "streets_major", "streets_minor", "rail", "tram", "stops")  # paths on request
PRIORITY = ("water", "streets_major", "rail", "tram", "stops", "parks", "streets_minor", "paths")  # when the budget runs out
MAX_POINTS = 4000
MAX_FEATURES = 6000
MAX_DIAG_M = 18000


@dataclass
class Feature:
    layer: str
    cls: str | None
    kind: str  # line | polygon | point
    coords: list[list[LatLon]]
    name: str | None = None
    id: str | None = None


def _sql_layer(layer: str) -> tuple[str, str | None]:
    return {"streets_major": ("streets", "major"), "streets_minor": ("streets", "minor"),
            "paths": ("streets", "path")}.get(layer, (layer, None))


def map_features(city: City, bbox, layers, tol_m: float | None = None) -> tuple[list[Feature], list[str]]:
    a, b, c, d = bbox
    if not (a < c and b < d):
        raise ValueError("bbox must be [minLat, minLon, maxLat, maxLon]")
    diag = haversine(a, b, c, d)
    if diag > MAX_DIAG_M:
        raise ValueError(f"bbox too large ({diag / 1000:.1f} km diagonal; max {MAX_DIAG_M / 1000:.0f} km)")
    tol = tol_m if tol_m is not None else max(2.0, diag / 1500)
    feats: list[Feature] = []
    truncated: list[str] = []
    for layer in layers:
        if layer == "stops":
            continue
        name, cls = _sql_layer(layer)
        q = ("SELECT f.class, f.poly, f.coords FROM features f JOIN features_rtree r ON r.id = f.id "
             "WHERE r.max_lat >= ? AND r.min_lat <= ? AND r.max_lon >= ? AND r.min_lon <= ? AND f.layer = ?"
             + (" AND f.class = ?" if cls else "") + " LIMIT ?")
        rows = city.db.execute(q, [a, c, b, d, name, *([cls] if cls else []), MAX_FEATURES + 1]).fetchall()
        if len(rows) > MAX_FEATURES:
            truncated.append(layer)
        for klass, poly, blob in rows[:MAX_FEATURES]:
            parts = [simplify(p, tol) for p in decode_parts(blob)]
            parts = [p for p in parts if len(p) >= (4 if poly else 2)]
            if parts:
                feats.append(Feature(layer, klass, "polygon" if poly else "line", parts))
    if "stops" in layers and city.has_transit:
        for sid, nm, lat, lon in city.db.execute(
                "SELECT s.stop_id, s.name, s.lat, s.lon FROM stops s JOIN stops_rtree r ON r.id = s.stop_idx "
                "WHERE r.min_lat >= ? AND r.max_lat <= ? AND r.min_lon >= ? AND r.max_lon <= ? LIMIT 3000", (a, c, b, d)):
            feats.append(Feature("stops", None, "point", [[(lat, lon)]], nm, sid))
    return feats, truncated


def map_extract(city: City, bbox, layers) -> dict:
    """Detail scales with the bbox; lines/polygons grouped per layer; at most MAX_POINTS points,
    filled in PRIORITY order; cut layers are named in the note."""
    diag = haversine(*bbox)
    tol = max(3.0, diag / 400)
    ordered = sorted(set(layers), key=PRIORITY.index)  # a repeated layer would repeat its query and double its output
    feats, truncated = map_features(city, bbox, ordered, tol)
    out: dict = {}
    cut = set(truncated)
    points = 0
    for f in feats:
        if f.kind == "polygon" and ring_area(f.coords[0]) < (3 * tol) ** 2:
            continue
        n = sum(len(p) for p in f.coords)
        if points + n > MAX_POINTS:
            cut.add(f.layer)
            continue
        points += n
        if f.kind == "point":
            out.setdefault("stops", []).append({"id": f.id, "name": f.name, "at": pt(*f.coords[0][0])})
            continue
        g = out.setdefault(f.layer, {})
        parts = [[pt(*q) for q in p] for p in f.coords]
        if f.kind == "polygon":
            g.setdefault("polygons", []).append(parts)
        else:
            g.setdefault("lines", []).extend(parts)
    return {"bbox": list(bbox), "simplified_m": round(tol), "points": points, "layers": out,
            "note": (f"incomplete layers (point budget {MAX_POINTS}): {', '.join(sorted(cut, key=PRIORITY.index))}; "
                     "use a smaller bbox or fewer layers") if cut else None}
