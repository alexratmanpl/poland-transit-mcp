"""plan_trip: walk + transit (RAPTOR) + walk, up to 3 itineraries."""
from __future__ import annotations

from dataclasses import dataclass

from .city import City
from .geo import pt, simplify
from .places import resolve_point
from .raptor import RJourney, raptor
from .timetable import DayTrip, headway_fields, route_color
from .timeutil import fmt_date, fmt_wall, parse_when
from .transit import WalkSearch, walk_path, walk_to_stops
from .walk import WALK_SPEED_MPS

ACCESS_SECS = 15 * 60
MAX_ITINERARIES = 3
MAX_WAIT_SECS = 4 * 3600  # options leaving > 4 h after depart_at (or arriving > 4 h before arrive_by) are dropped
WALK_ONLY_MAX_M = 5000
WALK_FIRST_MAX_M = 2500  # walks up to this length are ranked first when transit barely saves time
WALK_FIRST_SLACK_S = 300


@dataclass
class TLeg:
    trip: DayTrip
    board: int
    alight: int


@dataclass
class Itin:
    access: int
    access_secs: int
    egress: int
    egress_secs: int
    parts: list  # TLeg | (from_stop, to_stop, secs)
    key: str
    start: int
    end: int
    transfers: int


def _to_itin(j: RJourney, backward: bool) -> Itin:
    parts: list = []
    for leg in j.legs:
        if leg.kind == "walk":
            parts.append((leg.to, leg.frm, leg.secs) if backward else (leg.frm, leg.to, leg.secs))
        else:
            n = len(leg.rp.stops)
            trip = leg.rp.trips[leg.trip]
            parts.append(TLeg(trip, n - 1 - leg.alight, n - 1 - leg.board) if backward else TLeg(trip, leg.board, leg.alight))
    if backward:
        parts.reverse()
    legs = [p for p in parts if isinstance(p, TLeg)]
    acc, acc_s = (j.egress_stop, j.egress_secs) if backward else (j.access_stop, j.access_secs)
    egr, egr_s = (j.access_stop, j.access_secs) if backward else (j.egress_stop, j.egress_secs)
    return Itin(acc, acc_s, egr, egr_s, parts,
                "|".join(f"{l.trip.trip_idx}:{l.board}:{l.alight}" for l in legs),
                legs[0].trip.dep[legs[0].board] - acc_s, legs[-1].trip.arr[legs[-1].alight] + egr_s, len(legs) - 1)


def tightened(search, secs: int, backward: bool, rounds: int) -> list[Itin]:
    """Up to MAX_ITINERARIES distinct options. search(back, t, rounds) → itineraries; its access and egress
    maps carry each start and end stop's own seconds, so an option's start is its first departure minus
    that stop's seconds, and its end the last arrival plus that stop's seconds.

    Each option = earliest arrival for a departure window, tightened by a reverse search to the latest
    departure that still makes that arrival (and vice versa for arrive_by). The next window starts
    just after (before) it, so options are distinct and none involves needless waiting. The window moves
    in the traveller's own time (start or end), so a stop with more seconds is not cut off by an option
    that used another stop."""
    found: dict[str, Itin] = {}
    t = secs
    for _ in range(MAX_ITINERARIES):
        if len(found) >= MAX_ITINERARIES:
            break
        first = search(backward, t, rounds)
        if not first:
            break
        tight = []
        for it in first:
            r = [x for x in search(not backward, it.start if backward else it.end, it.transfers + 1)
                 if (x.end <= it.end if backward else x.start >= it.start)]
            tight.append(min(r, key=lambda x: x.end) if backward and r else max(r, key=lambda x: x.start) if r else it)
        for it in tight:
            found.setdefault(it.key, it)
        t = min(i.end for i in tight) - 60 if backward else max(i.start for i in tight) + 60
    return list(found.values())


def ranked(lst: list[Itin], backward: bool) -> list[Itin]:
    """Best first (arrive_by: latest start), without options another one beats on start, end and changes."""
    lst = sorted(lst, key=(lambda x: (-x.start, x.transfers)) if backward else (lambda x: (x.end, x.transfers)))
    return [x for x in lst if not any(y is not x and y.start >= x.start and y.end <= x.end and y.transfers <= x.transfers
                                      and (y.start > x.start or y.end < x.end or y.transfers < x.transfers) for y in lst)]


def plan_trip(city: City, frm, to, depart_at: str | None = None, arrive_by: str | None = None,
              max_transfers: int = 2) -> dict:
    A, a_label, _ = resolve_point(city, frm)
    B, b_label, _ = resolve_point(city, to)
    backward = bool(arrive_by)
    when = parse_when(arrive_by or depart_at)
    max_transfers = max(0, min(max_transfers if max_transfers is not None else 2, 4))
    date = when.date
    today = fmt_date(date)

    def fmt(s: int) -> str:
        wd, wt = fmt_wall(date, s)
        return wt if wd == today else f"{wd} {wt}"

    o = {"name": a_label, "at": pt(*A)}
    d = {"name": b_label, "at": pt(*B)}
    head = {"from": o, "to": d, ("arrive_by" if backward else "depart_at"): " ".join(fmt_wall(date, when.secs))}
    walk = walk_path(city, A, B, 2 * WALK_ONLY_MAX_M) if _straight(A, B) <= WALK_ONLY_MAX_M else None
    walk_itin = None
    if walk:
        ws = round(walk.secs)
        start = when.secs - ws if backward else when.secs
        walk_itin = {"depart": fmt(start), "arrive": fmt(start + ws), "duration_min": round(ws / 60), "transfers": 0,
                     "walk_m": round(walk.meters),
                     "legs": [{"mode": "walk", "from": o, "to": d, "distance_m": round(walk.meters), "minutes": round(ws / 60),
                               "path": [pt(*c) for c in simplify(walk.coords, 8)]}]}
    note = city.transit_note(date)
    if not city.has_transit or note:
        return {**head, "itineraries": [walk_itin] if walk_itin else [], "note": note}

    tt = city.tt
    acc = walk_to_stops(city, A, ACCESS_SECS)
    if not acc.stops:
        acc = walk_to_stops(city, A, 2 * ACCESS_SECS)
    egr = walk_to_stops(city, B, ACCESS_SECS)
    if not egr.stops:
        egr = walk_to_stops(city, B, 2 * ACCESS_SECS)
    day = tt.day(date)

    def search(back: bool, t: int, rounds: int) -> list[Itin]:
        js = raptor(tt.raptor(day, back), tt.transfers, egr.stops if back else acc.stops,
                    acc.stops if back else egr.stops, -t if back else t, rounds)
        return [_to_itin(j, back) for j in js]

    lst = [i for i in tightened(search, when.secs, backward, max_transfers + 1)
           if (when.secs - i.end if backward else i.start - when.secs) <= MAX_WAIT_SECS]
    lst = ranked(lst, backward)[:MAX_ITINERARIES]

    def stop_ref(idx: int) -> dict:
        s = tt.stops[idx]
        return {"name": s.name, "stop_id": s.id, "platform": s.platform, "at": pt(s.lat, s.lon)}

    def walk_leg(frm_: dict, to_: dict, start: int, secs: int, coords, meters: float) -> dict:
        return {"mode": "walk", "from": frm_, "to": to_, "depart": fmt(start), "arrive": fmt(start + secs),
                "distance_m": round(meters), "minutes": max(0, round(secs / 60)),
                "path": [pt(*c) for c in simplify(coords, 8)]}

    def search_leg(ws: WalkSearch, stop_idx: int, reverse: bool) -> tuple[list, float]:
        s = tt.stops[stop_idx]
        coords = ws.path_to(city, s)
        if reverse:
            coords.reverse()
        return coords, _length(coords)

    itineraries = []
    for it in lst:
        legs, walk_m = [], 0.0
        coords, m = search_leg(acc, it.access, False)
        legs.append(walk_leg(o, stop_ref(it.access), it.start, it.access_secs, coords, m))
        walk_m += m
        last_arr = it.start
        for p in it.parts:
            if not isinstance(p, TLeg):
                a, b, secs = p
                sa, sb = tt.stops[a], tt.stops[b]
                w = walk_path(city, (sa.lat, sa.lon), (sb.lat, sb.lon), 2000)
                cs = w.coords if w else [(sa.lat, sa.lon), (sb.lat, sb.lon)]
                mm = w.meters if w else secs * WALK_SPEED_MPS
                legs.append(walk_leg(stop_ref(a), stop_ref(b), last_arr, secs, cs, mm))
                walk_m += mm
                continue
            pat = p.trip.pattern
            stops = pat.stops[p.board:p.alight + 1]
            last_arr = p.trip.arr[p.alight]
            legs.append({"mode": pat.route.mode, "line": pat.route.short, "operator": pat.route.agency,
                         "direction": pat.headsign or tt.stops[pat.stops[-1]].name, "color": route_color(pat.route),
                         "from": stop_ref(stops[0]), "to": stop_ref(stops[-1]), "depart": fmt(p.trip.dep[p.board]),
                         "arrive": fmt(last_arr), "stops": len(stops) - 1, "status": "scheduled", "trip_id": p.trip.trip_id,
                         **headway_fields(p.trip.headway, "status"),
                         "path": [pt(*c) for c in simplify(tt.leg_path(pat, p.board, p.alight), 10)]})
        coords, m = search_leg(egr, it.egress, True)
        legs.append(walk_leg(stop_ref(it.egress), d, last_arr, it.egress_secs, coords, m))
        walk_m += m
        itineraries.append({"depart": fmt(it.start), "arrive": fmt(it.end), "duration_min": round((it.end - it.start) / 60),
                            "transfers": it.transfers, "walk_m": round(walk_m), "legs": legs})
    # a plain walk is offered when it is short or beats every transit option
    # a plain walk comes first when it is short, or when transit saves less than ~5 minutes over it
    walk_note = None
    if walk_itin:
        best_transit = min((i.end - i.start for i in lst), default=None)
        if (walk.meters <= 1200 or best_transit is None or walk.secs <= best_transit
                or (walk.meters <= WALK_FIRST_MAX_M and walk.secs <= best_transit + WALK_FIRST_SLACK_S)):
            itineraries.insert(0, walk_itin)
            itineraries = itineraries[:MAX_ITINERARIES]
            if best_transit is not None and walk.secs > best_transit:
                walk_note = (f"walking ({round(walk.secs / 60)} min) is listed first: the fastest transit option "
                             f"saves only {round((walk.secs - best_transit) / 60)} min")
    return {**head, "itineraries": itineraries,
            "note": walk_note if itineraries else
            f"no connection with ≤{max_transfers} transfers within {MAX_WAIT_SECS // 3600} h of the requested time"}


def _straight(a, b) -> float:
    from .geo import haversine
    return haversine(a[0], a[1], b[0], b[1])


def _length(coords) -> float:
    from .geo import haversine
    return sum(haversine(p[0], p[1], q[0], q[1]) for p, q in zip(coords, coords[1:]))
