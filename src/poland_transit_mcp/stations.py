"""plan_stations: trains and coaches between stations of the national layer, from several start stops to
several end stops, each with its own minutes.

A caller that brings the traveller to a station itself (another country's timetable, a car, a flight)
knows when the traveller can be at each station, and how long each end station is from where they are
going. Those minutes are RAPTOR's access and egress maps, so this is the same search as plan_trip's, with
no walk or city transport added: a start stop's minutes count from depart_at until the traveller is ready
to board there, and an end stop's minutes from arriving there until the trip is over. An option's
depart is its first train's departure minus the start stop's minutes; its arrive is the last arrival
plus the end stop's minutes. Each option names the stop and the minutes it used.

The timetable day searched is the one of the earliest ready time (depart_at plus the smallest start minutes;
arrive_by: arrive_by minus the smallest end minutes), so minutes that cross midnight search the next (previous)
day. Unlike plan_trip within a city, options are not limited to a few hours after the requested time: trains
between countries are few.
"""
from __future__ import annotations

from .errors import Refused
from .city import City
from .intercity import DAY, NATIONAL_MIN_CHANGE, _day_for, _fmt, _stop_ref, _transit_leg, _walk_leg
from .plan import MAX_ITINERARIES, TLeg, _to_itin, ranked, tightened
from .raptor import raptor
from .timeutil import fmt_date, fmt_wall, parse_when
from .text import quoted
from .transit import resolve_stops

MAX_STOPS = 20  # per side
MAX_MINUTES = 24 * 60


def _side(nat: City, refs: list[dict], what: str) -> tuple[dict[int, int], dict[int, tuple[str, int]]]:
    """{stop idx: seconds} and {stop idx: (the caller's ref, minutes)}; a station name stands for all its stops."""
    if not refs:
        raise Refused(f"give at least one {what} stop")
    if len(refs) > MAX_STOPS:
        raise Refused(f"at most {MAX_STOPS} {what} stops")
    secs: dict[int, int] = {}
    used: dict[int, tuple[str, int]] = {}
    for r in refs:
        ref, minutes = str(r["stop"]), int(r.get("minutes", 0))
        if not 0 <= minutes <= MAX_MINUTES:
            raise Refused(f'{what} stop {quoted(ref)}: minutes must be 0..{MAX_MINUTES}')
        stops = resolve_stops(nat, ref)
        if not stops:
            raise Refused(f'no {what} stop {quoted(ref)} in {nat.id}; find stations with find_places')
        for s in stops:
            if minutes * 60 < secs.get(s.idx, 1 << 40):
                secs[s.idx] = minutes * 60
                used[s.idx] = (ref, minutes)
    return secs, used


def plan_stations(nat: City, starts: list[dict], ends: list[dict], depart_at: str | None = None,
                  arrive_by: str | None = None, max_transfers: int = 2) -> dict:
    """starts, ends: [{"stop": stop_id or station name, "minutes": int}]."""
    backward = bool(arrive_by)
    when = parse_when(arrive_by or depart_at)
    if not nat.has_transit:
        raise Refused(f"{nat.id} has no timetable data")
    acc, acc_used = _side(nat, starts, "start")
    egr, egr_used = _side(nat, ends, "end")
    both = sorted({nat.tt.stops[i].name for i in acc.keys() & egr.keys()})
    if both:
        raise Refused(f"a stop is both a start and an end: {', '.join(both)}")
    max_transfers = max(0, min(max_transfers if max_transfers is not None else 2, 4))
    date = when.date
    tt = nat.tt
    ready = when.secs - min(egr.values()) if backward else when.secs + min(acc.values())
    day_date, k = _day_for(date, ready, backward)  # the timetable day of the earliest ready time
    shift = k * DAY  # that day's clock → the request date's clock
    day = tt.day(day_date)

    def search(back: bool, t: int, rounds: int):
        js = raptor(tt.raptor(day, back), tt.transfers, egr if back else acc, acc if back else egr,
                    -t if back else t, rounds, NATIONAL_MIN_CHANGE)
        return [_to_itin(j, back) for j in js]

    found = ranked(tightened(search, when.secs - shift, backward, max_transfers + 1), backward)[:MAX_ITINERARIES]
    head = {"from": [{"stop": r["stop"], "minutes": int(r.get("minutes", 0))} for r in starts],
            "to": [{"stop": r["stop"], "minutes": int(r.get("minutes", 0))} for r in ends],
            ("arrive_by" if backward else "depart_at"): " ".join(fmt_wall(date, when.secs))}
    options = [_option(nat, it, acc_used, egr_used, date, shift) for it in found]
    note = None if options else (f"no connection with ≤{max_transfers} changes from these stops in the timetable "
                                 f"of {fmt_date(day_date)}")
    notes = [n for n in (nat.partial_note(day_date), nat.transit_note(day_date)) if n]
    return {**head, "itineraries": options, "note": "; ".join(filter(None, [note, *notes])) or None}


def _option(nat: City, it, acc_used: dict, egr_used: dict, date: int, shift: int) -> dict:
    """One option; times on the searched day's clock, shown on the request date's (shift apart).
    It starts with a train or coach: RAPTOR adds no walk before the first one."""
    tt = nat.tt
    legs: list[dict] = []
    last_arr = None
    for p in it.parts:
        if isinstance(p, TLeg):
            legs.append(_transit_leg(nat, p, date, shift, tt.trip_details(p.trip.trip_idx) or {}))
            last_arr = p.trip.arr[p.alight]
        else:
            fa, fb, secs = p
            sa, sb = tt.stops[fa], tt.stops[fb]
            if sa.name == sb.name:
                continue  # a change of platform inside one station
            legs.append(_walk_leg(nat, _stop_ref(sa, nat.id), _stop_ref(sb, nat.id), last_arr + shift, secs, date, estimated=True))
    (a_ref, a_min), (b_ref, b_min) = acc_used[it.access], egr_used[it.egress]
    return {"depart": _fmt(date, it.start + shift), "arrive": _fmt(date, it.end + shift), "duration_min": round((it.end - it.start) / 60),
            "transfers": it.transfers,
            "start": {"stop": a_ref, "name": tt.stops[it.access].name, "stop_id": tt.stops[it.access].id, "minutes": a_min},
            "end": {"stop": b_ref, "name": tt.stops[it.egress].name, "stop_id": tt.stops[it.egress].id, "minutes": b_min},
            "legs": legs}
