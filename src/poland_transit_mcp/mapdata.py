"""map_extract: simplified geometry of map layers inside a bounding box."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import Refused
from .city import City
from .geo import LatLon, clip_line, clip_ring, decode_parts, fast_dist, haversine, pt, ring_area, simplify

LAYERS = ("water", "parks", "streets_major", "streets_minor", "paths", "rail", "tram", "stops")
DEFAULT_LAYERS = ("water", "parks", "streets_major", "streets_minor", "rail", "tram", "stops")  # paths on request
PRIORITY = ("water", "streets_major", "rail", "tram", "stops", "parks", "streets_minor", "paths")  # when the budget runs out
MAX_POINTS = 4000  # the most map_extract returns when asked (max_points)
DEFAULT_POINTS = 1000  # map_extract's default: about 20,000 characters
STOP_POINTS = 3  # a stop (name, id, point) is about as long as three points
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


def check_size(bbox) -> float:
    """The diagonal of a caller's box in metres; a box over MAX_DIAG_M is refused."""
    a, b, c, d = bbox
    if not (a < c and b < d):
        raise Refused("bbox must be [minLat, minLon, maxLat, maxLon]")
    diag = haversine(a, b, c, d)
    if diag > MAX_DIAG_M:
        raise Refused(f"bbox too large ({diag / 1000:.1f} km diagonal; max {MAX_DIAG_M / 1000:.0f} km)")
    return diag


def map_features(city: City, bbox, layers, tol_m: float | None = None,
                 max_diag_m: float = MAX_DIAG_M) -> tuple[list[Feature], list[str]]:
    """`max_diag_m` is above MAX_DIAG_M only for a frame the server widened itself (render_map's 4:1 limit), after
    the caller's box passed check_size."""
    a, b, c, d = bbox
    if not (a < c and b < d):
        raise Refused("bbox must be [minLat, minLon, maxLat, maxLon]")
    diag = haversine(a, b, c, d)
    if diag > max_diag_m:
        raise Refused(f"bbox too large ({diag / 1000:.1f} km diagonal; max {MAX_DIAG_M / 1000:.0f} km)")
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


def _line_m(p: list[LatLon]) -> float:
    return sum(fast_dist(*p[i], *p[i + 1]) for i in range(len(p) - 1))


def map_extract(city: City, bbox, layers, max_points: int = DEFAULT_POINTS) -> dict:
    """Map layers of a bbox as lines and polygons cut at the box, grouped per layer. The detail is coarsened (up to 8
    times) until the asked layers fit `max_points`; if they still do not, whole layers are left out from the lowest
    priority up (never a few pieces of one) and named in the note."""
    diag = haversine(*bbox)
    base = max(3.0, diag / 400)
    ordered = sorted(set(layers), key=PRIORITY.index)  # a repeated layer would repeat its query and double its output
    raw, truncated = map_features(city, bbox, ordered, 0.5)
    shapes: list[tuple[str, str, list[list[LatLon]]]] = []
    stops = []
    for f in raw:
        if f.kind == "point":
            stops.append({"id": f.id, "name": f.name, "at": pt(*f.coords[0][0])})
        elif f.kind == "polygon":  # every ring: outer parts and holes (an island in a river), filled even-odd
            rings = [r + ([r[0]] if r[0] != r[-1] else []) for r in (clip_ring(x, bbox) for x in f.coords) if len(r) >= 3]
            if rings:
                shapes.append((f.layer, "polygon", rings))
        else:
            for part in f.coords:
                shapes += [(f.layer, "line", [run]) for run in clip_line(part, bbox)]

    def at(tol: float) -> dict[str, tuple[int, dict | list]]:
        """Each layer's output and its cost in points at this tolerance."""
        per: dict[str, tuple[int, dict | list]] = {}
        for layer in ordered:
            if layer == "stops":
                per[layer] = (len(stops) * STOP_POINTS, stops)
                continue
            g: dict = {}
            n = 0
            for lay, kind, parts in shapes:
                if lay != layer:
                    continue
                if kind == "polygon":  # rings too small to see at this detail are left out
                    rings = [r for r in (simplify(x, tol) for x in parts) if len(r) >= 4 and ring_area(r) >= (3 * tol) ** 2]
                    if rings:
                        g.setdefault("polygons", []).append([[pt(*q) for q in r] for r in rings])
                        n += sum(map(len, rings))
                    continue
                part = simplify(parts[0], tol)
                if len(part) < 2 or _line_m(part) < 2 * tol:  # stubs too short to see at this detail
                    continue
                g.setdefault("lines", []).append([pt(*q) for q in part])
                n += len(part)
            per[layer] = (n, g)
        return per

    def fill(per: dict) -> tuple[dict, list[str], int]:
        """Whole layers by priority while they fit; from the first that does not, it and every lower one are left out
        (a lower layer never stays in place of a higher one): (layers, left out, points)."""
        out: dict = {}
        left: list[str] = []
        used = points = 0
        for layer in ordered:
            cost, data = per[layer]
            if not data:
                continue
            if left or used + cost > max_points:
                left.append(layer)
                continue
            used += cost
            points += len(data) if layer == "stops" else cost
            out[layer] = data
        return out, left, points

    # the finest detail that keeps every layer; failing that, the one that keeps the most layers
    best = None
    for k in (1, 2, 4, 8):
        tol = base * k
        out, left, points = fill(at(tol))
        if best is None or len(left) < len(best[2]):
            best = (tol, out, left, points)
        if not left:
            break
    tol, out, left, points = best
    notes = []
    if left:
        notes.append(f"left out to stay within {max_points} points: {', '.join(left)}; use a smaller bbox, fewer "
                     f"layers, or a larger max_points (up to {MAX_POINTS})")
    if truncated:
        notes.append(f"incomplete (over {MAX_FEATURES} features in the box): {', '.join(truncated)}")
    return {"bbox": list(bbox), "simplified_m": round(tol), "points": points, "layers": out,
            "note": "; ".join(notes) or None}
