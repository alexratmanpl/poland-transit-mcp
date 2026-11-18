"""plan_trip between two cities: city A → trains and coaches (the national layer) → city B.

The three timetables stay separate files. They are joined at gateway stations: the national
layer's stations inside a city's area, reached on that city's walking graph (docs/INTERCITY.md).

The searches (RAPTOR, in the Rust engine when it is installed), for depart_at:
1. City A, with no target: the earliest time at every stop, and so at every station of A, either
   from a stop or on foot from the start. STATION_SECS are added to reach the platform.
2. National, with no target, seeded with all of A's stations: the earliest arrival at every
   station of B.
3. City B, seeded from all of B's stations: the best way on to the destination, and so the station
   of B to aim for.
The chosen option is then rebuilt along one path:
- the national journey to that station, which also says which station of A it leaves from;
- city B from that train's real arrival;
- city A by a search backwards from that train's departure, so the plan does not leave earlier
  than needed.
arrive_by runs the same steps mirrored, from B. Each further option repeats steps 2 and 3 with
every train or coach boarding after the previous option's.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import Refused
from . import telemetry
from .city import City
from .geo import haversine, pt, simplify
from .places import resolve_point
from .plan import ACCESS_SECS, WALK_FIRST_MAX_M, WALK_FIRST_SLACK_S, Itin, TLeg, _length, _to_itin
from .raptor import INF, RJourney, raptor, raptor_all
from .timetable import NO_DROPOFF, NO_PICKUP, Stop, headway_fields, route_color
from .timeutil import add_days, fmt_date, fmt_wall, parse_when
from .transit import WalkSearch, walk_path, walk_to_stops
from .walk import WALK_SPEED_MPS

DAY = 86400
STATION_SECS = 5 * 60  # at a station, between city transport and a train or coach (estimated)
NATIONAL_MIN_CHANGE = 5 * 60  # between two trains or coaches (estimated: no minimum connection times are published)
NATIONAL_ROUNDS = 3  # at most 2 changes between trains and coaches
GATEWAY_SNAP_M = 400  # a station must be this close to the city's walkable ways
GATEWAY_WALK_SECS = 10 * 60  # city stops within this walk of a station connect to it
MAX_OPTIONS = 3
ASSUMPTIONS = [
    f"{STATION_SECS // 60} min are allowed at each station to change between city transport and a train or coach, "
    f"and {NATIONAL_MIN_CHANGE // 60} min between two trains or coaches (estimated; no minimum connection times are published)",
    "Walks inside stations are not measured; changes between platforms of one station count as 0 m",
]


@dataclass
class Gateway:
    """A station of the national layer inside a city: every platform and track of it, where it meets the
    city's walking graph, and the city stops within a short walk."""
    name: str
    nat: list[int]  # national stop idx
    lat: float
    lon: float
    node: int
    snap_m: float
    stops: dict[int, int]  # city stop idx → walking seconds from the station


def gateways(city: City, nat: City) -> list[Gateway]:
    """The national layer's stations inside the city, computed once per city and kept (a few kB)."""
    if city.id in nat.gateways:
        return nat.gateways[city.id]
    with telemetry.span("intercity.gateways", **{"poland_transit.city": city.id}) as span:
        a, b, c, d = city.bbox
        groups: dict[tuple, list[Stop]] = {}
        for s in nat.tt.stops:
            if s is not None and a <= s.lat <= c and b <= s.lon <= d:
                groups.setdefault((s.name, round(s.lat, 2), round(s.lon, 2)), []).append(s)
        g = city.graph
        out = []
        for (name, _, _), ss in sorted(groups.items()):
            lat, lon = sum(x.lat for x in ss) / len(ss), sum(x.lon for x in ss) / len(ss)
            snap = g.snap(lat, lon, GATEWAY_SNAP_M)
            if snap is None:
                continue
            stops = walk_to_stops(city, (lat, lon), GATEWAY_WALK_SECS).stops if city.has_transit else {}
            out.append(Gateway(name, [x.idx for x in ss], lat, lon, snap.node, snap.dist, stops))
        span.set_attribute("poland_transit.intercity.stations", len(out))
    nat.gateways[city.id] = out
    return out


@dataclass
class Side:
    city: City
    at: tuple[float, float]
    label: str
    walk: WalkSearch  # walking from the point
    gws: list[Gateway]


STATION_WALK_SECS = 30 * 60  # a station this close on foot is also offered as a walk (stops: ACCESS_SECS)


def _walk(city: City, at) -> WalkSearch:
    """Walking from a point: stops within ACCESS_SECS (or twice that if none), stations up to STATION_WALK_SECS."""
    w = walk_to_stops(city, at, STATION_WALK_SECS)
    near = {s: t for s, t in w.stops.items() if t <= ACCESS_SECS}
    if not near:
        near = {s: t for s, t in w.stops.items() if t <= 2 * ACCESS_SECS}
    w.stops = near
    return w


def _day_for(date: int, real: int, back: bool) -> tuple[int, int]:
    """The service day whose timetable window covers a search starting at `real` (seconds on the
    request date's clock), and how many days it is from the request date. A day's window runs from
    its own midnight to about 06:00 the next morning."""
    k = max(0, (real - 2 * 3600) // DAY) if not back else min(0, (real - 4 * 3600) // DAY)
    return add_days(date, k), k


@dataclass
class Segment:
    """One city part of an option: a journey found in that city's timetable, or a walk."""
    side: Side
    itin: Itin | None  # None: walk between the point and the station
    walk_secs: int  # for a walk
    days: int  # the city's timetable day, in days from the request date
    station: Gateway


def plan_intercity(a: City, b: City, nat: City, frm, to, depart_at: str | None = None,
                   arrive_by: str | None = None, max_transfers: int = 2) -> dict:
    back = bool(arrive_by)
    when = parse_when(arrive_by or depart_at)
    for c in (a, b):
        if not c.has_transit:
            raise Refused(f"{c.id} has no timetable data, so it cannot be joined to trains and coaches")
    A, a_label, _ = resolve_point(a, frm)
    B, b_label, _ = resolve_point(b, to)
    date = when.date
    rounds = max(0, min(max_transfers if max_transfers is not None else 2, 2)) + 1
    src = Side(a, A, a_label, _walk(a, A), gateways(a, nat))
    dst = Side(b, B, b_label, _walk(b, B), gateways(b, nat))
    for s in (src, dst):
        if not s.gws:
            raise Refused(f"no train or coach station of the national layer is inside {s.city.id}")
    first, last = (dst, src) if back else (src, dst)  # the search runs from `first` to `last`
    sign = -1 if back else 1
    clock = sign * when.secs

    # 1. first city, no target: the earliest time (search clock) at each of its stations, ready to board
    tt1 = first.city.tt
    best1 = raptor_all(tt1.raptor(tt1.day(date), back), tt1.transfers, first.walk.stops, clock, rounds)
    ready: dict[int, int] = {}
    for i, g in enumerate(first.gws):
        t = INF
        direct = first.walk.secs_to_node(g.node, g.snap_m)
        if direct is not None:
            t = clock + direct
        for s, w in g.stops.items():
            if best1[s] < INF:
                t = min(t, int(best1[s]) + w)
        if t < INF:
            ready[i] = t + STATION_SECS
    if not ready:
        return _empty(a, b, A, B, a_label, b_label, back, date, when.secs, f"no station of {first.city.id} is reachable")
    station_of = {n: i for i, g in enumerate(first.gws) for n in g.nat}
    last_nat = {n for g in last.gws for n in g.nat}

    ntt = nat.tt
    nday = ntt.day(date)
    ndata = ntt.raptor(nday, back)
    options: list[dict] = []
    seen: set[int] = set()
    cut = -INF
    later: dict[int, int] = {}  # station → the next option must board after the trains found so far pass it
    set_aside: set[int] = set()  # stations of the last city reached only on foot from another station
    for _ in range(MAX_OPTIONS + 4):
        if len(options) >= MAX_OPTIONS:
            break
        # 2. national, no target, seeded with every station of the first city
        seeds: dict[int, int] = {}
        for i, t in ready.items():
            for n in first.gws[i].nat:
                if n not in last_nat:  # a station in both cities' areas would make a trip with no train
                    seeds[n] = min(seeds.get(n, INF), max(t, cut, later.get(i, -INF)))
        if not seeds:
            break
        dep0 = min(seeds.values())
        access = {n: t - dep0 for n, t in seeds.items()}
        bestn = raptor_all(ndata, ntt.transfers, access, dep0, NATIONAL_ROUNDS, NATIONAL_MIN_CHANGE)
        reach = {j: min(int(bestn[n]) for n in g.nat) for j, g in enumerate(last.gws)}
        reach = {j: t for j, t in reach.items() if t < INF and j not in set_aside}
        if not reach:
            break
        # 3. last city, seeded from every station reached: which station leads on best to the point
        j = _best_station(last, reach, back, date, rounds)
        if j is None:
            break
        # rebuild: the national journey to that station, then both city parts from its real times
        js = raptor(ndata, ntt.transfers, access, {n: 0 for n in last.gws[j].nat}, dep0, NATIONAL_ROUNDS, NATIONAL_MIN_CHANGE)
        if not js:  # reached only by a walk from another station: that station will be found on its own
            set_aside.add(j)
            continue
        jn = min(js, key=lambda x: (x.arrival, x.transfers))
        national = _to_itin(jn, back)
        legs_n = [p for p in national.parts if isinstance(p, TLeg)]
        board, alight = legs_n[0], legs_n[-1]
        dep_real, arr_real = board.trip.dep[board.board], alight.trip.arr[alight.alight]
        n_start, n_end = (arr_real, dep_real) if back else (dep_real, arr_real)  # search order
        # the next option boards after this one, and after this train has passed every station of the
        # first city it serves (else the same train would come back, boarded one station further on)
        cut = sign * n_start + 60
        t_first = alight if back else board
        for pos, stop in enumerate(t_first.trip.pattern.stops):
            i = station_of.get(stop)
            if i is not None:
                passes = sign * (t_first.trip.arr[pos] if back else t_first.trip.dep[pos]) + 60
                later[i] = max(later.get(i, -INF), passes)
        if t_first.trip.trip_idx in seen:
            continue
        seen.add(t_first.trip.trip_idx)
        seg_last = _segment(last, last.gws[j], n_end + sign * STATION_SECS, back, date, rounds)
        # The same train may stop at several stations of the first city: use the one that lets the
        # traveller leave latest (arrive_by: arrive earliest), not just the one the search reached first.
        cands = []
        for pos, stop in enumerate(t_first.trip.pattern.stops):
            i = station_of.get(stop)
            if i is None:
                continue
            flags = t_first.trip.pattern.flags[pos]
            if (back and (pos <= t_first.board or flags & NO_DROPOFF)) or (not back and (pos >= t_first.alight or flags & NO_PICKUP)):
                continue
            at = t_first.trip.arr[pos] if back else t_first.trip.dep[pos]
            if ready.get(i, INF) > sign * at:
                continue  # the traveller cannot be at this station in time for the train
            cands.append((i, pos, at - sign * STATION_SECS))
        pick = _best_boarding(first, cands, not back, date, rounds)
        seg_first = _segment(first, first.gws[pick[0]], pick[2], not back, date, rounds) if pick else None
        if seg_last is None or seg_first is None:
            continue
        pos = pick[1]
        moved = TLeg(t_first.trip, t_first.board, pos) if back else TLeg(t_first.trip, pos, t_first.alight)
        national.parts = [moved if x is t_first else x for x in national.parts]
        seg_a, seg_b = (seg_last, seg_first) if back else (seg_first, seg_last)
        opt = _summary(seg_a, national, seg_b)  # the legs are only built for the options kept
        if any(_dominates(o, opt) for o in options):
            continue  # e.g. the same train again, reached by an extra change from another station
        options = [o for o in options if not _dominates(opt, o)] + [opt]
    options.sort(key=(lambda o: (-o["_start"], o["transfers"])) if back else (lambda o: (o["_end"], o["transfers"])))
    options = [_option(*o["_parts"], nat, date) for o in options]
    head = {"from": {"name": a_label, "at": pt(*A), "city": a.id}, "to": {"name": b_label, "at": pt(*B), "city": b.id},
            ("arrive_by" if back else "depart_at"): " ".join(fmt_wall(date, when.secs))}
    note = None if options else (f"no connection found from {a.id} to {b.id} with trains or coaches within the timetable "
                                 f"window of {fmt_date(date)} (this day and the night after)")
    notes = [n for c in (a, b) for n in c.intercity_notes]
    notes += [n for c in (a, nat, b) for n in (c.partial_note(date), c.transit_note(date)) if n]
    return {**head, "itineraries": options, "assumptions": ASSUMPTIONS, "note": "; ".join(filter(None, [note, *notes])) or None}


def _best_boarding(side: Side, cands: list[tuple[int, int, int]], back: bool, date: int,
                   rounds: int) -> tuple[int, int, int] | None:
    """Of the stations where one train can be boarded (or left), the one with the best city part: one
    search seeded from all of them at once. cands: (station, position on the train, time at the station
    on the request date's clock); back: the city part is searched backwards (depart_at: towards the start
    point, to leave as late as possible)."""
    if len(cands) <= 1:
        return cands[0] if cands else None
    sign = -1 if back else 1
    day, k = _day_for(date, cands[0][2], back)
    seeds: dict[int, tuple[int, int]] = {}  # city stop → (search clock, candidate)
    walk_best: tuple[int, int] | None = None
    for c, (i, _, at) in enumerate(cands):
        t = sign * (at - k * DAY)
        g = side.gws[i]
        for s, w in g.stops.items():
            if t + w < seeds.get(s, (INF, -1))[0]:
                seeds[s] = (t + w, c)
        direct = side.walk.secs_to_node(g.node, g.snap_m)
        if direct is not None and (walk_best is None or t + direct < walk_best[0]):
            walk_best = (t + direct, c)
    best: tuple[int, int, int] | None = None  # (arrival, transfers, candidate)
    if seeds:
        tt = side.city.tt
        t0 = min(v[0] for v in seeds.values())
        for j in raptor(tt.raptor(tt.day(day), back), tt.transfers, {s: v[0] - t0 for s, v in seeds.items()},
                        side.walk.stops, t0, rounds):
            cand = (j.arrival, j.transfers, seeds[j.access_stop][1])
            if best is None or cand[:2] < best[:2]:
                best = cand
    if walk_best and (best is None or walk_best[0] <= best[0] + WALK_FIRST_SLACK_S):
        return cands[walk_best[1]]
    return cands[best[2]] if best else None


def _summary(seg_a: Segment, national: Itin, seg_b: Segment) -> dict:
    """An option's start, end and number of changes, enough to compare it with others."""
    t_legs = [p for p in national.parts if isinstance(p, TLeg)]
    n_dep, n_arr = t_legs[0].trip.dep[t_legs[0].board], t_legs[-1].trip.arr[t_legs[-1].alight]
    start = seg_a.itin.start + seg_a.days * DAY if seg_a.itin else n_dep - STATION_SECS - seg_a.walk_secs
    end = seg_b.itin.end + seg_b.days * DAY if seg_b.itin else n_arr + STATION_SECS + seg_b.walk_secs
    vehicles = len(t_legs) + sum(isinstance(p, TLeg) for seg in (seg_a, seg_b) if seg.itin for p in seg.itin.parts)
    return {"_start": start, "_end": end, "transfers": max(0, vehicles - 1), "_parts": (seg_a, national, seg_b)}


def _dominates(y: dict, x: dict) -> bool:
    """y leaves no earlier, arrives no later and changes no more often than x, and is better in one."""
    return (y["_start"] >= x["_start"] and y["_end"] <= x["_end"] and y["transfers"] <= x["transfers"]
            and (y["_start"] > x["_start"] or y["_end"] < x["_end"] or y["transfers"] < x["transfers"]))


def _best_station(last: Side, reach: dict[int, int], back: bool, date: int, rounds: int) -> int | None:
    """Step 3: one search in the last city seeded from all reached stations; the station its best
    journey (or walk) starts from."""
    sign = -1 if back else 1
    t0 = min(reach.values())
    day, k = _day_for(date, sign * t0, back)
    shift = sign * k * DAY  # request-date search clock → this day's search clock
    seeds: dict[int, tuple[int, int]] = {}
    walk_best: tuple[int, int] | None = None
    for j, t in reach.items():
        t = t + STATION_SECS - shift
        for s, w in last.gws[j].stops.items():
            if t + w < seeds.get(s, (INF, -1))[0]:
                seeds[s] = (t + w, j)
        direct = last.walk.secs_to_node(last.gws[j].node, last.gws[j].snap_m)
        if direct is not None and (walk_best is None or t + direct < walk_best[0]):
            walk_best = (t + direct, j)
    best: tuple[int, int, int] | None = None  # (arrival, transfers, station)
    if seeds:
        tt = last.city.tt
        dep0 = min(v[0] for v in seeds.values())
        js = raptor(tt.raptor(tt.day(day), back), tt.transfers, {s: v[0] - dep0 for s, v in seeds.items()},
                    last.walk.stops, dep0, rounds)
        for jr in js:
            first_stop = jr.access_stop
            cand = (jr.arrival, jr.transfers, seeds[first_stop][1])
            if best is None or cand[:2] < best[:2]:
                best = cand
    if walk_best and (best is None or walk_best[0] <= best[0] + WALK_FIRST_SLACK_S):
        return walk_best[1]
    return best[2] if best else None


def _segment(side: Side, g: Gateway, at_station: int, back: bool, date: int, rounds: int) -> Segment | None:
    """A city part between the station and the point. `at_station` is a real time (request-date clock);
    back=False searches from the station onwards (arriving), back=True searches backwards to the latest
    departure from the point that still makes the station by then."""
    sign = -1 if back else 1
    day, k = _day_for(date, at_station, back)
    t = sign * (at_station - k * DAY)
    walk = side.walk.secs_to_node(g.node, g.snap_m)
    tt = side.city.tt
    js: list[RJourney] = []
    if g.stops:
        js = raptor(tt.raptor(tt.day(day), back), tt.transfers, g.stops, side.walk.stops, t, rounds)
    best = min(js, key=lambda x: (x.arrival, x.transfers)) if js else None
    if walk is not None:
        walk_m = walk * WALK_SPEED_MPS
        if best is None or t + walk <= best.arrival or (walk_m <= WALK_FIRST_MAX_M and t + walk <= best.arrival + WALK_FIRST_SLACK_S):
            return Segment(side, None, walk, k, g)
    if best is None:
        return None
    return Segment(side, _to_itin(best, back), 0, k, g)


def _fmt(date: int, secs: int) -> str:
    wd, wt = fmt_wall(date, secs)
    return wt if wd == fmt_date(date) else f"{wd} {wt}"


def _stop_ref(stop: Stop, city: str, where: tuple[str, str] | None = None) -> dict:
    out = {"name": stop.name, "stop_id": stop.id, "platform": stop.platform, "at": pt(stop.lat, stop.lon), "city": city}
    if where:
        out["platform"] = where[0] or None
        out["track"] = where[1] or None
    return out


def _station_ref(g: Gateway, nat: City) -> dict:
    return {"name": g.name, "at": pt(g.lat, g.lon), "city": nat.id}


def _walk_leg(city: City, frm: dict, to: dict, start: int, secs: int, date: int, estimated: bool = False) -> dict:
    a, b = tuple(frm["at"]), tuple(to["at"])
    w = None if estimated else walk_path(city, a, b, 4000)
    coords = w.coords if w else [a, b]
    meters = w.meters if w else haversine(a[0], a[1], b[0], b[1]) * 1.3
    leg = {"mode": "walk", "from": frm, "to": to, "depart": _fmt(date, start), "arrive": _fmt(date, start + secs),
           "distance_m": round(meters), "minutes": max(0, round(secs / 60)), "path": [pt(*c) for c in simplify(coords, 8)]}
    if not w:
        leg["distance"] = "estimated (straight line × 1.3)"
    return leg


def _transit_leg(city: City, p: TLeg, date: int, offset: int, details: dict | None = None) -> dict:
    tt = city.tt
    pat = p.trip.pattern
    stops = pat.stops[p.board:p.alight + 1]
    where = (details or {}).get("where") or []

    def at(pos):
        return where[pos] if pos < len(where) else None
    leg = {"mode": pat.route.mode, "line": pat.route.short, "operator": pat.route.agency,
           "direction": pat.headsign or tt.stops[pat.stops[-1]].name, "color": route_color(pat.route),
           "from": _stop_ref(tt.stops[stops[0]], city.id, at(p.board) if details else None),
           "to": _stop_ref(tt.stops[stops[-1]], city.id, at(p.alight) if details else None),
           "depart": _fmt(date, p.trip.dep[p.board] + offset), "arrive": _fmt(date, p.trip.arr[p.alight] + offset),
           "stops": len(stops) - 1, "status": "scheduled", "trip_id": p.trip.trip_id, **headway_fields(p.trip.headway, "status"),
           "path": [pt(*c) for c in simplify(tt.leg_path(pat, p.board, p.alight), 10 if not details else 60)]}
    if details:
        leg = {**leg, "train": details.get("train"), "category": details.get("category")}
    return leg


def _city_legs(seg: Segment, date: int, nat: City, toward_station: bool) -> tuple[list[dict], int, int, float]:
    """Legs of one city part in travel order, with its real start and end (request-date clock) and
    walking metres. toward_station: the point comes first (city A), else the station does (city B)."""
    side, city, off = seg.side, seg.side.city, seg.days * DAY
    point = {"name": side.label, "at": pt(*side.at), "city": city.id}
    station = _station_ref(seg.station, nat)
    if seg.itin is None:  # a walk between the point and the station
        return [], seg.walk_secs, 0, 0.0  # filled in by _option, which knows the times
    it = seg.itin
    tt = city.tt
    legs: list[dict] = []
    walk_m = 0.0
    # walk from the point (or the station) to the first stop boarded
    first_ref = _stop_ref(tt.stops[it.access], city.id)
    start = it.start + off
    if toward_station:
        coords = side.walk.path_to(city, tt.stops[it.access])
        legs.append({"mode": "walk", "from": point, "to": first_ref, "depart": _fmt(date, start),
                     "arrive": _fmt(date, start + it.access_secs), "distance_m": round(_length(coords)),
                     "minutes": round(it.access_secs / 60), "path": [pt(*c) for c in simplify(coords, 8)]})
        walk_m += _length(coords)
    else:
        legs.append(_walk_leg(city, station, first_ref, start, it.access_secs, date))
        walk_m += legs[-1]["distance_m"]
    last_arr = it.start
    for p in it.parts:
        if isinstance(p, TLeg):
            legs.append(_transit_leg(city, p, date, off))
            last_arr = p.trip.arr[p.alight]
        else:
            fa, fb, secs = p
            legs.append(_walk_leg(city, _stop_ref(tt.stops[fa], city.id), _stop_ref(tt.stops[fb], city.id), last_arr + off, secs, date))
            walk_m += legs[-1]["distance_m"]
    last_ref = _stop_ref(tt.stops[it.egress], city.id)
    if toward_station:
        legs.append(_walk_leg(city, last_ref, station, last_arr + off, it.egress_secs, date))
        walk_m += legs[-1]["distance_m"]
    else:
        coords = side.walk.path_to(city, tt.stops[it.egress])
        coords.reverse()
        legs.append({"mode": "walk", "from": last_ref, "to": point, "depart": _fmt(date, last_arr + off),
                     "arrive": _fmt(date, last_arr + off + it.egress_secs), "distance_m": round(_length(coords)),
                     "minutes": round(it.egress_secs / 60), "path": [pt(*c) for c in simplify(coords, 8)]})
        walk_m += _length(coords)
    return legs, it.start + off, it.end + off, walk_m


def _option(seg_a: Segment, national: Itin, seg_b: Segment, nat: City, date: int) -> dict:
    ntt = nat.tt
    n_legs: list[dict] = []
    walk_m = 0.0
    last_arr = None
    for p in national.parts:
        if isinstance(p, TLeg):
            n_legs.append(_transit_leg(nat, p, date, 0, ntt.trip_details(p.trip.trip_idx) or {}))
            last_arr = p.trip.arr[p.alight]
        else:
            fa, fb, secs = p
            sa, sb = ntt.stops[fa], ntt.stops[fb]
            if sa.name == sb.name:
                continue  # a change of platform inside one station
            n_legs.append(_walk_leg(nat, _stop_ref(sa, nat.id), _stop_ref(sb, nat.id), last_arr, secs, date, estimated=True))
            walk_m += n_legs[-1]["distance_m"]
    t_legs = [p for p in national.parts if isinstance(p, TLeg)]
    n_dep, n_arr = t_legs[0].trip.dep[t_legs[0].board], t_legs[-1].trip.arr[t_legs[-1].alight]

    legs_a, a_start, a_end, wa = _city_legs(seg_a, date, nat, True)
    if seg_a.itin is None:  # walk from the start point to the station, arriving STATION_SECS before the train
        a_end = n_dep - STATION_SECS
        a_start = a_end - seg_a.walk_secs
        legs_a = [_walk_leg(seg_a.side.city, {"name": seg_a.side.label, "at": pt(*seg_a.side.at), "city": seg_a.side.city.id},
                            _station_ref(seg_a.station, nat), a_start, seg_a.walk_secs, date)]
        wa = legs_a[0]["distance_m"]
    legs_b, b_start, b_end, wb = _city_legs(seg_b, date, nat, False)
    if seg_b.itin is None:
        b_start = n_arr + STATION_SECS
        b_end = b_start + seg_b.walk_secs
        legs_b = [_walk_leg(seg_b.side.city, _station_ref(seg_b.station, nat),
                            {"name": seg_b.side.label, "at": pt(*seg_b.side.at), "city": seg_b.side.city.id},
                            b_start, seg_b.walk_secs, date)]
        wb = legs_b[0]["distance_m"]
    legs = [*legs_a, *n_legs, *legs_b]
    vehicles = sum(1 for leg in legs if leg["mode"] != "walk")
    return {"depart": _fmt(date, a_start), "arrive": _fmt(date, b_end), "duration_min": round((b_end - a_start) / 60),
            "transfers": max(0, vehicles - 1), "walk_m": round(wa + walk_m + wb),
            "stations": {"from": seg_a.station.name, "to": seg_b.station.name},
            "legs": legs, "_start": a_start, "_end": b_end}


def _empty(a, b, A, B, a_label, b_label, back, date, secs, note) -> dict:
    return {"from": {"name": a_label, "at": pt(*A), "city": a.id}, "to": {"name": b_label, "at": pt(*B), "city": b.id},
            ("arrive_by" if back else "depart_at"): " ".join(fmt_wall(date, secs)),
            "itineraries": [], "assumptions": ASSUMPTIONS, "note": note}
