"""Shared parts of the contract tests (test_contract_*.py): the published tools/list is the contract, and these tests
are generated from it, so a new tool or argument is covered without new test code. What each tool needs that a
schema cannot say (one call that answers on the synthetic cities) is the table VALID below."""
import json
import math
from contextlib import contextmanager
from typing import Any

import anyio.from_thread
import jsonschema
import pytest
from fixture import at, build_national_fixture
from mcp import Client

from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server

DAY = "2026-11-10"  # a Tuesday inside every fixture timetable
CITIES = ("testowo", "alfa", "beta", "poland")  # the ids the contract server serves (see `contract_server`)

# One call per tool that answers (not an error) on the synthetic cities. A new tool fails
# test_every_tool_has_a_valid_call until it has a row here.
VALID: dict[str, dict[str, Any]] = {
    "list_cities": {},
    "find_places": {"city": "testowo", "query": "Centrum"},
    "stops_near": {"city": "testowo", "point": at(2, 1), "date": DAY},
    "departures": {"city": "testowo", "stop_id": "Centrum", "at": f"{DAY} 07:58"},
    "line_info": {"city": "testowo", "line": "1", "date": DAY},
    "walk_route": {"city": "testowo", "from": at(2, 1), "to": at(2, 5)},
    "plan_trip": {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": f"{DAY} 07:58"},
    "plan_stations": {"from": [{"stop": "Alfa Główna"}], "to": [{"stop": "Beta Główna"}], "depart_at": f"{DAY} 08:00"},
    "map_extract": {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02]},
    "render_map": {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02], "output": "svg"},
    "credits": {"ids": ["osm"]},
}

# Where the server is deliberately looser than its published schema. Each entry: (tool or "*", argument or "*",
# mutation kind) -> reason. Everything not listed must hold the rule "the server never answers what the schema
# refuses".
LOOSER: dict[tuple[str, str, str], str] = {
    ("*", "*", "null-optional"): "null means 'not given' for every optional argument: clients with strict function "
                                 "schemas must send every argument and send null for the ones they leave out",
}

# Keywords a published input schema may use: JSON Schema (draft 2020-12) validation and annotation keywords. pydantic
# writes its own names ("ge", "le") when a constraint follows a validator; a client ignores those, so the bound would
# be invisible.
KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "anyOf", "enum", "default",
            "description", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
            "minItems", "maxItems", "maxProperties", "propertyNames"}


def walk(node: Any, path: str = ""):
    """Every schema node under `node`, with a readable path: `itinerary.legs[].from.at[]`."""
    if not isinstance(node, dict):
        return
    yield path, node
    for name, sub in node.get("properties", {}).items():
        yield from walk(sub, f"{path}.{name}" if path else name)
    if isinstance(node.get("items"), dict):
        yield from walk(node["items"], f"{path}[]")
    if isinstance(node.get("additionalProperties"), dict):
        yield from walk(node["additionalProperties"], f"{path}{{}}")
    if isinstance(node.get("propertyNames"), dict):
        yield from walk(node["propertyNames"], f"{path}{{key}}")
    for i, option in enumerate(node.get("anyOf", [])):
        yield from walk(option, f"{path}|{i}")


def schema_accepts(schema: dict, args: dict) -> bool:
    """The published schema's verdict. JSON has no NaN or Infinity: a value carrying either is refused (jsonschema
    itself compares NaN with bounds and lets it through)."""
    if _non_finite(args):
        return False
    return jsonschema.Draft202012Validator(schema).is_valid(args)


def _non_finite(v: Any) -> bool:
    if isinstance(v, float):
        return not math.isfinite(v)
    if isinstance(v, dict):
        return any(_non_finite(x) for x in v.values())
    if isinstance(v, list):
        return any(_non_finite(x) for x in v)
    return False


def text_of(result) -> str:
    return next((b.text for b in result.content if b.type == "text"), "")


def readable(name: str, text: str) -> bool:
    """A refusal a model can act on: not the SDK's bare crash line, no traceback, short."""
    return (text.strip() != f"Error executing tool {name}" and "Traceback" not in text and len(text) < 1000
            and text.startswith(f"Error executing tool {name}: "))


@contextmanager
def session(server):
    """One client session kept open across many synchronous calls (hypothesis and parametrised tests call one at a
    time). Yields `call(name, args) -> (result, seconds)`."""
    import time
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:
            def call(name: str, args: dict):
                t = time.perf_counter()
                r = portal.call(client.call_tool, name, args)
                return r, time.perf_counter() - t
            yield call, portal, client


@pytest.fixture(scope="session")
def contract_server(city, tmp_path_factory):
    """The server as a caller meets it: a city (testowo) and a national layer with two cities (alfa, beta, poland),
    so every tool has something to answer."""
    alfa, beta, nat = build_national_fixture(tmp_path_factory.mktemp("contract-national"))
    return create_server(CityPool({"testowo": city, "alfa": alfa, "beta": beta, "poland": nat}, 1000))


@pytest.fixture(scope="session")
def published(contract_server):
    """tools/list as a client receives it: {name: Tool}."""
    with session(contract_server) as (_, portal, client):
        return {t.name: t for t in portal.call(client.list_tools).tools}


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, indent=1)
