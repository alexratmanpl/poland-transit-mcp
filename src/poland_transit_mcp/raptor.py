"""Round-based public transit routing (RAPTOR, Delling et al. 2012) over one day's RaptorData.
Backward ("arrive by") searches run the same code on time-reversed data."""
from __future__ import annotations

import time
from bisect import bisect_left
from dataclasses import dataclass, field

import numpy as np

from . import telemetry
from .timetable import RaptorData, RPattern

INF = 1 << 40


@dataclass
class RLeg:
    kind: str  # "transit" | "walk"
    rp: RPattern | None = None
    trip: int = -1
    board: int = -1
    alight: int = -1
    frm: int = -1  # walk: from stop
    to: int = -1
    secs: int = 0


@dataclass
class RJourney:
    access_stop: int
    access_secs: int
    egress_stop: int
    egress_secs: int
    legs: list[RLeg] = field(default_factory=list)
    arrival: int = 0
    transfers: int = 0


def raptor(data: RaptorData, transfers: list[list[tuple[int, int]]], access: dict[int, int], egress: dict[int, int],
           departure: int, max_rounds: int, min_change: int = 60) -> list[RJourney]:
    """Earliest-arrival journeys with 0..max_rounds-1 transfers (one per improving round).
    Runs in the Rust engine when the day's tables were handed to it (engine.py), else in Python."""
    name = "rust" if data.engine is not None else "python"
    direction = "backward" if data.backward else "forward"
    t = time.perf_counter()
    with telemetry.span("routing.search", **{
            "poland_transit.search_engine": name, "poland_transit.direction": direction,
            "poland_transit.routing.access_stops": len(access), "poland_transit.routing.egress_stops": len(egress),
            "poland_transit.routing.max_rounds": max_rounds}) as span:
        if data.engine is not None:
            js = _raptor_engine(data, access, egress, departure, max_rounds, min_change)
        else:
            js = _raptor_python(data, transfers, access, egress, departure, max_rounds, min_change)
        span.set_attribute("poland_transit.routing.journeys", len(js))
    telemetry.search_duration.record(time.perf_counter() - t, {"poland_transit.search_engine": name,
                                                               "poland_transit.direction": direction})
    return js


def raptor_all(data: RaptorData, transfers: list[list[tuple[int, int]]], access: dict[int, int],
               departure: int, max_rounds: int, min_change: int = 60) -> np.ndarray:
    """Earliest arrival at every stop (by transit or on foot, at most max_rounds vehicles); INF where
    unreached. The same search with no target, so nothing is pruned: it seeds searches that continue
    from many stops, such as the stations of a city on an intercity trip."""
    name = "rust" if data.engine is not None and hasattr(data.engine, "earliest") else "python"
    direction = "backward" if data.backward else "forward"
    t = time.perf_counter()
    with telemetry.span("routing.search_all", **{
            "poland_transit.search_engine": name, "poland_transit.direction": direction,
            "poland_transit.routing.access_stops": len(access), "poland_transit.routing.max_rounds": max_rounds}):
        if name == "rust":
            best = np.frombuffer(data.engine.earliest(data.engine_transfers, list(access.items()), departure,
                                                      max_rounds, min_change), "<i8")
        else:
            best = np.array(_raptor_python(data, transfers, access, {}, departure, max_rounds, min_change,
                                           want_best=True), dtype=np.int64)
    telemetry.search_duration.record(time.perf_counter() - t, {"poland_transit.search_engine": name,
                                                               "poland_transit.direction": direction})
    return best


def _raptor_python(data: RaptorData, transfers: list[list[tuple[int, int]]], access: dict[int, int],
                   egress: dict[int, int], departure: int, max_rounds: int, min_change: int,
                   want_best: bool = False) -> list[RJourney] | list[int]:
    S = data.n_stops
    K = max_rounds
    tau = [dict() for _ in range(K + 1)]  # stop → best arrival incl. foot transfers, per round
    tau_t = [dict() for _ in range(K + 1)]  # stop → arrival by transit, per round
    p_transit = [dict() for _ in range(K + 1)]  # stop → (pattern, trip, board, alight)
    p_walk = [dict() for _ in range(K + 1)]  # stop → from stop
    best = [INF] * S
    best_t = [INF] * S
    # stops improved in the last round, in the order they were reached (a dict, not a set: ties between
    # equally fast connections are then broken the same way every time, and the same way as the engine)
    marked: dict[int, None] = {}
    for s, w in access.items():
        t = departure + w
        if t < tau[0].get(s, INF):
            tau[0][s] = t
            best[s] = min(best[s], t)
            marked[s] = None
    target_best = INF
    journeys: list[RJourney] = []
    patterns = data.patterns
    stop_routes = data.stop_routes

    for k in range(1, K + 1):
        if not marked:
            break
        queue: dict[int, int] = {}
        for s in marked:
            for p, pos in stop_routes[s]:
                q = queue.get(p)
                if q is None or pos < q:
                    queue[p] = pos
        marked = {}
        prev = tau[k - 1]
        slack = min_change if k > 1 else 0
        tau_k, tau_tk, pt_k = tau[k], tau_t[k], p_transit[k]
        for p, start in queue.items():
            rp = patterns[p]
            stops, pick, drop, arr_cols, dep_cols = rp.stops, rp.pick, rp.drop, rp.arr_cols, rp.dep_cols
            trip = -1
            board = -1
            for i in range(start, len(stops)):
                s = stops[i]
                if trip >= 0 and drop[i]:
                    a = arr_cols[i][trip]
                    if a < best_t[s] and a < target_best:
                        tau_tk[s] = a
                        best_t[s] = a
                        pt_k[s] = (p, trip, board, i)
                        if a < best[s]:
                            best[s] = a
                        if a < tau_k.get(s, INF):
                            tau_k[s] = a
                            p_walk[k].pop(s, None)
                        marked[s] = None
                if pick[i]:
                    pv = prev.get(s)
                    if pv is not None:
                        need = pv + slack
                        col = dep_cols[i]
                        if trip < 0 or need <= col[trip]:
                            hi = rp.n_trips if trip < 0 else trip + 1
                            t2 = bisect_left(col, need, 0, hi)
                            if t2 < hi and (trip < 0 or t2 < trip):
                                trip, board = t2, i
        # foot transfers from stops reached by transit in this round
        for s in list(marked):
            a0 = tau_tk.get(s)
            if a0 is None:
                continue
            for s2, w in transfers[s]:
                a = a0 + w
                if a < tau_k.get(s2, INF) and a < best[s2] and a < target_best:
                    tau_k[s2] = a
                    best[s2] = a
                    p_walk[k][s2] = s
                    marked[s2] = None
        best_e, best_arr = -1, INF
        for s, w in egress.items():
            a = tau_tk.get(s)
            if a is not None and a + w < best_arr:
                best_arr, best_e = a + w, s
        if best_e >= 0 and best_arr < target_best:
            target_best = best_arr
            journeys.append(_reconstruct(k, best_e, egress[best_e], best_arr, access, patterns, tau, tau_t, p_transit, p_walk))
    return best if want_best else journeys


def _raptor_engine(data: RaptorData, access: dict[int, int], egress: dict[int, int], departure: int,
                   max_rounds: int, min_change: int) -> list[RJourney]:
    """The same search in Rust; its answers are turned into the same RJourney objects."""
    out = []
    for k, arrival, egress_stop, raw in data.engine.search(data.engine_transfers, list(access.items()), list(egress.items()),
                                                          departure, max_rounds, min_change):
        legs = [RLeg("transit", rp=data.patterns[a], trip=b, board=c, alight=d) if kind == 0
                else RLeg("walk", frm=a, to=b, secs=c) for kind, a, b, c, d in raw]
        first = legs[0].rp.stops[legs[0].board]  # where the first vehicle is boarded
        out.append(RJourney(first, access.get(first, 0), egress_stop, egress[egress_stop], legs, arrival, k - 1))
    return out


def _reconstruct(k, egress_stop, egress_secs, arrival, access, patterns, tau, tau_t, p_transit, p_walk) -> RJourney:
    legs: list[RLeg] = []
    s = egress_stop
    rnd = k
    via_transit = True  # egress always leaves from a transit arrival
    while rnd > 0:
        if not via_transit and s in p_walk[rnd]:
            frm = p_walk[rnd][s]
            legs.append(RLeg("walk", frm=frm, to=s, secs=tau[rnd][s] - tau_t[rnd][frm]))
            s = frm
        p, trip, board, alight = p_transit[rnd][s]
        rp = patterns[p]
        legs.append(RLeg("transit", rp=rp, trip=trip, board=board, alight=alight))
        s = rp.stops[board]
        rnd -= 1
        via_transit = False
    legs.reverse()
    return RJourney(s, access.get(s, 0), egress_stop, egress_secs, legs, arrival, k - 1)
