"""Stop names are resolved from the timetable's index of folded names: the same stops, in the same order, and the
same refusals as the scan over every stop it replaced, at a cost that does not grow with the number of stops."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from poland_transit_mcp import text, timetable, transit
from poland_transit_mcp.errors import Refused
from poland_transit_mcp.schema import SCHEMA
from poland_transit_mcp.shared_db import SharedConnection
from poland_transit_mcp.text import fold, quoted
from poland_transit_mcp.timetable import Timetable


def scan_resolve_stops(city, ref: str):
    """resolve_stops as it was before the index: up to three folds of every stop's name per call."""
    tt = city.tt
    s = tt.stop_by_id.get(ref) or next((tt.stop_by_id[f'{f["key"]}:{ref}'] for f in city.feeds
                                         if f'{f["key"]}:{ref}' in tt.stop_by_id), None)
    if s:
        return [s]
    n = fold(ref)
    found = [s for s in tt.stops if s and fold(s.name) == n]
    if not found and len(n) > 3 and n[-3] == " " and n[-2:].isdigit():
        n = n[:-3]
    found = found or [s for s in tt.stops if s and fold(s.name) == n] or [s for s in tt.stops if s and fold(s.name).startswith(n)]
    names = sorted({s.name for s in found})
    if len(names) > 1:
        raise Refused(f'{quoted(ref)} matches several stops: {", ".join(names[:8])}; pass one of these names')
    if not found and len(n) >= 4:
        within = sorted({s.name for s in tt.stops if s and n in fold(s.name)})
        if within:
            raise Refused(f'no stop named {quoted(ref)} in {city.id}; stops with that in their name: '
                          f'{", ".join(within[:8])}; pass one of these names')
    return found


def outcome(fn, city, ref):
    try:
        return [s.idx for s in fn(city, ref)]
    except Refused as e:
        return f"refused: {e}"


NAMES = ["Dworzec Główny", "Dworzec Główny", "DWORZEC GŁÓWNY", "Dworzec st. 17", "Oliwa PKP", "Oliwa PKP",
         "Plac Grunwaldzki", "pl. Wolności", "Teatr Bagatela", "Aleja Róż", "Łódź Kaliska", "Lodz Fabryczna",
         "Szkoła Podstawowa nr 11", "Bierutowska 65", "Brama", "Bramka", "Brama Wyżynna", "Zajezdnia", "Zajęcza",
         "Stacja 00100", "Stacja 00100 02", "Stacja 001000", "Stacja 00101", "!!", "Ü-Bahn Süd"]


def synthetic_city(names: list[str], gaps: bool = True):
    """A timetable holding only stops with these names (every other index left empty when `gaps`)."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(SCHEMA)
    for i, name in enumerate(names):
        idx = 2 * i + 1 if gaps else i
        db.execute("INSERT INTO stops(stop_idx, feed, stop_id, name, name_norm, lat, lon) VALUES (?,?,?,?,?,?,?)",
                   (idx, "T", f"T:{idx}", name, fold(name), 50.0, 19.0))
    tt = Timetable(SharedConnection(db), city_id="synthetic")
    return SimpleNamespace(tt=tt, feeds=[{"key": "T"}], id="synthetic")


REFS = ["Dworzec Główny", "dworzec glowny", "DWORZEC", "dworzec st. 17", "Dworzec st", "Oliwa PKP 02", "oliwa pkp",
        "Oliwa", "Plac Grunwaldzki", "pl grunwaldzki", "Plac Wolności", "Bagatela", "aleja roz", "al. Róż",
        "lodz", "Łódź", "Szkoła Podstawowa nr 11", "Bierutowska 65", "Bierutowska 6", "Bram", "Brama", "Bramka",
        "Zaj", "Stacja 00100", "Stacja 00100 02", "Stacja 00100 03", "Stacja 0010", "Stacja 00102", "Stacja 001",
        "!!", "", "  ", "...", "ubahn", "u bahn sud", "Süd", "Nowhere", "nowh", "T:1", "1", "3", "T:999", "999",
        "PL. GRUNWALDZKI", "abc 12", "zzzzzz"]


def test_index_answers_as_the_scan_did_on_tricky_names():
    for gaps in (True, False):
        city = synthetic_city(NAMES, gaps)
        for ref in REFS:
            assert outcome(transit.resolve_stops, city, ref) == outcome(scan_resolve_stops, city, ref), ref


def test_index_answers_as_the_scan_did_on_the_fixtures(city, tmp_path):
    from fixture import build_national_fixture

    alfa, beta, nat = build_national_fixture(tmp_path / "nat")
    for c in (city, alfa, nat):
        refs = {s.name for s in c.tt.stops if s} | {s.id for s in c.tt.stops if s}
        refs |= {r[:k] for r in list(refs) for k in (1, 3, 4, 6)} | {r.upper() for r in refs} | {r + " 02" for r in refs}
        refs |= {r.split(":", 1)[-1] for r in refs}
        for ref in sorted(refs):
            assert outcome(transit.resolve_stops, c, ref) == outcome(scan_resolve_stops, c, ref), (c.id, ref)


def test_resolving_a_name_does_not_fold_every_stop(monkeypatch):
    """The cost of a name is a few folds of the caller's text, whatever the number of stops."""
    names = [f"Stacja {i:05d}" for i in range(3000)]
    city = synthetic_city(names, gaps=False)
    calls = []
    real = text.fold

    def counting(s):
        calls.append(s)
        return real(s)

    monkeypatch.setattr(transit, "fold", counting)
    monkeypatch.setattr(timetable, "fold", counting)
    for ref in ("Stacja 00100", "Stacja 01234 02", "stacja 0299"):
        assert outcome(transit.resolve_stops, city, ref) == outcome(scan_resolve_stops, city, ref)
    calls.clear()
    for ref in ("Stacja 00100", "Stacja 01234 02", "Stacja 0299", "Nowhere"):
        try:
            transit.resolve_stops(city, ref)
        except Refused:
            pass
    assert len(calls) <= 4, len(calls)


@pytest.mark.parametrize("ref", ["Stacja", "stacja 0"])
def test_a_prefix_of_many_names_is_refused_with_the_first_eight(ref):
    city = synthetic_city([f"Stacja {i:03d}" for i in range(50)])
    with pytest.raises(Refused, match=r"matches several stops: Stacja 000, Stacja 001, .*Stacja 007; pass"):
        transit.resolve_stops(city, ref)
