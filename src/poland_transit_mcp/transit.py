"""stops_near, walk_route, departures, line_info."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import Refused
from .city import City
from .geo import LatLon, haversine, pt, simplify
from .places import resolve_point
from .text import fold, quoted
from .timetable import NO_PICKUP, Stop, headway_fields, route_color
from .timeutil import LocalTime, fmt_date, fmt_wall, now_local
from .walk import FLAG_BRIDGE, FLAG_STEPS, WALK_SPEED_MPS


@dataclass
class WalkSearch:
    """Result of one bounded walking search from a point: walking seconds to each reachable stop,
    plus what is needed to draw the path to any of them."""
    at: LatLon
    snap_node: int
    snap_dist: float
    stops: dict[int, int]  # stop idx → seconds
    pred: np.ndarray
    dist: np.ndarray | None = None  # metres along the graph from the snapped node (inf where not reached)

    def secs_to_node(self, node: int, extra_m: float = 0.0) -> int | None:
        """Walking seconds to a graph node (plus extra_m from the node to a point), if reached."""
        d = float(self.dist[node]) if self.dist is not None else float("inf")
        return int(round((d + self.snap_dist + extra_m) / WALK_SPEED_MPS)) if d < float("inf") else None

    def path_to(self, city: City, stop: Stop) -> list[LatLon]:
        g = city.graph
        return [self.at, *g.coords_of(g.path_from(self.pred, stop.node)), (stop.lat, stop.lon)]


def walk_to_stops(city: City, at: LatLon, max_secs: float) -> WalkSearch:
    g = city.graph
    snap = g.snap(at[0], at[1], 400)
    if snap is None:
        raise Refused(f"point {pt(*at)} is more than 400 m from any mapped walkable way")
    max_m = max_secs * WALK_SPEED_MPS
    dist, pred = g.search(snap.node, max(0.0, max_m - snap.dist))
    nodes, stops = city.stop_nodes()
    d = dist[nodes] + snap.dist + np.array([s.snap for s in stops])
    out = {stops[i].idx: int(round(d[i] / WALK_SPEED_MPS)) for i in np.nonzero(d <= max_m)[0]}
    return WalkSearch(at, snap.node, snap.dist, out, pred, dist)


def stops_near(city: City, point, max_walk_min: float = 10, date: int | None = None) -> dict:
    at, label, resolved = resolve_point(city, point)
    if not city.has_transit:
        return {"point": pt(*at), "stops": [], "note": city.transit_note()}
    d = date or now_local().date
    w = walk_to_stops(city, at, min(max_walk_min, 30) * 60)
    out = []
    for idx, secs in sorted(w.stops.items(), key=lambda x: x[1])[:20]:
        s = city.tt.stops[idx]
        out.append({"stop_id": s.id, "name": s.name, "platform": s.platform, "at": pt(s.lat, s.lon),
                    "walk_min": round(secs / 60, 1), "lines": [r.short for r in city.tt.lines_at(idx, d)]})
    return {"point": pt(*at), "resolved_from": resolved["name"] if resolved else None, "date": fmt_date(d),
            "max_walk_min": max_walk_min, "stops": out,
            "note": (city.transit_note(d) if out else f"no stops within {max_walk_min} min walk")}


@dataclass
class WalkPath:
    meters: float
    secs: float
    coords: list[LatLon]  # start point, graph nodes..., end point
    flags: list[int]  # one per edge between consecutive graph nodes
    snap: tuple[float, float]


def walk_path(city: City, a: LatLon, b: LatLon, max_m: float = 15000) -> WalkPath | None:
    g = city.graph
    sa, sb = g.snap(a[0], a[1], 400), g.snap(b[0], b[1], 400)
    if sa is None or sb is None:
        raise Refused(f'{"start" if sa is None else "end"} point is more than 400 m from any mapped walkable way')
    r = g.route(sa.node, sb.node, max_m)
    if r is None:
        return None
    cost, path = r
    length, flags = g.describe(path)
    return WalkPath(length + sa.dist + sb.dist, (cost + sa.dist + sb.dist) / WALK_SPEED_MPS,
                    [a, *g.coords_of(path), b], flags, (sa.dist, sb.dist))


def walk_route(city: City, frm, to) -> dict:
    A, a_label, _ = resolve_point(city, frm)
    B, b_label, _ = resolve_point(city, to)
    r = walk_path(city, A, B)
    if r is None:
        return {"from": {"name": a_label, "at": pt(*A)}, "to": {"name": b_label, "at": pt(*B)}, "path": None,
                "note": "no walking connection found within 15 km"}
    bridges = sum(1 for i, f in enumerate(r.flags) if f & FLAG_BRIDGE and not (i > 0 and r.flags[i - 1] & FLAG_BRIDGE))
    notes = []
    if r.snap[0] > 60:
        notes.append(f"start is {round(r.snap[0])} m from the nearest mapped way")
    if r.snap[1] > 60:
        notes.append(f"end is {round(r.snap[1])} m from the nearest mapped way")
    return {"from": {"name": a_label, "at": pt(*A)}, "to": {"name": b_label, "at": pt(*B)},
            "distance_m": round(r.meters), "minutes": round(r.secs / 60),
            "straight_line_m": round(haversine(A[0], A[1], B[0], B[1])),
            "bridges_crossed": bridges, "uses_steps": any(f & FLAG_STEPS for f in r.flags),
            # the drawn length changes by under 1% at length/500 (4 m up to a 2 km walk)
            "path": [pt(*c) for c in simplify(r.coords, max(4.0, r.meters / 500))], "note": "; ".join(notes) or None}


def resolve_stops(city: City, ref: str) -> list[Stop]:
    """A stop_id (with or without the feed prefix), or a stop name: all poles with that name."""
    tt = city.tt
    s = tt.stop_by_id.get(ref) or next((tt.stop_by_id[f'{f["key"]}:{ref}'] for f in city.feeds
                                         if f'{f["key"]}:{ref}' in tt.stop_by_id), None)
    if s:
        return [s]
    n = fold(ref)
    found = [s for s in tt.stops if s and fold(s.name) == n]  # exact name first ("Dworzec st. 17")
    if not found and len(n) > 3 and n[-3] == " " and n[-2:].isdigit():
        n = n[:-3]  # "Oliwa PKP 02": the feed's platform number is split off the stored name
    found = found or [s for s in tt.stops if s and fold(s.name) == n] or [s for s in tt.stops if s and fold(s.name).startswith(n)]
    names = sorted({s.name for s in found})
    if len(names) > 1:
        raise Refused(f'{quoted(ref)} matches several stops: {", ".join(names[:8])}; pass one of these names')
    return found


def scheduled_departures(city: City, stops: list[Stop], when: LocalTime, line: str | None = None, limit: int = 10) -> list[dict]:
    tt = city.tt
    day = tt.day(when.date)
    want = fold(line) if line else None
    today = fmt_date(when.date)
    out = []
    for s in stops:
        for p, pos in tt.stop_patterns.get(s.idx, []):
            if pos == len(p.stops) - 1 or p.flags[pos] & NO_PICKUP:
                continue
            if want and fold(p.route.short) != want:
                continue
            direction = p.headsign or tt.stops[p.stops[-1]].name
            for t in day.by_pattern.get(p.idx, []):
                dep = t.dep[pos]
                if dep < when.secs:
                    continue
                wdate, wtime = fmt_wall(when.date, dep)
                out.append({"line": p.route.short, "mode": p.route.mode, "operator": p.route.agency, "direction": direction, "time": wtime,
                            "date": wdate if wdate != today else None, "stop_id": s.id, "platform": s.platform,
                            "status": "scheduled", "trip_id": t.trip_id, "color": route_color(p.route), "_secs": dep,
                            "_trip": t.trip_idx, "_pos": pos, **headway_fields(t.headway, "status")})
    out.sort(key=lambda d: d["_secs"])
    out = out[:limit]
    if tt.has_trip_details:  # trains: name and number, category, platform and track at this stop
        for d in out:
            info = tt.trip_details(d["_trip"])
            if info:
                where = info["where"][d["_pos"]] if d["_pos"] < len(info["where"]) else ("", "")
                d.update(train=info["train"], category=info["category"], platform=where[0] or None, track=where[1] or None)
    return out


def train_info(city: City, train: str, date: int) -> dict | None:
    """A train by its number or name (national layer): its stops with times, platforms and tracks on a date."""
    tt = city.tt
    if not tt.has_trip_details:
        return None
    want = fold(train)
    rows = tt.db.execute("SELECT trip_idx, train FROM trip_info WHERE train IS NOT NULL").fetchall()
    ids = {i for i, name in rows if fold(name) == want or fold(name).split(" ")[0] == want
           or (len(want) > 3 and want in fold(name))}
    if not ids:
        return None
    day = tt.day(date)
    runs = [t for t in day.trips if t.trip_idx in ids and t.service_date == date]
    if not runs:
        return {"train": train, "date": fmt_date(date), "runs": [], "note": f"no run of {train} on {fmt_date(date)}"}
    out = []
    for t in runs[:5]:
        info = tt.trip_details(t.trip_idx) or {}
        where = info.get("where") or []
        p = t.pattern
        stops = []
        for pos, s in enumerate(p.stops):
            st = tt.stops[s]
            wd, wt = fmt_wall(date, t.arr[pos])
            dd, dt = fmt_wall(date, t.dep[pos])
            w = where[pos] if pos < len(where) else ("", "")
            stops.append({"name": st.name, "arrive": None if pos == 0 else (wt if wd == fmt_date(date) else f"{wd} {wt}"),
                          "depart": None if pos == len(p.stops) - 1 else (dt if dd == fmt_date(date) else f"{dd} {dt}"),
                          "platform": w[0] or None, "track": w[1] or None})
        out.append({"train": info.get("train"), "category": info.get("category"), "line": p.route.short,
                    "operator": p.route.agency, "direction": p.headsign or tt.stops[p.stops[-1]].name,
                    "trip_id": t.trip_id, "stops": stops,
                    "path": [pt(*c) for c in simplify(tt.leg_path(p, 0, len(p.stops) - 1), 60)]})
    return {"train": train, "date": fmt_date(date), "runs": out,
            "note": f"{len(runs)} runs match; the first 5 are shown" if len(runs) > 5 else None}


BANDS = ((4, 7), (7, 9), (9, 14), (14, 18), (18, 22), (22, 28))


def line_info(city: City, line: str, date: int) -> dict:
    tt = city.tt
    want = fold(line)
    routes = [r for r in tt.routes.values() if fold(r.short) == want]
    if not routes:
        similar = [r.short for r in tt.routes.values() if fold(r.short).startswith(want[:1])][:15]
        raise Refused(f'no line {quoted(line)} in {city.id} timetable' + (f"; similar: {', '.join(similar)}" if similar else ""))
    ids = {r.idx for r in routes}
    day = tt.day(date)
    groups: dict = {}
    for t in day.trips:
        if t.service_date == date and t.pattern.route.idx in ids:
            key = f"d{t.pattern.dir}" if t.pattern.dir is not None and t.pattern.dir >= 0 else t.pattern.headsign
            groups.setdefault(key, []).append(t)

    def clock(s: int) -> str:
        wd, wt = fmt_wall(date, s)
        return wt if wd == fmt_date(date) else f"{wt} (+1 day)"

    directions = []
    for ts in groups.values():
        by_pat: dict[int, int] = {}
        by_head: dict[str, int] = {}
        for t in ts:
            by_pat[t.pattern.idx] = by_pat.get(t.pattern.idx, 0) + 1
            by_head[t.pattern.headsign] = by_head.get(t.pattern.headsign, 0) + 1
        main = tt.patterns[max(by_pat.items(), key=lambda x: x[1])[0]]
        head = max(by_head.items(), key=lambda x: x[1])[0] or tt.stops[main.stops[-1]].name
        deps = sorted(t.dep[0] - t.shift for t in ts)
        freq = []
        for a, b in BANDS:
            n = sum(1 for d in deps if a * 3600 <= d < b * 3600)
            if n:
                freq.append({"hours": f"{a % 24:02d}-{b % 24:02d}", "departures": n, "every_min": round((b - a) * 60 / n)})
        directions.append({
            "direction": head, "trips": len(ts), "variants": len(by_pat),
            "first_departure": clock(deps[0]), "last_departure": clock(deps[-1]), "frequency": freq,
            "stops": [{"stop_id": tt.stops[s].id, "name": tt.stops[s].name} for s in main.stops],
            # a national line runs hundreds of km: its overview is drawn coarser (TLK was 112,000 characters at 15 m)
            "path": [pt(*c) for c in simplify(tt.leg_path(main, 0, len(main.stops) - 1), 150 if city.national else 15)],
        })
    r = routes[0]
    operators = sorted({x.agency for x in routes if x.agency})
    return {"line": r.short, "mode": r.mode, "operator": ", ".join(operators) or None, "color": route_color(r), "name": r.long, "date": fmt_date(date),
            "directions": directions,
            "note": None if directions else (city.transit_note(date) or f"line {r.short} has no trips on {fmt_date(date)}")}
