"""Stop names and platforms at import: a trailing two-digit number is a platform only in feeds that name stops so."""
import sqlite3

from poland_transit_mcp.build.gtfs import import_feed
from poland_transit_mcp.config import CITIES
from poland_transit_mcp.schema import SCHEMA


def feed_by_key(city: str, key: str):
    return next(f for f in CITIES[city].feeds if f.key == key)


def imported_stops(tmp_path, feed, stops: list[tuple[str, str]]) -> dict[str, tuple[str, str | None]]:
    """Imports a minimal feed with these (stop_id, stop_name) stops; returns stop_id -> (name, platform)."""
    d = tmp_path / feed.key
    d.mkdir()
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nA,Test,https://example.invalid,Europe/Warsaw\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,stop_timezone,platform_code\n"
                     + "".join(f'{i},"{n}",52.{k:02d},18.5,Europe/Warsaw,\n' for k, (i, n) in enumerate(stops)),
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\nR,A,1,,3\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "S,1,1,1,1,1,1,1,20260101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id\nR,S,T1\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          + "".join(f"T1,10:{k:02d}:00,10:{k:02d}:00,{i},{k + 1}\n" for k, (i, _) in enumerate(stops)),
    }
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    import_feed(db, d, feed)
    rows = db.execute("SELECT stop_id, name, platform FROM stops").fetchall()
    return {sid.split(":", 1)[1]: (name, platform) for sid, name, platform in rows}


def test_flixbus_names_keep_their_trailing_numbers(tmp_path):
    got = imported_stops(tmp_path, feed_by_key("poland", "FX"), [
        ("koz", "Kostrzyn nad Odra, Dworcowa Street 22 and 17"),
        ("cem", "Chelm, Armii Krajowej Street/KFC 05 and 06"),
        ("wpk", "Warsaw, Metro Politechnika 02"),
    ])
    assert got == {
        "koz": ("Kostrzyn nad Odra, Dworcowa Street 22 and 17", None),
        "cem": ("Chelm, Armii Krajowej Street/KFC 05 and 06", None),
        "wpk": ("Warsaw, Metro Politechnika 02", None),
    }


def test_gdansk_names_still_carry_the_platform(tmp_path):
    """ZTM Gdańsk numbers nearly every stop (1,621 of 1,626 on 2026-11-17), so every trailing number is a post."""
    stops = [("1", "Oliwa PKP 02"), ("2", "Jelitkowo 31"), ("3", "Pokładowa 91")] + [
        (f"x{k}", f"Przystanek {k} {k + 10:02d}") for k in range(10)] + [("4", "Targ Rybny")]
    got = imported_stops(tmp_path, feed_by_key("gdansk", "G"), stops)
    assert got["1"] == ("Oliwa PKP", "02") and got["2"] == ("Jelitkowo", "31") and got["3"] == ("Pokładowa", "91")
    assert got["4"] == ("Targ Rybny", None)


def test_wroclaw_splits_platforms_but_keeps_house_numbers(tmp_path):
    """Wrocław numbers only a few busy stops; its other trailing numbers are street numbers (2026-11-17 feed)."""
    galeria = [(f"g{k:02d}", f"GALERIA DOMINIKAŃSKA {k:02d}") for k in range(8, 13)]
    got = imported_stops(tmp_path, feed_by_key("wroclaw", "W"), galeria + [
        ("p1", "PL. JANA PAWŁA II 01"), ("b65", "Bierutowska 65"), ("b75", "Bierutowska 75"),
        ("op", "Opatowicka nr 59"), ("kw", "Kowalska 56"), ("rk", "Rynek")]
        + [(f"z{k}", f"Zwykły przystanek {chr(65 + k)}") for k in range(20)])
    assert got["g10"] == ("GALERIA DOMINIKAŃSKA", "10") and got["g08"] == ("GALERIA DOMINIKAŃSKA", "08")
    assert got["p1"] == ("PL. JANA PAWŁA II", "01")
    for sid, name in (("b65", "Bierutowska 65"), ("b75", "Bierutowska 75"), ("op", "Opatowicka nr 59"),
                      ("kw", "Kowalska 56"), ("rk", "Rynek")):
        assert got[sid] == (name, None)


def test_gzm_names_stay_whole(tmp_path):
    """GZM has no platform numbers in names (20 of 7,269 end in two digits, all part of the name)."""
    got = imported_stops(tmp_path, feed_by_key("katowice", "K"), [
        ("s", "Gołonóg Szkoła Podstawowa nr 11"), ("c", "Chorzów Szkoła 13"), ("t", "Tychy Stoczniowców 70"),
        ("r", "Katowice Rynek")])
    assert got == {"s": ("Gołonóg Szkoła Podstawowa nr 11", None), "c": ("Chorzów Szkoła 13", None),
                   "t": ("Tychy Stoczniowców 70", None), "r": ("Katowice Rynek", None)}


def test_platform_splitter_edges():
    """'nr' and 'nr.' keep the number even when zero-padded; one name splits all its numbers or none; a station
    row (location_type 1) is not counted by import_feed, and an unknown name is never split."""
    from poland_transit_mcp.build.gtfs import platform_splitter
    names = ["Rynek 01", "Rynek 12", "Szkoła nr 05", "Szkoła nr. 07", "Dom 44"] + [f"Plain {k}" for k in "ABCDEFGHIJ"]
    split = platform_splitter(names)
    assert split("Rynek 01")[2] == "01" and split("Rynek 12")[2] == "12"
    assert split("Szkoła nr 05") is None and split("Szkoła nr. 07") is None and split("Dom 44") is None
    assert split("Nowhere 03") is None
