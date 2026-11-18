"""render_map: an SVG string of map layers plus an optional plan_trip itinerary. Deliberately minimal:
one neutral style, no raster, no tiles, labels only for itinerary stops and trip endpoints."""
from __future__ import annotations

import math
import re
from html import escape

from .errors import Refused
from .city import City
from .geo import haversine, ring_area
from .mapdata import MAX_DIAG_M, check_size, map_features

DEFAULT_THEME = {
    "background": "#f7f6f2", "water": "#a9cbe8", "parks": "#cfe3c1", "streets_major": "#ffffff",
    "streets_minor": "#ffffff", "paths": "#d8d4cc", "casing": "#d2cdc3", "rail": "#8a8a8a", "tram": "#b0a7a7",
    "stop": "#ffffff", "stop_stroke": "#444444", "walk": "#333333", "text": "#222222", "halo": "#ffffff",
    "start": "#2a9d8f", "end": "#e76f51",
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$|^[a-z]{3,20}$")


MAX_ASPECT = 4
# widening the shorter side to a quarter of the longer one lengthens the diagonal by at most sqrt(1 + 1/16)
WIDENED = 1.04
# a trip's own frame stays below the map limit even after a thin frame is widened to MAX_ASPECT (at most +3%)
TRIP_DIAG_M = MAX_DIAG_M * 0.95


def _padded(a: float, b: float, c: float, d: float) -> tuple[float, float, float, float]:
    m_lat, m_lon = max((c - a) * 0.08, 0.002), max((d - b) * 0.08, 0.003)
    return a - m_lat, b - m_lon, c + m_lat, d + m_lon


def trip_frame(pts: list) -> tuple[tuple[float, float, float, float], bool]:
    """The frame for a trip's points (in travel order) and whether the whole trip fits. A trip longer than one map
    gets the part from its start that fits within the map limit, at normal detail; the rest is marked at the
    edge with its distance."""
    a = c = pts[0][0]
    b = d = pts[0][1]
    for lat, lon in pts:
        box = (min(a, lat), min(b, lon), max(c, lat), max(d, lon))
        pa, pb, pc, pd = _padded(*box)
        if haversine(pa, pb, pc, pd) > TRIP_DIAG_M:
            return _padded(a, b, c, d), False
        a, b, c, d = box
    return _padded(a, b, c, d), True


def _inside(p, box) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def exit_point(pts: list, box) -> list[float] | None:
    """Where the trip leaves the frame for the last time, on the frame's edge; None when it ends inside."""
    if _inside(pts[-1], box):
        return None
    i = max((k for k, p in enumerate(pts) if _inside(p, box)), default=None)
    if i is None:
        return None
    (y0, x0), (y1, x1) = pts[i], pts[i + 1]
    t = 1.0  # the share of the segment inside the frame
    for lo, hi, v0, v1 in ((box[0], box[2], y0, y1), (box[1], box[3], x0, x1)):
        if v1 != v0:
            for edge in (lo, hi):
                k = (edge - v0) / (v1 - v0)
                if 0 <= k < t:
                    t = k
    return [y0 + (y1 - y0) * t, x0 + (x1 - x0) * t]


# the longest text drawn: a line badge and a place label. Text comes from the caller's itinerary, and drawing it
# costs time with the square of its length (a 500,000-character line label held the server for 5 minutes)
MAX_LINE, MAX_NAME = 12, 60


def _short(text, n: int) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _color(c, fallback: str) -> str:
    return c if isinstance(c, str) and _COLOR.fullmatch(c) else fallback


def render_map(city: City, bbox=None, itinerary: dict | None = None, width: int = 800, style: dict | None = None,
               notes: list[str] | None = None) -> str:
    """The SVG. `notes` (when given) receives sentences for the answer, e.g. that the trip goes beyond the map."""
    th = dict(DEFAULT_THEME)
    for k, v in (style or {}).items():
        th[k] = _color(v, th.get(k, "#000"))
    legs = [l for l in (itinerary or {}).get("legs", []) if l.get("path")]
    pts = [p for l in legs for p in l["path"]]
    if bbox is None:
        if not pts:
            raise Refused("itinerary has no leg paths: pass its `trip` reference instead, or an itinerary from plan_trip with detail=full, or a bbox")
        bbox, _ = trip_frame(pts)
    a, b, c, d = bbox
    if not (a < c and b < d):
        raise Refused("bbox is [minLat, minLon, maxLat, maxLon] with min below max")
    check_size(bbox)  # the box asked for: a thin one under the limit stays allowed after it is widened below
    W = int(width)
    cos = math.cos(math.radians((a + c) / 2))
    # A map is at most MAX_ASPECT times as tall as wide, or as wide as tall: a thinner box is widened around its
    # centre. Without it a valid 18 km x 7 m box asked for millions of pixels, and a server-side PNG for gigabytes.
    north, east = c - a, (d - b) * cos
    if north > MAX_ASPECT * east:
        grow = (north / MAX_ASPECT - east) / cos / 2
        b, d = b - grow, d + grow
    elif east > MAX_ASPECT * north:
        grow = (east / MAX_ASPECT - north) / 2
        a, c = a - grow, c + grow
    bbox = (a, b, c, d)
    scale = W / ((d - b) * cos)
    H = round((c - a) * scale)
    m_per_px = (d - b) * cos * 111320 / W

    def X(lon) -> int:
        return round((lon - b) * cos * scale)

    def Y(lat) -> int:
        return round((c - lat) * scale)

    def path_d(pts, close=False) -> str:
        out, last = [], None
        for p in pts:
            xy = f"{X(p[1])} {Y(p[0])}"
            if xy != last:
                out.append(("L" if out else "M") + xy)
            last = xy
        return "".join(out) + ("Z" if close else "")

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif">',
           f'<rect width="100%" height="100%" fill="{th["background"]}"/>']
    # fewer layers when zoomed out: footpaths below 3 m/px, minor streets below 5 m/px
    layers = ["parks", "water", *(["paths"] if m_per_px < 3 else []), *(["streets_minor"] if m_per_px < 5 else []),
              "streets_major", "rail", "tram"]
    if not legs and m_per_px < 6:
        layers.append("stops")
    feats, _ = map_features(city, bbox, layers, max(2.0, m_per_px), MAX_DIAG_M * WIDENED)
    # the smallest area drawn: a 4 px square; parks 10 px (a smaller patch of green adds bytes, not information)
    min_area = {"parks": (m_per_px * 10) ** 2}
    widths = {"streets_major": 3.2, "streets_minor": 1.6, "paths": 0.7, "rail": 1.4, "tram": 1.1}
    for layer in layers:
        fs = [f for f in feats if f.layer == layer]
        if not fs:
            continue
        if layer == "stops":
            svg.append(f'<g fill="{th["stop"]}" stroke="{th["stop_stroke"]}" stroke-width="1">')
            svg += [f'<circle cx="{X(f.coords[0][0][1])}" cy="{Y(f.coords[0][0][0])}" r="2.5"><title>{escape(f.name or "")}</title></circle>' for f in fs]
            svg.append("</g>")
            continue
        polys = [f for f in fs if f.kind == "polygon" and ring_area(f.coords[0]) >= min_area.get(layer, (m_per_px * 4) ** 2)
                 and not (m_per_px > 4 and f.cls in ("grass", "meadow"))]
        lines = [f for f in fs if f.kind == "line" and any(
            len(p) > 2 or abs(p[0][0] - p[-1][0]) + abs(p[0][1] - p[-1][1]) > 3 * m_per_px / 111320 for p in f.coords)]
        if polys:
            dd = "".join(path_d(p, True) for f in polys for p in f.coords)
            svg.append(f'<path fill="{th[layer]}" fill-rule="evenodd" stroke="none" d="{dd}"/>')
        if lines:
            dd = "".join(path_d(p) for f in lines for p in f.coords)
            w = 1.5 if layer == "water" else widths.get(layer, 1.0)
            if layer.startswith("streets"):  # casing and fill share one path definition
                common = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
                svg.append(f'<defs><path id="{layer}" d="{dd}"/></defs>')
                svg.append(f'<use href="#{layer}" {common} stroke="{th["casing"]}" stroke-width="{w + 1.2:g}"/>'
                           f'<use href="#{layer}" {common} stroke="{th[layer]}" stroke-width="{w:g}"/>')
            else:
                dash = ' stroke-dasharray="6 3"' if layer == "rail" else ""
                svg.append(f'<path fill="none" stroke="{th[layer]}" stroke-width="{w:g}" stroke-linecap="round" '
                           f'stroke-linejoin="round"{dash} d="{dd}"/>')
    labels, stops_drawn = [], []
    for leg in legs:
        if leg.get("mode") == "walk":
            svg.append(f'<path fill="none" stroke="{th["walk"]}" stroke-width="2.4" stroke-dasharray="0.1 5" '
                       f'stroke-linecap="round" d="{path_d(leg["path"])}"/>')
            continue
        col = _color(leg.get("color"), "#1F5FAD")
        dd = path_d(leg["path"])
        svg.append(f'<path fill="none" stroke="{th["halo"]}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" d="{dd}"/>')
        svg.append(f'<path fill="none" stroke="{col}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" d="{dd}"/>')
        seen_here = [p for p in leg["path"] if _inside(p, bbox)] or leg["path"]  # the badge on the part drawn
        mid = seen_here[len(seen_here) // 2]
        label = escape(_short(leg.get("line", "?"), MAX_LINE))
        bw = 10 + len(label) * 7
        svg.append(f'<g transform="translate({X(mid[1])} {Y(mid[0])})"><rect x="{-bw / 2:g}" y="-9" width="{bw}" height="18" rx="4" '
                   f'fill="{col}" stroke="{th["halo"]}" stroke-width="1.5"/><text text-anchor="middle" y="4.5" font-size="12" '
                   f'font-weight="bold" fill="#fff">{label}</text></g>')
        for s in (leg.get("from"), leg.get("to")):
            if s and s.get("at"):
                stops_drawn.append(s["at"])
                labels.append((s["at"], _short(s.get("name", ""), MAX_NAME)))
    svg += [f'<circle cx="{X(p[1])}" cy="{Y(p[0])}" r="4.5" fill="{th["stop"]}" stroke="{th["stop_stroke"]}" stroke-width="1.6"/>'
            for p in stops_drawn]
    if legs:
        first = legs[0].get("from", {}).get("at") or legs[0]["path"][0]
        last = legs[-1].get("to", {}).get("at") or legs[-1]["path"][-1]
        svg.append(f'<circle cx="{X(first[1])}" cy="{Y(first[0])}" r="7" fill="{th["start"]}" stroke="{th["halo"]}" stroke-width="2"/>')
        svg.append(f'<circle cx="{X(last[1])}" cy="{Y(last[0])}" r="7" fill="{th["end"]}" stroke="{th["halo"]}" stroke-width="2"/>')
        labels += [(first, _short(legs[0].get("from", {}).get("name", "Start"), MAX_NAME)),
                   (last, _short(legs[-1].get("to", {}).get("name", "End"), MAX_NAME))]
    placed: list[tuple[float, float, float, float]] = []
    exit_at = exit_point(pts, bbox) if pts else None
    if exit_at:  # the trip ends beyond this map: an arrow at the edge towards its end, with the distance from there
        end = pts[-1]
        km = haversine(exit_at[0], exit_at[1], end[0], end[1]) / 1000
        dx, dy = X(end[1]) - X(exit_at[1]), Y(end[0]) - Y(exit_at[0])
        n = math.hypot(dx, dy) or 1
        ux, uy = dx / n, dy / n
        tx, ty = min(max(X(exit_at[1]), 14), W - 14), min(max(Y(exit_at[0]), 14), H - 14)  # tip, inside the frame
        bx, by = tx - ux * 14, ty - uy * 14
        svg.append(f'<path d="M{tx:.0f} {ty:.0f}L{bx - uy * 7:.0f} {by + ux * 7:.0f}L{bx + uy * 7:.0f} {by - ux * 7:.0f}Z" '
                   f'fill="{th["end"]}" stroke="{th["halo"]}" stroke-width="1.5"/>')
        name = legs[-1].get("to", {}).get("name")
        if not isinstance(name, str) or not name or name.startswith("["):  # a point has no name of its own
            name = "End"
        name = _short(name, MAX_NAME)
        label = f"{name}: {km:.0f} km" if km >= 1 else f"{name}: {km * 1000:.0f} m"
        w = 6.2 * len(label)
        lx = min(max(bx - ux * 8 - w / 2, 4), W - 4 - w)
        ly = min(max(by - uy * 8 + 4, 14), H - 6)
        placed.append((lx, ly - 10, lx + w, ly + 2))
        svg.append(f'<text x="{lx:.0f}" y="{ly:.0f}" font-size="11" font-weight="bold" fill="{th["text"]}" '
                   f'stroke="{th["halo"]}" stroke-width="3" paint-order="stroke">{escape(label)}</text>')
        if notes is not None:
            notes.append(f"the trip ends beyond this map: the arrow at the edge points to {name}, {label.split(': ')[1]} "
                         f"in a straight line (one map covers at most {MAX_DIAG_M / 1000:.0f} km)")
    # labels: trip endpoints first, then stops; each tries four spots around its point and takes the
    # first that neither overlaps an earlier label nor leaves the frame
    seen = set()
    ordered = labels[-2:] + labels[:-2] if legs else labels
    for p, t in ordered:
        key = (t, round(p[0] * 2000), round(p[1] * 2000))
        if not t or key in seen:
            continue
        seen.add(key)
        x, y = X(p[1]), Y(p[0])
        w, h = 6.2 * len(t), 12
        spots = [(x + 8, y - 6, "start"), (x + 8, y + 14, "start"), (x - 8, y - 6, "end"), (x - 8, y + 14, "end")]
        best = None
        for tx, ty, anchor in spots:
            box = (tx if anchor == "start" else tx - w, ty - h + 2, tx + w if anchor == "start" else tx, ty + 2)
            inside = box[0] >= 2 and box[2] <= W - 2 and box[1] >= 2 and box[3] <= H - 2
            clash = any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed)
            if inside and not clash:
                best = (tx, ty, anchor, box)
                break
            if best is None and inside:
                best = (tx, ty, anchor, box)  # fallback: inside the frame even if it touches another label
        if best is None:
            continue
        tx, ty, anchor, box = best
        placed.append(box)
        a = ' text-anchor="end"' if anchor == "end" else ""
        svg.append(f'<text x="{tx:g}" y="{ty:g}"{a} font-size="11" fill="{th["text"]}" stroke="{th["halo"]}" '
                   f'stroke-width="3" paint-order="stroke">{escape(t)}</text>')
    svg.append(f'<text x="{W - 4}" y="{H - 4}" text-anchor="end" font-size="9" fill="{th["text"]}" opacity="0.7">© OpenStreetMap contributors</text>')
    svg.append("</svg>")
    return "".join(svg)
