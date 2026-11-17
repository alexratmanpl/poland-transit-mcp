"""render_map: an SVG string of map layers plus an optional plan_trip itinerary. Deliberately minimal:
one neutral style, no raster, no tiles, labels only for itinerary stops and trip endpoints."""
from __future__ import annotations

import math
import re
from html import escape

from .city import City
from .geo import ring_area
from .mapdata import map_features

DEFAULT_THEME = {
    "background": "#f7f6f2", "water": "#a9cbe8", "parks": "#cfe3c1", "streets_major": "#ffffff",
    "streets_minor": "#ffffff", "paths": "#d8d4cc", "casing": "#d2cdc3", "rail": "#8a8a8a", "tram": "#b0a7a7",
    "stop": "#ffffff", "stop_stroke": "#444444", "walk": "#333333", "text": "#222222", "halo": "#ffffff",
    "start": "#2a9d8f", "end": "#e76f51",
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$|^[a-z]{3,20}$")


def _color(c, fallback: str) -> str:
    return c if isinstance(c, str) and _COLOR.match(c) else fallback


def render_map(city: City, bbox=None, itinerary: dict | None = None, width: int = 800, style: dict | None = None) -> str:
    th = dict(DEFAULT_THEME)
    for k, v in (style or {}).items():
        th[k] = _color(v, th.get(k, "#000"))
    legs = [l for l in (itinerary or {}).get("legs", []) if l.get("path")]
    if bbox is None:
        pts = [p for l in legs for p in l["path"]]
        if not pts:
            raise ValueError("itinerary has no leg paths; pass a plan_trip itinerary or a bbox")
        a, b = min(p[0] for p in pts), min(p[1] for p in pts)
        c, d = max(p[0] for p in pts), max(p[1] for p in pts)
        m_lat, m_lon = max((c - a) * 0.08, 0.002), max((d - b) * 0.08, 0.003)
        bbox = (a - m_lat, b - m_lon, c + m_lat, d + m_lon)
    a, b, c, d = bbox
    if not (a < c and b < d):
        raise ValueError("bbox is [minLat, minLon, maxLat, maxLon] with min below max")
    W = int(width)
    cos = math.cos(math.radians((a + c) / 2))
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
    feats, _ = map_features(city, bbox, layers, max(2.0, m_per_px))
    min_area = (m_per_px * 4) ** 2
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
        polys = [f for f in fs if f.kind == "polygon" and ring_area(f.coords[0]) >= min_area
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
        mid = leg["path"][len(leg["path"]) // 2]
        label = escape(str(leg.get("line", "?")))
        bw = 10 + len(label) * 7
        svg.append(f'<g transform="translate({X(mid[1])} {Y(mid[0])})"><rect x="{-bw / 2:g}" y="-9" width="{bw}" height="18" rx="4" '
                   f'fill="{col}" stroke="{th["halo"]}" stroke-width="1.5"/><text text-anchor="middle" y="4.5" font-size="12" '
                   f'font-weight="bold" fill="#fff">{label}</text></g>')
        for s in (leg.get("from"), leg.get("to")):
            if s and s.get("at"):
                stops_drawn.append(s["at"])
                labels.append((s["at"], s.get("name", "")))
    svg += [f'<circle cx="{X(p[1])}" cy="{Y(p[0])}" r="4.5" fill="{th["stop"]}" stroke="{th["stop_stroke"]}" stroke-width="1.6"/>'
            for p in stops_drawn]
    if legs:
        first = legs[0].get("from", {}).get("at") or legs[0]["path"][0]
        last = legs[-1].get("to", {}).get("at") or legs[-1]["path"][-1]
        svg.append(f'<circle cx="{X(first[1])}" cy="{Y(first[0])}" r="7" fill="{th["start"]}" stroke="{th["halo"]}" stroke-width="2"/>')
        svg.append(f'<circle cx="{X(last[1])}" cy="{Y(last[0])}" r="7" fill="{th["end"]}" stroke="{th["halo"]}" stroke-width="2"/>')
        labels += [(first, legs[0].get("from", {}).get("name", "Start")), (last, legs[-1].get("to", {}).get("name", "End"))]
    # labels: trip endpoints first, then stops; each tries four spots around its point and takes the
    # first that neither overlaps an earlier label nor leaves the frame
    placed: list[tuple[float, float, float, float]] = []
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
