"""The answer rule, the same on every tool: the default answer holds what a person asking needs and every id another
tool takes; `detail="full"` adds fields and removes none; `limit` gives more rows; an answer that was cut says so in
`note`, with how to get the rest; every argument says what it does. And the text that states all this stays small.
"""
import json

import pytest
from contract import DAY, VALID, contract_server, published, session, walk  # noqa: F401
from fixture import near

import poland_transit_mcp.transit as transit

# Every call of a tool with `detail` that answers on the synthetic data: a city and the national layer where both
# give a different answer (a city line and a train, a trip in one city and between two).
DETAIL_CALLS = [
    ("departures", VALID["departures"]),
    ("departures", {"city": "poland", "stop_id": "Alfa Główna", "at": f"{DAY} 08:00"}),
    ("line_info", VALID["line_info"]),
    ("line_info", {"city": "poland", "line": "101", "date": DAY}),
    ("walk_route", VALID["walk_route"]),
    ("plan_trip", VALID["plan_trip"]),
    ("plan_trip", {"city": "alfa", "from": near("alfa", 2, 1), "to_city": "beta", "to": near("beta", 18, 12),
                   "depart_at": f"{DAY} 08:40"}),
    ("plan_stations", VALID["plan_stations"]),
]
IDS = [f"{name}:{args.get('city', '')}" for name, args in DETAIL_CALLS]

# Keys whose values another tool takes as input, and where. A default answer must keep every such value that its
# full answer has, or one that names the same thing for that tool: a stop's name, where it is in the default
# (departures takes a name for all of a stop's platforms: a line's default stops are names), and a city id the
# server's instructions name (a leg's end on the national layer: its id is in every session's instructions).
CHAINED = {
    "stop_id": [("departures", "stop_id"), ("departures", "to"), ("plan_stations", "from[].stop")],
    "stop_ids": [("departures", "stop_id")],
    "line": [("line_info", "line"), ("departures", "line")],
    "train": [("line_info", "line")],
    "city": [("departures", "city"), ("plan_trip", "to_city")],
    "trip": [("render_map", "trip")],
    "credits": [("credits", "ids")],
}

# tools/list as 0.3.0 published it on the contract server: what a model loads (instructions, and each tool's name,
# description and input schema) and the whole list with output schemas. The rule may cost a little text: the
# budget below. Measured with this change: 1,542 + 13,994 = 15,536 loaded, 28,277 in all.
BEFORE_LOADED, BEFORE_LIST = 1_300 + 14_099, 28_382
BUDGET = 300  # characters, about 150 of Claude's tokens


def answer(server, name, args):
    with session(server) as (call, _, _):
        r, _ = call(name, args)
    assert not r.is_error, next(b.text for b in r.content if b.type == "text")
    return r.structured_content


def paths(o, prefix=""):
    """Every key path of an answer, list positions collapsed to [] (a value that is text at one level and an object
    at the other, such as a line's stops or `as_of`, counts as its own path only)."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield f"{prefix}.{k}"
            yield from paths(v, f"{prefix}.{k}")
    elif isinstance(o, list):
        for v in o:
            yield from paths(v, f"{prefix}[]")


def values(o, key):
    """Every value under `key` anywhere in an answer, lists flattened."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield from (map(str, v) if isinstance(v, list) else [str(v)])
            yield from values(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from values(v, key)


def strings(o):
    """Every text value of an answer."""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def stop_names(o):
    """(stop_id, name) of every object in an answer that has both."""
    if isinstance(o, dict):
        if isinstance(o.get("stop_id"), str) and isinstance(o.get("name"), str):
            yield o["stop_id"], o["name"]
        for v in o.values():
            yield from stop_names(v)
    elif isinstance(o, list):
        for v in o:
            yield from stop_names(v)


@pytest.mark.parametrize("name,args", DETAIL_CALLS, ids=IDS)
def test_full_holds_every_key_the_default_holds(contract_server, name, args):
    default = answer(contract_server, name, args)
    full = answer(contract_server, name, {**args, "detail": "full"})
    missing = set(paths(default)) - set(paths(full))
    assert not missing, missing
    assert len(json.dumps(full, ensure_ascii=False)) >= len(json.dumps(default, ensure_ascii=False))


@pytest.mark.parametrize("name,args", DETAIL_CALLS, ids=IDS)
def test_the_default_keeps_every_id_another_tool_takes(contract_server, name, args):
    """What `full` adds (paths, trip ids, colours, leg-end points) is never a value another tool takes."""
    default = answer(contract_server, name, args)
    full = answer(contract_server, name, {**args, "detail": "full"})
    named = set(strings(default))
    for key in CHAINED:
        dropped = set(values(full, key)) - set(values(default, key))
        if key == "stop_id":
            dropped -= {sid for sid, stop in stop_names(full) if stop in named}
        if key == "city":
            dropped = {c for c in dropped if f" {c}" not in contract_server.instructions}
        assert not dropped, (key, dropped)


def test_the_chained_inputs_exist(published):
    """CHAINED names real arguments, so the test above checks what callers chain on."""
    for key, targets in CHAINED.items():
        for tool, where in targets:
            assert where in {w for w, _ in walk(published[tool].input_schema)}, (key, tool, where)


def test_every_tool_with_detail_says_what_full_adds_and_how_much_larger(published):
    texts = {name: t.input_schema["properties"]["detail"]["description"] for name, t in published.items()
             if "detail" in t.input_schema["properties"]}
    assert set(texts) == {name for name, _ in DETAIL_CALLS}
    assert len(set(texts.values())) == len(texts), "each tool's own text, not one shared sentence"
    for name, text in texts.items():
        assert text.startswith("full adds ") and "times larger" in text, (name, text)


def test_every_argument_has_a_description(published):
    missing = [f"{name}.{where}" for name, t in published.items() for where, node in walk(t.input_schema)
               if where and "[]" not in where.rsplit(".", 1)[-1] and "|" not in where and "{" not in where
               and not node.get("description")]
    assert not missing, missing


NEAR = {"city": "testowo", "near": [50.0018, 19.0014], "radius_m": 5000}  # every place of the test city


def test_a_cut_list_of_places_says_so_and_how_to_get_the_rest(contract_server):
    few = answer(contract_server, "find_places", {**NEAR, "limit": 1})
    assert len(few["places"]) == 1 and "places match" in few["note"]
    assert "kinds" in few["note"] and "raise limit" in few["note"]
    every = answer(contract_server, "find_places", {**NEAR, "limit": 50})
    assert len(every["places"]) > 1 and "places match" not in every.get("note", "")
    stations = answer(contract_server, "find_places", {"city": "poland", "query": "Alfa", "limit": 1})
    assert len(stations["places"]) == 1 and stations["note"].startswith("3 places match")


def test_a_list_of_places_at_its_largest_does_not_suggest_a_larger_limit(contract_server, monkeypatch):
    import poland_transit_mcp.server as srv
    monkeypatch.setattr(srv, "MAX_PLACES", 1)
    few = answer(contract_server, "find_places", {**NEAR, "limit": 1})
    assert "places match" in few["note"] and "limit" not in few["note"]


def test_a_cut_list_of_stops_says_so_and_how_to_get_the_rest(contract_server, monkeypatch):
    args = VALID["stops_near"]
    whole = answer(contract_server, "stops_near", args)
    assert len(whole["stops"]) >= 2 and "platforms within" not in whole.get("note", "")
    monkeypatch.setattr(transit, "MAX_PLATFORMS", 1)
    cut = answer(contract_server, "stops_near", args)
    assert len(cut["stops"]) == 1
    assert cut["note"].startswith(f"the nearest 1 of {len(whole['stops'])} platforms") and "find_places" in cut["note"]


def test_a_cut_line_says_so(contract_server, monkeypatch):
    import poland_transit_mcp.server as srv
    monkeypatch.setattr(srv, "MAX_DIRECTIONS", 0)
    cut = answer(contract_server, "line_info", VALID["line_info"])
    assert "left out" in cut["note"]


def test_departures_narrow_by_mode(contract_server):
    board = {"city": "poland", "stop_id": "Alfa Główna", "at": f"{DAY} 08:00"}
    rail = answer(contract_server, "departures", {**board, "mode": "rail"})
    assert rail["departures"] and all(d["mode"] == "rail" for d in rail["departures"])
    tram = answer(contract_server, "departures", {**board, "mode": "tram"})
    assert not tram["departures"] and tram["note"].startswith("no tram departures")
    city = answer(contract_server, "departures", {**VALID["departures"], "mode": "bus", "limit": 50})
    assert all(d["mode"] == "bus" for d in city["departures"])


def test_the_rule_costs_little_text(contract_server, published):
    """What a model loads grows by at most BUDGET characters over 0.3.0, and the whole tools/list does not grow."""
    loaded = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
              for t in published.values()]
    size = len(contract_server.instructions) + len(json.dumps(loaded, ensure_ascii=False))
    assert size <= BEFORE_LOADED + BUDGET, size
    whole = len(json.dumps([t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in published.values()],
                           ensure_ascii=False))
    assert whole <= BEFORE_LIST, whole


def test_the_instructions_state_the_rule(contract_server):
    text = contract_server.instructions
    for words in ('detail="full"', "`limit`", "filters", "`note`", "times larger"):
        assert words in text, words
