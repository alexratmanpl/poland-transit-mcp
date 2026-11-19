"""The published input schema is the real contract: what it allows is answered or refused in one readable line, what
it refuses never reaches the tools, and no input crashes a tool or quotes itself back at length."""
import anyio
import pytest
from fixture import at
from mcp import Client

from poland_transit_mcp.server import create_server

PLAN = {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-11-10 07:58"}


@pytest.fixture(scope="module")
def server(city):
    return create_server({"testowo": city})


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args)
            return next(b.text for b in r.content if b.type == "text"), r.is_error
    return anyio.run(go)


def refused(server, name, args, *words):
    text, err = call(server, name, args)
    assert err, text
    assert text != f"Error executing tool {name}", "a bare error: the tool crashed"
    for w in words:
        assert w in text, text
    return text


# ---------------------------------------------------------------- render_map takes a trip one way

def test_an_itinerary_is_refused_and_the_refusal_names_trip(server):
    """render_map takes a plan option as its `trip` reference only; the argument it took before is refused with what
    to pass instead."""
    refused(server, "render_map", {"city": "testowo", "itinerary": {"legs": [{"path": [[50.0, 19.0]]}]}},
            'unknown argument "itinerary"', "pass its `trip` value as trip")


# ---------------------------------------------------------------- lengths of text and lists

@pytest.mark.parametrize("name,args,where", [
    ("departures", {"city": "testowo", "stop_id": "a" * 3_900_000}, "stop_id"),
    ("departures", {"city": "c" * 100_000, "stop_id": "Centrum"}, "city"),
    ("find_places", {"city": "testowo", "query": "a " * 1001}, "query"),
    ("find_places", {"city": "testowo", "query": "zolw", "kinds": ["stop"] * 40_000}, "kinds"),
    ("plan_trip", {**PLAN, "from": "R" * 201}, "from"),
    ("plan_trip", {**PLAN, "depart_at": "9" * 41}, "depart_at"),
    ("line_info", {"city": "testowo", "line": "8" * 61}, "line"),
    ("line_info", {"city": "testowo", "line": "2", "date": "2026-11-100"}, "date"),
    ("credits", {"ids": ["osm"] * 51}, "ids"),
    ("credits", {"ids": ["x" * 81]}, "ids.0"),
    ("render_map", {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02], "style": {"k" * 100_000: "red"}}, "style"),
])
def test_long_text_and_long_lists_are_refused_in_a_short_line(server, name, args, where):
    text = refused(server, name, args, where)
    assert len(text) < 500, len(text)


@pytest.mark.parametrize("name,args", [
    ("plan_trip", {**PLAN, "to": "Q" * 200}),  # a name of the longest length: not found, quoted cut
    ("departures", {"city": "testowo", "stop_id": "S" * 200}),
    ("line_info", {"city": "testowo", "line": "L" * 60}),
    ("find_places", {"city": "testowo", "query": "z", "zz" + "u" * 100_000: 1}),  # an unknown argument's name
])
def test_messages_quote_at_most_80_characters_of_the_input(server, name, args):
    text = refused(server, name, args)
    longest = max(len(run) for run in text.replace("…", " ").split())
    assert longest <= 82, text  # the cut quote, with its quotation marks


# ---------------------------------------------------------------- finite numbers, coordinates in range

NAN, INF = float("nan"), float("inf")
BOX = [49.999, 18.999, 50.01, 19.02]


@pytest.mark.parametrize("name,args,where", [
    ("find_places", {"city": "testowo", "near": [NAN, NAN]}, "near"),
    ("find_places", {"city": "testowo", "near": "[NaN, NaN]"}, "near"),  # JSON inside a string
    ("find_places", {"city": "testowo", "near": [INF, 19.0]}, "near"),
    ("find_places", {"city": "testowo", "near": [50.0, 19.0], "radius_m": -5}, "radius_m"),
    ("find_places", {"city": "testowo", "near": [95.0, 19.0]}, "latitude"),
    ("walk_route", {"city": "testowo", "from": [NAN, 19.0], "to": at(2, 9)}, "from"),
    ("plan_trip", {**PLAN, "to": [50.0, 200.0]}, "to"),
    ("map_extract", {"city": "testowo", "bbox": [NAN, 18.999, 50.01, 19.02]}, "bbox"),
    ("map_extract", {"city": "testowo", "bbox": [49.999, 18.999, 95.0, 19.02]}, "latitude"),
    ("render_map", {"city": "testowo", "bbox": [49.999, 18.999, 50.01, INF]}, "bbox"),
])
def test_nan_infinity_and_out_of_range_coordinates_are_refused(server, name, args, where):
    text = refused(server, name, args, where)
    assert "cannot convert" not in text and "expected a finite input" not in text


@pytest.mark.parametrize("name,args,where", [
    ("find_places", {"city": "testowo", "near": [NAN, NAN]}, "near"),
    ("find_places", {"city": "testowo", "near": [50.0, 19.0], "radius_m": INF}, "radius_m"),
    ("find_places", {"city": "testowo", "near": [50.0, 19.0], "radius_m": NAN}, "radius_m"),
    ("stops_near", {"city": "testowo", "point": [50.0, 19.0], "max_walk_min": NAN}, "max_walk_min"),
    ("render_map", {"city": "testowo", "bbox": [49.999, 18.999, 50.01, NAN]}, "bbox"),
])
def test_nan_as_the_http_transport_lets_it_in_is_refused(server, name, args, where):
    """Python's JSON reader takes a bare NaN in an HTTP body; the in-memory client above turns it into null, so the
    argument model is checked directly."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as e:
        server._tool_manager.get_tool(name).fn_metadata.validate_arguments(args)
    assert e.value.errors()[0]["loc"][0] == where


def test_a_point_names_its_problem_once(server):
    text = refused(server, "walk_route", {"city": "testowo", "from": [50.0], "to": at(2, 9)}, "from")
    assert "constrained" not in text and "function-after" not in text and "[lat, lon] (2 numbers) or a place name" in text


# ---------------------------------------------------------------- dates and times

@pytest.mark.parametrize("value", ["", "  ", "soon", "9999-12-31", "0001-01-01", "0000-00-00", "2026-13-45",
                                   "2026-02-30", "1999-12-31", "2101-01-01", "99:99", "48:00", "12:60",
                                   "9999-12-31T23:59:59+14:00", "0001-01-01T00:00:00Z", "2026-11-18 24:61"])
def test_a_bad_time_gets_one_message(value):
    from poland_transit_mcp.timeutil import LocalTime, parse_when
    with pytest.raises(ValueError, match="is not a valid time: use HH:MM or YYYY-MM-DD HH:MM"):
        parse_when(value, LocalTime(20261116, 0))


@pytest.mark.parametrize("value", ["", "9999-12-31", "0001-01-01", "0000-00-00", "2026-13-45", "2026-02-29", "today"])
def test_a_bad_date_gets_one_message(value):
    from poland_transit_mcp.timeutil import parse_date
    with pytest.raises(ValueError, match="is not a valid date: use YYYY-MM-DD, years 2000-2100"):
        parse_date(value)


@pytest.mark.parametrize("name,args,words", [
    ("stops_near", {"city": "testowo", "point": [50.0, 19.0], "date": "9999-12-31"}, ["date", "not a valid date"]),
    ("stops_near", {"city": "testowo", "point": [50.0, 19.0], "date": "0000-00-00"}, ["not a valid date"]),
    ("stops_near", {"city": "testowo", "point": [50.0, 19.0], "date": ""}, ["date"]),
    ("departures", {"city": "testowo", "stop_id": "Centrum", "at": ""}, ["at"]),
    ("departures", {"city": "testowo", "stop_id": "Centrum", "at": "9999-12-31 10:00"}, ["not a valid time"]),
    ("line_info", {"city": "testowo", "line": "2", "date": "2026-13-45"}, ["not a valid date"]),
    ("plan_trip", {**PLAN, "depart_at": "99:99"}, ["not a valid time", '"99:99"']),
    ("plan_trip", {**PLAN, "depart_at": "9999-12-31T23:00:00+01:00"}, ["not a valid time"]),
])
def test_tools_refuse_bad_dates_and_times_instead_of_answering_for_now(server, name, args, words):
    text = refused(server, name, args, *words)
    assert "100:39" not in text and "month must be" not in text


# ---------------------------------------------------------------- the server log

def test_the_log_names_the_tool_never_the_callers_text(server, caplog):
    import logging
    secret = "SECRET-HOME-ADDRESS 12"
    with caplog.at_level(logging.INFO, logger="mcp.server.mcpserver.server"):
        refused(server, "plan_trip", {**PLAN, "to": secret}, secret)  # the caller sees its own text
        refused(server, "render_map", {"city": "testowo", "bbox": BOX, "style": {"k" * 1000: "red"}})
    logged = caplog.text
    assert "plan_trip" in logged and "refused a call" in logged
    assert secret not in logged and "k" * 41 not in logged


def test_an_unexpected_error_is_logged_by_kind_and_place(server, caplog, capsys, monkeypatch):
    import logging
    import poland_transit_mcp.server as srv

    def boom(*a, **k):
        raise RuntimeError("SECRET-HOME-ADDRESS 12")
    monkeypatch.setattr(srv, "search_places", boom)
    with caplog.at_level(logging.INFO, logger="mcp.server.mcpserver.server"):
        text, err = call(server, "find_places", {"city": "testowo", "query": "x"})
    stderr = capsys.readouterr().err
    assert err and "could not answer" in text and "SECRET" not in text
    assert "RuntimeError at test_input_contract.py:" in stderr
    assert "SECRET" not in caplog.text + stderr


# ---------------------------------------------------------------- strict types; text stays text

@pytest.mark.parametrize("name,args,where", [
    ("find_places", {"city": "testowo", "query": "zolw", "limit": True}, "limit"),
    ("find_places", {"city": "testowo", "query": "zolw", "limit": "5"}, "limit"),
    ("find_places", {"city": "testowo", "query": "zolw", "limit": 5.5}, "limit"),
    ("find_places", {"city": "testowo", "near": ["50.0", "19.0"]}, "near"),
    ("plan_trip", {**PLAN, "max_transfers": "2"}, "max_transfers"),
    ("departures", {"city": "testowo", "stop_id": 5}, "stop_id"),
    ("render_map", {"city": "testowo", "bbox": BOX, "size": "500"}, "size"),
])
def test_strict_types(server, name, args, where):
    refused(server, name, args, where)


def test_strict_types_inside_a_station_list(server):
    from pydantic import ValidationError
    meta = server._tool_manager.get_tool("plan_stations").fn_metadata
    with pytest.raises(ValidationError):
        meta.validate_arguments({"from": [{"stop": "Alfa", "minutes": "5"}], "to": [{"stop": "Beta"}]})
    meta.validate_arguments({"from": [{"stop": "Alfa", "minutes": 5}], "to": '[{"stop": "Beta"}]'})  # list as JSON


@pytest.mark.parametrize("name,args", [
    ("find_places", {"city": "testowo", "query": "zolw", "limit": 5.0}),
    ("plan_trip", {**PLAN, "max_transfers": 1.0}),
    ("plan_stations", {"from": [{"stop": "A", "minutes": 5.0}], "to": [{"stop": "B"}]}),
])
def test_a_whole_number_float_is_an_integer(server, name, args):
    """JSON Schema's "integer" accepts 10.0: a strict client may send it, so the server must take it too."""
    meta = server._tool_manager.get_tool(name).fn_metadata
    got = meta.validate_arguments(args)
    whole = [v for v in [got.get("limit"), got.get("max_transfers"), *[s.minutes for s in got.get("from_") or []]]
             if v is not None]
    assert whole and all(type(v) is int for v in whole), got


def test_an_integer_is_a_number(server):
    _, err = call(server, "find_places", {"city": "testowo", "near": [50, 19], "radius_m": 500})
    assert not err


@pytest.mark.parametrize("query", ["[1,2]", "null", '{"a": 1}', "12"])
def test_text_that_looks_like_json_stays_text(server, query):
    import json
    text, err = call(server, "find_places", {"city": "testowo", "query": query})
    assert not err, text
    assert json.loads(text)["note"] == "nothing found in this city's data"


def test_lists_and_objects_sent_as_json_text_still_work(server):
    _, err = call(server, "map_extract", {"city": "testowo", "bbox": "[49.999, 18.999, 50.01, 19.02]", "layers": '["water"]'})
    assert not err
    text, err = call(server, "render_map", {"city": "testowo", "bbox": BOX, "style": '{"water": "#9cc3e6"}', "output": "svg"})
    assert not err, text


# ---------------------------------------------------------------- errors that never reach the caller as they are

def test_a_missing_data_file_is_named_by_city_not_by_path(city_dir, tmp_path, capsys):
    import shutil
    from poland_transit_mcp.city import City
    shutil.copy(city_dir / "testowo.sqlite", tmp_path / "testowo.sqlite")
    srv = create_server({"testowo": City.open(tmp_path, "testowo")})
    (tmp_path / "testowo.sqlite").unlink()
    text, err = call(srv, "stops_near", {"city": "testowo", "point": [50.0, 19.0]})
    assert err and str(tmp_path) not in text and "No such file" not in text, text
    assert text.endswith("the data of testowo is not available"), text


@pytest.mark.parametrize("error", [OverflowError("date value out of range 9999"), TypeError("'NoneType' object"),
                                   ZeroDivisionError("float division by zero"), RuntimeError("SECRET state"),
                                   IndexError("list index out of range"), ValueError("invalid literal: 'SECRET'"),
                                   KeyError("SECRET")])
def test_leftover_crash_classes_get_a_neutral_line(server, monkeypatch, capsys, error):
    import poland_transit_mcp.server as srv

    def boom(*a, **k):
        raise error
    monkeypatch.setattr(srv, "search_places", boom)
    text = refused(server, "find_places", {"city": "testowo", "query": "x"}, "could not answer this request")
    assert str(error) not in text
    assert type(error).__name__ in capsys.readouterr().err


def test_a_thin_box_under_the_limit_is_drawn(server):
    """The map widens a box thinner than 4:1 before drawing; the size limit is checked on the box asked for."""
    from poland_transit_mcp.geo import haversine
    thin = [50.0, 18.877, 50.0001, 19.126]
    assert 17_500 < haversine(*thin) < 18_000
    text, err = call(server, "render_map", {"city": "testowo", "bbox": thin, "output": "svg"})
    assert not err, text
    wide = [50.0, 18.87, 50.0001, 19.134]
    refused(server, "render_map", {"city": "testowo", "bbox": wide}, "bbox too large (18.")


@pytest.mark.parametrize("text", ["[50.0018, 19.0014]", "[50.0018,19.0014]", " [ 50.0018 , 19.0014 ] ", "[50, 19]"])
def test_a_point_sent_as_text_of_two_plain_numbers_is_a_point(server, text):
    """Some clients send lists as JSON text: two plain numbers in brackets are read as [lat, lon]; nothing else is
    (no JSON parsing of text: "[NaN, NaN]", "[1e2, 3]" or "[1, 2, 3]" stay place names)."""
    _, err = call(server, "walk_route", {"city": "testowo", "from": text, "to": at(2, 9)})
    assert not err


@pytest.mark.parametrize("text", ["[NaN, NaN]", "[1e2, 19]", "[50, 19, 3]", "[50.0018, 19.0014]x", "[Infinity, 1]"])
def test_other_bracketed_text_stays_a_place_name(server, text):
    refused(server, "walk_route", {"city": "testowo", "from": text, "to": at(2, 9)}, "no place named")


@pytest.mark.parametrize("value,words", [
    ([50.0], ["from:", "2 numbers"]),
    ([95.0, 19.0], ["from:", "latitude 95"]),
    (["a", "b"], ["from:", "number"]),
    (5, ["from:", "[lat, lon] or a place name"]),
    ("", ["from:", "place name"]),
])
def test_a_bad_point_gets_one_message(server, value, words):
    text = refused(server, "walk_route", {"city": "testowo", "from": value, "to": at(2, 9)}, *words)
    assert text.count("from:") == 1 and "valid string" not in text and "List should" not in text, text


def test_a_hint_follows_the_error_as_its_own_sentence(server):
    text = refused(server, "plan_stations", {"from": [{"stop": 5}], "to": "x"}, "Each stop is an object")
    assert "list. Each stop" in text, text


def test_a_deliberate_refusal_keeps_its_message(server, monkeypatch):
    import poland_transit_mcp.server as srv
    from poland_transit_mcp.errors import Refused

    def no(*a, **k):
        raise Refused("nothing to see near the river")
    monkeypatch.setattr(srv, "search_places", no)
    text = refused(server, "find_places", {"city": "testowo", "query": "x"})
    assert text.endswith("nothing to see near the river"), text


def test_an_unknown_tool_name_is_cut_in_the_answer_and_the_log(server, caplog):
    import logging
    name = "HOME-ADDRESS-" + "x" * 100_000
    with caplog.at_level(logging.INFO, logger="mcp.server.mcpserver.server"):
        text, err = call(server, name, {})
    assert err and len(text) < 400 and "HOME-ADDRESS-" in text and "list_cities" in text, text[:500]
    assert "x" * 41 not in caplog.text and "HOME-ADDRESS" not in caplog.text, caplog.text[:300]


def test_a_refused_call_logs_at_most_five_argument_places(server, caplog):
    import logging
    stops = [{"stop": 5, "minutes": "x"}] * 20  # 40 places wrong
    with caplog.at_level(logging.INFO, logger="mcp.server.mcpserver.server"):
        _, err = call(server, "plan_stations", {"from": stops, "to": stops})
    assert err
    line = next(r.getMessage() for r in caplog.records if "rejected arguments" in r.getMessage())
    assert len(line) < 500 and "more" in line, line[:600]


@pytest.mark.parametrize("name,args,words", [
    ("render_map", {"city": "testowo", "bbox": [*at(0, 0), *at(4, 4)], "style": {"water": "red\n"}}, ["colour"]),
    ("render_map", {"city": "testowo", "trip": "t2?c=testowo%0A&f=50.0,19.0&t=50.01,19.01&d=2026-11-10T08:40:00&x=2&i=0"
                                               "&h=0123456789ab"}, ["not a trip reference"]),
    ("render_map", {"city": "testowo", "trip": "t2?c=testowo&f=50.0,19.0&t=50.01,19.01&d=2026-11-10T08:40:00%0A&x=2&i=0"
                                               "&h=0123456789ab"}, ["not a trip reference"]),
])
def test_a_final_newline_does_not_pass_a_pattern(server, name, args, words):
    refused(server, name, args, *words)


def test_an_unknown_resource_or_prompt_never_reaches_the_log(server, caplog, capsys):
    import logging

    from mcp.shared.exceptions import MCPError
    secret = "SECRET-HOME-ADDRESS-12"

    async def go():
        async with Client(server) as c:
            for ask in (lambda: c.read_resource(f"credits://{secret}"), lambda: c.read_resource(f"file:///{secret}"),
                        lambda: c.get_prompt(secret)):
                with pytest.raises(MCPError):
                    await ask()
    with caplog.at_level(logging.DEBUG):
        anyio.run(go)
    logged = caplog.text + capsys.readouterr().err
    assert "resource read was refused" in logged and "ValueError at" in logged, logged[:600]  # still logged, by kind
    assert secret not in logged and "Traceback" not in logged, logged[:600]
