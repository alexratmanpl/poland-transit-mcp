import json
import sys

import anyio
import pytest
from fixture import at
from mcp import Client, StdioServerParameters

from poland_transit_mcp.server import create_server

TOOLS = {"credits", "departures", "find_places", "line_info", "list_cities", "map_extract", "plan_stations", "plan_trip", "render_map", "stops_near", "walk_route"}


@pytest.fixture(scope="module")
def server(city):
    return create_server({"testowo": city})


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args)
            return next(b.text for b in r.content if b.type == "text"), r.is_error
    text, err = anyio.run(go)
    return (text if err else json.loads(text)), err


def test_lists_the_tools(server):
    async def go():
        async with Client(server) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert anyio.run(go) == TOOLS


@pytest.mark.parametrize("name,args", [
    ("list_cities", {}),
    ("find_places", {"city": "testowo", "query": "muzeum zolwia"}),
    ("stops_near", {"city": "testowo", "point": at(2, 1), "date": "2026-11-10"}),
    ("departures", {"city": "testowo", "stop_id": "T:A", "at": "2026-11-10 08:00", "limit": 2}),
    ("line_info", {"city": "testowo", "line": "2", "date": "2026-11-10"}),
    ("walk_route", {"city": "testowo", "from": at(2, 6), "to": at(2, 9)}),
    ("plan_trip", {"city": "testowo", "from": at(2, 1), "to": "Ełcka", "depart_at": "2026-11-10 07:58"}),
    ("map_extract", {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02]}),
])
def test_every_answer_has_credits_and_as_of(server, name, args):
    body, err = call(server, name, args)
    assert not err, body
    assert isinstance(body["credits"], list) and isinstance(body["as_of"], dict)
    assert all(isinstance(i, str) and len(i) < 40 for i in body["credits"])  # ids, never the full text


def test_find_places_without_diacritics(server):
    body, _ = call(server, "find_places", {"city": "testowo", "query": "aleja lakowa"})
    assert (body["places"][0]["name"], body["places"][0]["kind"]) == ("Aleja Łąkowa", "stop")


def test_departures_labelled_scheduled(server):
    body, _ = call(server, "departures", {"city": "testowo", "stop_id": "Centrum", "at": "2026-11-10 08:14", "limit": 2})
    assert body["departures"][0]["status"] == "scheduled" and body["departures"][0]["line"] == "2"
    assert "not offered" in body["live"] or "not used" in body["live"]


def test_map_extract_groups_layers(server):
    body, _ = call(server, "map_extract", {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02], "layers": ["water", "stops"]})
    assert body["layers"]["water"]["polygons"] and any(s["name"] == "Aleja Łąkowa" for s in body["layers"]["stops"])
    assert body["points"] <= 4000


def test_render_map_svg(server):
    plan, _ = call(server, "plan_trip", {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-11-10 07:58"})
    it = next(i for i in plan["itineraries"] if any(l["mode"] != "walk" for l in i["legs"]))
    body, err = call(server, "render_map", {"city": "testowo", "trip": it["trip"], "size": 400, "output": "svg"})
    assert not err and body["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg" width=')
    assert max(body["width"], body["height"]) == 400  # size is the longer side
    assert 'stroke-dasharray="0.1 5"' in body["svg"] and ">1</text>" in body["svg"]


def test_errors_are_reported(server):
    text, err = call(server, "departures", {"city": "nowhere", "stop_id": "x"})
    assert err and "not available" in text


def test_stdio_end_to_end(city_dir):
    """The real entry point over stdio, as an MCP client launches it."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "poland_transit_mcp", "--data-dir", str(city_dir), "--cities", "testowo"],
                                   env={"POLAND_TRANSIT_NO_WARMUP": "1"})

    async def go():
        async with Client(params) as c:
            r = await c.call_tool("plan_trip", {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-11-10 07:58"})
            return json.loads(r.content[0].text)
    body = anyio.run(go)
    assert any(l.get("line") == "2" for i in body["itineraries"] for l in i["legs"])


def test_map_extract_ignores_repeated_layers(server):
    args = {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02]}
    once, e1 = call(server, "map_extract", {**args, "layers": ["water", "stops"]})
    again, e2 = call(server, "map_extract", {**args, "layers": ["water", "water", "stops", "water"]})
    assert not e1 and not e2
    assert once == again  # a repeated layer neither repeats its work nor its output


@pytest.mark.parametrize("name,args,word", [
    ("find_places", {"city": "testowo", "near": [50.0]}, "near"),
    ("walk_route", {"city": "testowo", "from": [50.0], "to": at(2, 9)}, "point"),
    ("render_map", {"city": "testowo", "bbox": [50.0, 19.0, 50.01, 19.0]}, "bbox"),
])
def test_bad_point_shapes_are_explained(server, name, args, word):
    """A malformed point or box must give the model a message naming the argument, not a bare crash."""
    text, err = call(server, name, args)
    assert err and word in text.lower()


def test_live_falls_back_on_malformed_feed(city, monkeypatch):
    from types import SimpleNamespace

    from poland_transit_mcp import live
    monkeypatch.setattr(city, "cfg", SimpleNamespace(live="ztm-gdansk"))
    monkeypatch.setattr(live, "_enabled", {city.id})
    monkeypatch.setattr(live, "_fetch_ztm", lambda stop_num: {"departures": [{"status": "REALTIME"}]})  # no theoreticalTime
    out = live.live_departures(city, [s for s in city.tt.stops if s][:2], None, 5)
    assert out["ok"] is False and out["status"].startswith("unavailable")
    assert isinstance(out["departures"], list)


def test_map_extract_cuts_shapes_at_the_box(server):
    box = [*at(2, 6), *at(6, 9)]  # across the river, which runs the whole height of the city
    body, err = call(server, "map_extract", {"city": "testowo", "bbox": box})
    assert not err, body
    pad = 0.002  # a line keeps one point beyond the edge so it reaches it
    shapes = [line for g in body["layers"].values() if isinstance(g, dict) for line in g.get("lines", [])]
    shapes += [ring for g in body["layers"].values() if isinstance(g, dict) for p in g.get("polygons", []) for ring in p]
    assert shapes
    for shape in shapes:
        assert all(box[0] - pad <= y <= box[2] + pad and box[1] - pad <= x <= box[3] + pad for y, x in shape)


def test_map_extract_leaves_out_whole_layers_by_priority(server):
    args = {"city": "testowo", "bbox": [49.999, 18.999, 50.01, 19.02]}
    full, _ = call(server, "map_extract", args)
    small, _ = call(server, "map_extract", {**args, "max_points": 100})
    assert small["points"] <= 100 or "left out" in small["note"]
    if "note" in small:
        left = small["note"].split(": ", 1)[1].split(";")[0].split(", ")
        assert not set(left) & set(small["layers"])  # a layer is in whole or named as left out, never in pieces


def test_a_stop_found_by_the_start_of_its_name_is_named(server):
    body, err = call(server, "departures", {"city": "testowo", "stop_id": "Aleja", "at": "2026-11-10 08:00"})
    assert not err, body
    assert body["note"].startswith('"Aleja" matched the stop "Aleja Łąkowa"') and body["at"] == "2026-11-10 08:00"
    exact, _ = call(server, "departures", {"city": "testowo", "stop_id": "Aleja Łąkowa", "at": "2026-11-10 08:00"})
    assert "matched the stop" not in (exact.get("note") or "")
