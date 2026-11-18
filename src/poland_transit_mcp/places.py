"""find_places: name (diacritic-insensitive) and proximity search over the city's own data."""
from __future__ import annotations

import json
import math
import re

from .errors import Refused
from .city import City
from .geo import fast_dist, pt
from .text import quoted, tokens

KINDS = ("attraction", "museum", "park", "station", "stop", "hotel", "landmark", "street")
_BOOST = {"station": 8, "attraction": 6, "museum": 6, "landmark": 5, "park": 4, "stop": 3, "street": 2, "hotel": 0}


def find_places(city: City, query: str | None = None, near: list[float] | None = None, radius_m: float | None = None,
                kinds: list[str] | None = None, limit: int = 10) -> list[dict]:
    return search_places(city, query, near, radius_m, kinds, limit)[0]


def search_places(city: City, query: str | None = None, near: list[float] | None = None, radius_m: float | None = None,
                  kinds: list[str] | None = None, limit: int = 10) -> tuple[list[dict], bool]:
    """The best `limit` places, and whether more matched than were returned (the answer was cut)."""
    limit = max(1, min(limit or 10, 50))
    toks = tokens(query) if query else []
    where, params = [], []
    for t in toks:
        where.append("name_norm LIKE ?")
        params.append(f"%{t}%")
    if kinds:
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    radius = radius_m if radius_m is not None else (1000 if near and not toks else None)
    if near and radius is not None:
        radius = min(radius, 20000)
        d_lat = radius / 111320
        d_lon = radius / (111320 * math.cos(math.radians(near[0])))
        where.append("id IN (SELECT id FROM places_rtree WHERE min_lat >= ? AND max_lat <= ? AND min_lon >= ? AND max_lon <= ?)")
        params += [near[0] - d_lat, near[0] + d_lat, near[1] - d_lon, near[1] + d_lon]
    if not where:
        return [], False
    rows = city.db.execute(f"SELECT kind, name, name_norm, lat, lon, ref, extra FROM places WHERE {' AND '.join(where)} LIMIT 5000",
                           params).fetchall()
    q = " ".join(toks)
    scored = []
    for kind, name, norm, lat, lon, ref, extra in rows:
        d = fast_dist(near[0], near[1], lat, lon) if near else None
        if radius is not None and d is not None and d > radius:
            continue
        score = _BOOST.get(kind, 0) + (15 if extra and '"wikipedia"' in extra else 0)
        if q:
            if norm == q:
                score += 100
            elif norm.startswith(q):
                score += 50
            elif all((" " + t) in (" " + norm) for t in toks):
                score += 45
            else:
                score += 20
            score -= min(10, len(norm) / 10)  # prefer shorter, more specific names
        if d is not None:
            score -= d / 500 if q else d / 50
        scored.append((score, kind, name, lat, lon, ref, extra, d))
    scored.sort(key=lambda x: -x[0])
    out: list[dict] = []
    cut = len(rows) >= 5000  # the query's own cap
    groups: dict[str, dict] = {}
    for _, kind, name, lat, lon, ref, extra, d in scored:
        ex = json.loads(extra) if extra else {}
        if kind == "stop":
            g = groups.get(name)
            if g and fast_dist(g["at"][0], g["at"][1], lat, lon) < 400:  # poles of one stop → one hit
                g["stop_ids"].append(ex.get("stop_id"))
                continue
            if len(out) >= limit:
                cut = True
                continue
            hit = {"name": name, "kind": "stop", "at": pt(lat, lon), "stop_ids": [ex.get("stop_id")]}
            groups[name] = hit
        else:
            if len(out) >= limit:
                cut = True
                continue
            hit = {"name": name, "kind": kind, "at": pt(lat, lon)}
            if ref and re.fullmatch(r"[nwr]\d+", ref):
                hit["osm"] = ref
            if ex.get("wikipedia"):
                hit["wikipedia"] = ex["wikipedia"]
        if d is not None:
            hit["dist_m"] = round(d)
        out.append(hit)
    return out, cut


def closest_names(city: City, query: str, limit: int = 4) -> list[str]:
    """Places that match one word of a name that matched nothing as a whole (an English or declined name:
    "Kosciuszko Mound" finds "Kopiec Kościuszki"), as `name (kind)`: an error that names them saves the caller a
    search. The three longest words, longest first (a name of 200 characters must not become dozens of searches); a
    long word is matched without its last letter, because Polish names decline (Kościuszko, Kościuszki)."""
    found: dict[str, None] = {}
    for word in sorted({t for t in tokens(query) if len(t) >= 4}, key=lambda t: (-len(t), t))[:3]:
        for h in find_places(city, query=word[:-1] if len(word) >= 7 else word, limit=limit):
            found.setdefault(f'{h["name"]} ({h["kind"]})')
    return list(found)[:limit]


def resolve_point(city: City, p) -> tuple[tuple[float, float], str, dict | None]:
    """[lat, lon] or a place name → (point, label, resolved place). Raises ValueError with a readable message."""
    if isinstance(p, (list, tuple)):
        if len(p) != 2:
            raise Refused("a point is [lat, lon] or a place name")
        lat, lon = float(p[0]), float(p[1])
        a, b, c, d = city.bbox
        if not (a <= lat <= c and b <= lon <= d):
            raise Refused(f"point [{lat}, {lon}] is outside {city.id} (bbox {city.bbox})")
        return (lat, lon), f"[{round(lat, 5)}, {round(lon, 5)}]", None
    hits = find_places(city, query=str(p), limit=1)
    if not hits:
        close = closest_names(city, str(p))
        if close:
            raise Refused(f'no place named {quoted(p)} found in {city.id}; closest: {", ".join(close)}. Pass one of '
                          "these names, or [lat, lon]")
        raise Refused(f'no place named {quoted(p)} found in {city.id}; try its Polish name, find_places, or [lat, lon]. '
                      'A place in another city: plan_trip with to_city; a station elsewhere: plan_stations')
    h = hits[0]
    return (h["at"][0], h["at"][1]), h["name"], h
