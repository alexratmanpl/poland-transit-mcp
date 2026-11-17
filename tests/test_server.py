import json
import sys

import anyio
import pytest
from fixture import at
from mcp import Client, StdioServerParameters

from poland_transit_mcp.server import create_server

TOOLS = {"departures", "find_places", "line_info", "list_cities", "map_extract", "plan_trip", "render_map", "stops_near", "walk_route"}


@pytest.fixture(scope="module")
def server(city):
    return create_server({"testowo": city})


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            r = await c.call_tool(name, args)
            return r.content[0].text, r.is_error
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
def test_every_answer_has_source_and_as_of(server, name, args):
    body, err = call(server, name, args)
    assert not err, body
    assert isinstance(body["source"], list) and isinstance(body["as_of"], dict)


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
    body, err = call(server, "render_map", {"city": "testowo", "itinerary": it, "width": 400})
    assert not err and body["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg" width="400"')
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
