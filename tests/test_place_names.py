"""A place name without a point: among comparable matches the one nearer the city's centre (its main station) wins,
the city's own name before a stop's name counts as no part of it, and an answer says in `note` when the match may not
be the place meant (far from the centre, or another place scored about the same)."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest
from fixture import at

from poland_transit_mcp import places
from poland_transit_mcp.city import City
from poland_transit_mcp.places import centre, find_places, resolve_point
from poland_transit_mcp.text import fold
from poland_transit_mcp.transit import walk_route

KM_SOUTH = 1 / 111.32  # degrees of latitude per km


@pytest.fixture(scope="module")
def named(city_dir, tmp_path_factory) -> City:
    """testowo with places that share names; its centre is the station Testowo Główne at (2, 0)."""
    d = tmp_path_factory.mktemp("names")
    shutil.copy(city_dir / "testowo.sqlite", d / "testowo.sqlite")
    db = sqlite3.connect(d / "testowo.sqlite")
    pid = db.execute("SELECT max(id) FROM places").fetchone()[0]
    c_lat, c_lon = at(2, 0)

    def place(kind, name, p, extra=None):
        nonlocal pid
        pid += 1
        db.execute("INSERT INTO places(id, kind, name, name_norm, lat, lon, ref, extra) VALUES (?,?,?,?,?,?,?,?)",
                   (pid, kind, name, fold(name), p[0], p[1], f"n{pid}", json.dumps(extra) if extra else None))
        db.execute("INSERT INTO places_rtree(id, min_lat, max_lat, min_lon, max_lon) VALUES (?,?,?,?,?)",
                   (pid, p[0], p[0], p[1], p[1]))

    # "Wiadukt": a street fragment of that bare name 20 km out, and the stop "Testowo Wiadukt" in the centre (like "Katowice Rondo")
    place("street", "Wiadukt", [c_lat - 20 * KM_SOUTH, c_lon])
    place("stop", "Testowo Wiadukt", at(3, 1), {"stop_id": "T:R"})
    # "Ratusz": a landmark of that name in the centre, and a better-known one (Wikipedia) 15 km out
    place("landmark", "Ratusz", at(4, 2))
    place("landmark", "Ratusz", [c_lat - 15 * KM_SOUTH, c_lon], {"wikipedia": "pl:Ratusz"})
    # "Kopiec": only 20 km out
    place("landmark", "Kopiec", [c_lat - 20 * KM_SOUTH, c_lon])
    # "Brama": two of the same kind, as far from the centre, 2 km apart
    place("landmark", "Brama", at(12, 0))
    place("landmark", "Brama", at(2, 14))
    db.commit()
    db.close()
    return City.open(d, "testowo")


def test_the_centre_is_the_main_station(named):
    (lat, lon), label = centre(named)
    assert [lat, lon] == pytest.approx(at(2, 0)) and label == "Testowo Główne"


def test_a_stop_named_by_the_query_in_the_centre_beats_a_far_street_of_that_bare_name(named):
    p, label, hit = resolve_point(named, "Wiadukt")
    assert label == "Testowo Wiadukt" and hit["kind"] == "stop" and "match_note" not in hit
    assert find_places(named, "Wiadukt")[0]["name"] == "Testowo Wiadukt"


def test_of_two_places_of_one_name_the_nearer_wins(named):
    p, label, hit = resolve_point(named, "Ratusz")
    assert label == "Ratusz" and list(p) == pytest.approx(at(4, 2))
    assert "match_note" not in hit  # the far one scores clearly lower


def test_a_far_match_says_how_far_from_the_centre(named):
    _, label, hit = resolve_point(named, "Kopiec")
    assert label == "Kopiec"
    assert hit["match_note"] == '"Kopiec" matched Kopiec (landmark), 20 km from the centre (Testowo Główne)'


def test_a_far_match_names_a_nearer_place(named, monkeypatch):
    monkeypatch.setattr(places, "CENTRE_MAX", 0)  # no pull to the centre: the far Ratusz (Wikipedia) wins
    _, _, hit = resolve_point(named, "Ratusz")
    assert hit["match_note"] == ('"Ratusz" matched Ratusz (landmark), 15 km from the centre (Testowo Główne); '
                                 "nearer: Ratusz (landmark, 0.3 km)")


def test_another_place_with_about_the_same_score_is_named(named):
    _, label, hit = resolve_point(named, "Brama")
    assert label == "Brama"
    assert hit["match_note"].startswith('"Brama" matched Brama (landmark), 1.0 km from the centre (Testowo Główne); '
                                        "also: Brama (landmark, ")


def test_the_note_reaches_the_answer(named):
    r = walk_route(named, "Brama", at(2, 2))
    assert r["distance_m"] and r["note"].startswith('"Brama" matched Brama (landmark)')
    clear = walk_route(named, "Wiadukt", at(2, 2))
    assert clear["from"]["name"] == "Testowo Wiadukt" and clear["note"] is None


def test_without_a_main_station_the_centre_is_the_middle_of_the_box(named, monkeypatch):
    class NoStation:
        id, bbox, db = "nowhere", [50.0, 19.0, 50.2, 19.4], named.db
    (lat, lon), label = centre(NoStation())
    assert (lat, lon) == pytest.approx((50.1, 19.2)) and label is None
