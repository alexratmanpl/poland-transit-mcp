"""What a model reads of this server: the instructions and each tool's name, description and input schema. Tools
refuse arguments they do not declare (an ignored argument gave an answer to another question)."""
import json

import anyio
import pytest
from fixture import at
from mcp import Client

from poland_transit_mcp.server import create_server


@pytest.fixture(scope="module")
def server(city):
    return create_server({"testowo": city})


def run(server, fn):
    async def go():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(go)


def text(r):
    return next(b.text for b in r.content if b.type == "text")


@pytest.fixture(scope="module")
def tools(server):
    return run(server, lambda c: c.list_tools()).tools


def test_an_unknown_argument_is_refused_by_name_in_one_line(server):
    r = run(server, lambda c: c.call_tool("departures", {"city": "testowo", "stop_id": "T:A", "date": "2026-11-10"}))
    assert r.is_error and text(r) == ('Error executing tool departures: unknown argument "date"; departures takes '
                                      "city, stop_id, at, line, mode, to, limit, detail")


def test_a_wrong_value_is_one_line_naming_the_argument(server):
    r = run(server, lambda c: c.call_tool("departures", {"city": "testowo", "stop_id": "T:A", "limit": 500}))
    assert r.is_error and text(r).startswith("Error executing tool departures: limit: ")
    assert "pydantic" not in text(r) and "input_value" not in text(r) and "\n" not in text(r)


def test_plan_stations_shows_the_shape_of_a_stop(server):
    r = run(server, lambda c: c.call_tool("plan_stations", {"from": "Kraków Główny", "to": [{"stop": "X"}]}))
    assert r.is_error and '[{"stop": "Kraków Główny", "minutes": 10}]' in text(r)


def test_every_tool_has_a_title(tools):
    assert all(t.title for t in tools)


def test_from_is_the_only_spelling(server):
    args = {"city": "testowo", "to": at(2, 9)}
    ok = run(server, lambda c: c.call_tool("walk_route", {**args, "from": at(2, 6)}))
    assert not ok.is_error, text(ok)
    bad = run(server, lambda c: c.call_tool("walk_route", {**args, "from_": at(2, 6)}))
    assert bad.is_error and "from_" in text(bad)


def test_null_means_not_given_for_every_argument(server):
    """Clients with strict function schemas send every argument, null for those they leave out."""
    r = run(server, lambda c: c.call_tool("stops_near", {"city": "testowo", "point": at(2, 1), "date": None,
                                                         "max_walk_min": None}))
    assert not r.is_error, text(r)
    r = run(server, lambda c: c.call_tool("departures", {"city": "testowo", "stop_id": "T:A", "at": "2026-11-10 08:00",
                                                         "line": None, "limit": None}))
    assert not r.is_error, text(r)


def _walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            if k == "properties":
                for name, p in v.items():
                    yield from _walk(p, f"{path}.{name}")
            else:
                yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_input_schemas_carry_only_what_a_model_needs(tools):
    for t in tools:
        schema = t.input_schema
        assert schema.get("additionalProperties") is False, t.name
        for where, node in _walk(schema):
            assert "title" not in node and "$defs" not in node and "$ref" not in node, (t.name, where)
            assert {"type": "null"} not in node.get("anyOf", []), (t.name, where)
            assert node.get("default", ...) is not None, (t.name, where)


def test_what_a_model_loads_stays_small(server, tools):
    """Claude Code loads a tool's name, description and input schema (never its outputSchema), and cuts the
    instructions and each description at 4,096 characters."""
    loaded = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
    size = len(json.dumps(loaded, ensure_ascii=False))
    assert size < 14_400, size  # 13,994 measured, +3%: about 6,000 of Claude's tokens for every tool, bounds included
    for t in tools:
        assert len(t.description) < 4096, t.name
    assert len(server.instructions) < 4096


def test_descriptions_carry_no_source_indentation(tools):
    """Python 3.11 and 3.12 keep a docstring's indentation; the server strips it so every version publishes the same
    tools/list."""
    for t in tools:
        assert not any(line.startswith(" ") for line in t.description.splitlines()), t.name
