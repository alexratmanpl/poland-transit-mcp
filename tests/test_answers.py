"""Declared answer shapes (answers.py): every tool publishes an outputSchema and returns structuredContent that a
standard JSON Schema validator accepts, with the same JSON as text; the tool list stays small enough to load."""
import json

import anyio
import pytest
from fixture import at, build_national_fixture, near
from jsonschema import Draft202012Validator
from mcp import Client

from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server

START, END = near("alfa", 2, 1), near("beta", 18, 12)
CALLS = [
    ("list_cities", {}),
    ("find_places", {"city": "testowo", "query": "muzeum zolwia"}),
    ("find_places", {"city": "poland", "query": "alfa"}),
    ("stops_near", {"city": "testowo", "point": at(2, 1), "date": "2026-11-10"}),
    ("departures", {"city": "testowo", "stop_id": "T:A", "at": "2026-11-10 08:00", "limit": 2}),
    ("departures", {"city": "poland", "stop_id": "Alfa Główna", "at": "2026-11-10 08:00"}),
    ("line_info", {"city": "testowo", "line": "2", "date": "2026-11-10"}),
    ("line_info", {"city": "poland", "line": "101", "date": "2026-11-10"}),
    ("walk_route", {"city": "testowo", "from": at(0, 0), "to": at(2, 2)}),
    ("plan_trip", {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-11-10 08:00"}),
    ("plan_trip", {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-12-25 08:00"}),  # outside the data
    ("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END, "depart_at": "2026-11-10 08:40"}),
    ("plan_trip", {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "arrive_by": "2026-11-10 09:00"}),
    ("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END, "arrive_by": "2026-11-10 16:00"}),
    ("plan_stations", {"from": [{"stop": "Alfa Główna", "minutes": 10}], "to": [{"stop": "Beta Główna"}], "depart_at": "2026-11-10 08:00"}),
    ("plan_stations", {"from": [{"stop": "Alfa Główna"}], "to": [{"stop": "Beta Główna"}], "depart_at": "2026-11-14 08:00"}),  # no train
    ("map_extract", {"city": "testowo", "bbox": [*at(0, 0), *at(4, 4)]}),
    ("render_map", {"city": "testowo", "bbox": [*at(0, 0), *at(4, 4)]}),
    ("credits", {}),
    ("credits", {"ids": ["osm", "nowhere/X"]}),
]
# the same calls with everything (detail="full"): both forms match the published schema
CALLS += [(n, {**a, "detail": "full"}) for n, a in CALLS if n in ("departures", "line_info", "walk_route", "plan_trip", "plan_stations")]


@pytest.fixture(scope="module")
def server(city, tmp_path_factory):
    alfa, beta, nat = build_national_fixture(tmp_path_factory.mktemp("answers"))
    return create_server(CityPool({"testowo": city, "alfa": alfa, "beta": beta, "poland": nat}, 1500))


def run(server, fn):
    async def go():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(go)


@pytest.fixture(scope="module")
def schemas(server):
    tools = run(server, lambda c: c.list_tools()).tools
    return {t.name: t.output_schema for t in tools}


def test_every_tool_publishes_an_output_schema(schemas):
    assert set(schemas) == {name for name, _ in CALLS}
    for name, schema in schemas.items():
        assert schema and schema.get("type") == "object", name
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("name,args", CALLS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CALLS)])
def test_answers_match_their_published_schema_and_the_text_is_the_same_json(server, schemas, name, args):
    r = run(server, lambda c: c.call_tool(name, args))
    text = next(b.text for b in r.content if b.type == "text")
    assert not r.is_error, text
    errors = sorted(Draft202012Validator(schemas[name]).iter_errors(r.structured_content), key=str)
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors[:5]]
    assert json.loads(text) == r.structured_content
    assert text == json.dumps(r.structured_content, ensure_ascii=False, separators=(",", ":"))


def test_the_tool_list_stays_small(server):
    """Agents load every definition before the first call: at most about 6,000 tokens (24,000 characters)."""
    tools = run(server, lambda c: c.list_tools()).tools
    size = len(json.dumps([t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]))
    assert size < 28_700, size  # 27,870 measured, +3%; bounds on every argument, lean fields described


def test_an_error_keeps_its_own_quotes(server):
    r = run(server, lambda c: c.call_tool("departures", {"city": "poland", "stop_id": "Alfa"}))
    assert r.is_error and '"Alfa" matches several stops' in r.content[0].text


def test_an_answer_that_does_not_match_its_shape_is_logged(server, monkeypatch, capsys):
    """The SDK refuses it with a bare "Error executing tool"; the server says where it differs (never the values)."""
    import poland_transit_mcp.server as srv
    monkeypatch.setattr(srv, "_find_places", lambda *a, **k: [{"name": "Wawel", "kind": "museum"}])  # no point
    r = run(server, lambda c: c.call_tool("find_places", {"city": "testowo", "query": "x"}))
    assert r.is_error
    err = capsys.readouterr().err
    assert "find_places: answer does not match its declared shape" in err and "places.0.at" in err
    assert "Wawel" not in err


def test_the_fields_callers_chain_on_are_declared(schemas):
    """A stop's ids feed departures; a departure's operator, status and track are what people ask about."""
    def props(schema, *path):
        node = schema
        for key in path:
            node = node["properties"][key] if key != "[]" else node["items"]
            node = schemas_defs(schema, node)
        return set(node["properties"])

    def schemas_defs(root, node):
        return root["$defs"][node["$ref"].rsplit("/", 1)[-1]] if "$ref" in node else node

    assert "stop_ids" in props(schemas["find_places"], "places", "[]")
    assert {"operator", "status", "track", "platform", "train"} <= props(schemas["departures"], "departures", "[]")
    assert {"from", "to"} <= set(schemas["walk_route"]["properties"])


@pytest.mark.parametrize("args", [
    {"city": "poland", "from": START, "to": END},  # the national layer has no map
    {"city": "alfa", "from": START, "to_city": "krakow", "to": END},  # a city that is not loaded
])
def test_errors_point_to_plan_stations(server, args):
    r = run(server, lambda c: c.call_tool("plan_trip", args))
    assert r.is_error and "plan_stations" in r.content[0].text


def test_a_map_without_fonts_says_so(server, monkeypatch):
    import poland_transit_mcp.server as srv
    monkeypatch.setattr(srv, "picture_warning", lambda: "no fonts found on this machine: maps are drawn without labels")
    r = run(server, lambda c: c.call_tool("render_map", {"city": "testowo", "bbox": [*at(0, 0), *at(4, 4)]}))
    assert not r.is_error and "without labels" in r.structured_content["note"]


def test_a_trip_between_cities_is_drawn_from_its_start(server):
    plan = run(server, lambda c: c.call_tool("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                                           "depart_at": "2026-11-10 08:40", "detail": "full"}))
    it = plan.structured_content["itineraries"][0]
    r = run(server, lambda c: c.call_tool("render_map", {"city": "alfa", "itinerary": it, "output": "svg"}))
    assert not r.is_error, r.content[0].text


def test_a_trip_between_cities_has_a_reference_that_draws_its_start(server):
    plan = run(server, lambda c: c.call_tool("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                                           "depart_at": "2026-11-10 08:40"})).structured_content
    ref = plan["itineraries"][0]["trip"]
    assert ref.startswith("t2?c=alfa&") and "&tc=beta&" in ref
    r = run(server, lambda c: c.call_tool("render_map", {"city": "alfa", "trip": ref, "output": "svg"}))
    assert not r.is_error, r.content[0].text
    r = run(server, lambda c: c.call_tool("render_map", {"city": "beta", "trip": ref}))
    assert r.is_error and 'starts in "alfa"' in r.content[0].text


def test_a_map_of_a_trip_between_cities_credits_the_national_layer(server):
    """The drawn trip rides the national layer's trains: its credits name them, as plan_trip's do."""
    plan = run(server, lambda c: c.call_tool("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                                           "depart_at": "2026-11-10 08:40"})).structured_content
    ref = plan["itineraries"][0]["trip"]
    r = run(server, lambda c: c.call_tool("render_map", {"city": "alfa", "trip": ref, "output": "svg"}))
    assert not r.is_error, r.content[0].text
    assert set(plan["credits"]) <= set(r.structured_content["credits"]), r.structured_content["credits"]


def test_a_reference_from_a_city_to_itself_is_refused_before_planning(server, monkeypatch):
    """plan_trip never writes tc equal to c (to_city in the same city plans within it): such a reference is forged."""
    import poland_transit_mcp.server as srv
    called = []
    monkeypatch.setattr(srv, "plan_intercity", lambda *a, **k: called.append(a) or {"itineraries": []})
    ref = "t2?c=alfa&f=50.0,19.0&t=50.01,19.01&tc=alfa&d=2026-11-10T08:40:00&x=2&i=0&h=0123456789ab"
    r = run(server, lambda c: c.call_tool("render_map", {"city": "alfa", "trip": ref}))
    assert r.is_error and "not a trip reference" in r.content[0].text and not called
