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


# A name without `near` is ranked nearer the city's centre first, among comparable matches: 1.5 points a km from the
# centre, at most CENTRE_MAX (a whole match class, exact vs. starts-with, is 50 points; "Rynek" in Kraków is a square
# in many towns of its area, and the bare name should not land 20 km out).
CENTRE_PER_KM, CENTRE_MAX = 1.5, 30
FAR_M = 10_000  # a name resolved farther than this from the centre is said so in the answer's note
CLOSE = 5  # another place this close in score (and over 1 km away) is named in the note
NEARER_GAP = 50  # with a far match, the best nearer place within this many points is named
# "Rynek Główny" is the main Rynek: a name that is the query and then "Główny" counts as the query itself. Only the
# masculine form: "Główna" is also a street ("Sarnów Główna" is a stop on it), and with it "Warszawa" in Warszawa
# matched Warszawa Główna, not Warszawa Centralna.
MAIN_OF = " glowny"
_MAIN = ("centralna", "glowny", "glowna", "glowne")  # the main station's name: "<city> Główny", or "<city>" alone


def centre(city: City) -> tuple[tuple[float, float], str | None]:
    """The point a city's names are ranked around, and the name of that place: the city's main station (a station
    place named "<city> Centralna" / "Główny", or just "<city>", by the folded city id), else the middle of the
    file's box (no name). Computed once per city."""
    c = getattr(city, "_centre", None)
    if c is None:
        names = [f"{city.id} {m}" for m in _MAIN] + [city.id]
        rows = {r[0]: r for r in city.db.execute(
            f"SELECT name_norm, name, lat, lon FROM places WHERE kind = 'station' AND name_norm IN "
            f"({','.join('?' * len(names))})", names)}
        row = next((rows[n] for n in names if n in rows), None)
        a, b, cc, d = city.bbox
        c = ((row[2], row[3]), row[1]) if row else (((a + cc) / 2, (b + d) / 2), None)
        city._centre = c
    return c


def find_places(city: City, query: str | None = None, near: list[float] | None = None, radius_m: float | None = None,
                kinds: list[str] | None = None, limit: int = 10) -> list[dict]:
    return search_places(city, query, near, radius_m, kinds, limit)[0]


def search_places(city: City, query: str | None = None, near: list[float] | None = None, radius_m: float | None = None,
                  kinds: list[str] | None = None, limit: int = 10) -> tuple[list[dict], bool]:
    """The best `limit` places, and whether more matched than were returned (the answer was cut)."""
    scored, cut = _scored(city, query, near, radius_m, kinds)
    return _hits(scored, max(1, min(limit or 10, 50)), cut)


def _scored(city: City, query: str | None, near: list[float] | None, radius_m: float | None,
            kinds: list[str] | None) -> tuple[list[tuple], bool]:
    """Every matching place as (score, kind, name, lat, lon, ref, extra, distance to `near`, distance to the
    centre), best first, and whether the query's own cap cut the rows."""
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
    own = city.id + " "  # "Katowice Rondo" is the stop "Rondo" of Katowice: the city's name before a name is no part of it
    mid = centre(city)[0] if q and not near else None
    scored = []
    for kind, name, norm, lat, lon, ref, extra in rows:
        d = fast_dist(near[0], near[1], lat, lon) if near else None
        if radius is not None and d is not None and d > radius:
            continue
        dc = fast_dist(mid[0], mid[1], lat, lon) if mid else None
        score = _BOOST.get(kind, 0) + (15 if extra and '"wikipedia"' in extra else 0)
        if q:
            core = norm[len(own):] if norm.startswith(own) else norm
            if norm == q or core == q or norm == q + MAIN_OF:
                score += 100
            elif norm.startswith(q) or core.startswith(q):
                score += 50
            elif all((" " + t) in (" " + norm) for t in toks):
                score += 45
            else:
                score += 20
            score -= min(10, len(norm) / 10)  # prefer shorter, more specific names
        if d is not None:
            score -= d / 500 if q else d / 50
        if dc is not None:
            score -= min(CENTRE_MAX, CENTRE_PER_KM * dc / 1000)
        scored.append((score, kind, name, lat, lon, ref, extra, d, dc))
    scored.sort(key=lambda x: -x[0])
    return scored, len(rows) >= 5000  # the query's own cap


def _hits(scored: list[tuple], limit: int, cut: bool) -> tuple[list[dict], bool]:
    out: list[dict] = []
    groups: dict[str, dict] = {}
    for _, kind, name, lat, lon, ref, extra, d, _dc in scored:
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


def _km(m: float) -> str:
    return f"{m / 1000:.1f} km" if m < 9950 else f"{round(m / 1000)} km"


def match_note(city: City, query: str, scored: list[tuple]) -> str | None:
    """One line when a name's match may not be the place meant: it is over FAR_M from the centre (and a nearer
    place matched too), or a place over 1 km from it scored within CLOSE. None when the match is clear."""
    best = scored[0]
    lat, lon, dc = best[3], best[4], best[8]
    if dc is None:
        return None
    others = [r for r in scored[1:] if fast_dist(lat, lon, r[3], r[4]) > 1000]
    label = centre(city)[1]
    far = dc > FAR_M
    alt = next((r for r in others if r[8] < dc / 2 and r[0] >= best[0] - NEARER_GAP), None) if far else None
    word = "nearer"
    if alt is None:
        alt, word = next((r for r in others if r[0] >= best[0] - CLOSE), None), "also"
    if not far and alt is None:
        return None
    note = f"{quoted(query)} matched {best[2]} ({best[1]}), {_km(dc)} from the centre" + (f" ({label})" if label else "")
    if alt:
        note += f"; {word}: {alt[2]} ({alt[1]}, {_km(alt[8])})"
    return note


def notes_of(*resolved: dict | None) -> list[str]:
    """The match notes of resolved places (resolve_point's third value), for an answer's note."""
    return [r["match_note"] for r in resolved if r and r.get("match_note")]


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
    """[lat, lon] or a place name → (point, label, resolved place). Raises Refused with a readable message. A
    resolved place carries `match_note` when the match may not be the place meant (match_note)."""
    if isinstance(p, (list, tuple)):
        if len(p) != 2:
            raise Refused("a point is [lat, lon] or a place name")
        lat, lon = float(p[0]), float(p[1])
        a, b, c, d = city.bbox
        if not (a <= lat <= c and b <= lon <= d):
            raise Refused(f"point [{lat}, {lon}] is outside {city.id} (bbox {city.bbox})")
        return (lat, lon), f"[{round(lat, 5)}, {round(lon, 5)}]", None
    scored, cut = _scored(city, str(p), None, None, None)
    hits = _hits(scored, 1, cut)[0]
    if not hits:
        close = closest_names(city, str(p))
        if close:
            raise Refused(f'no place named {quoted(p)} found in {city.id}; closest: {", ".join(close)}. Pass one of '
                          "these names, or [lat, lon]")
        raise Refused(f'no place named {quoted(p)} found in {city.id}; try its Polish name, find_places, or [lat, lon]. '
                      'A place in another city: plan_trip with to_city; a station elsewhere: plan_stations')
    h = hits[0]
    note = match_note(city, str(p), scored)
    if note:
        h["match_note"] = note
    return (h["at"][0], h["at"][1]), h["name"], h
