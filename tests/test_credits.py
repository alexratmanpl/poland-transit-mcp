"""Credits: answers carry ids and data dates; the full text is one set (the credits tool, the credits://all
resource, `poland-transit-mcp credits`)."""
import json
import subprocess
import sys

import anyio
import pytest
from fixture import build_national_fixture, near
from mcp import Client

from poland_transit_mcp.pool import CityPool
from poland_transit_mcp.server import create_server

START, END = near("alfa", 2, 1), near("beta", 18, 12)


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return build_national_fixture(tmp_path_factory.mktemp("credits"))


@pytest.fixture(scope="module")
def server(city, files):
    alfa, beta, nat = files
    return create_server(CityPool({"testowo": city, "alfa": alfa, "beta": beta, "poland": nat}, 1500))


def run(server, fn):
    async def go():
        async with Client(server) as c:
            return await fn(c)
    return anyio.run(go)


def test_every_id_in_an_answer_has_its_text(server):
    plan = run(server, lambda c: c.call_tool("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                                           "depart_at": "2026-11-10 08:40"})).structured_content
    assert "osm" in plan["credits"] and any(i.startswith("poland/") for i in plan["credits"])
    full = run(server, lambda c: c.call_tool("credits", {"ids": plan["credits"]})).structured_content
    assert {e["id"] for e in full["credits"]} == set(plan["credits"])
    assert all(e["text"] for e in full["credits"]) and "note" not in full
    assert "processed" in full["notice"]


def test_an_unknown_id_is_named(server):
    body = run(server, lambda c: c.call_tool("credits", {"ids": ["osm", "nowhere/X"]})).structured_content
    assert [e["id"] for e in body["credits"]] == ["osm"] and "nowhere/X" in body["note"]


def test_the_set_is_also_a_resource(server):
    """The resource (and the published file) keeps every field; the tool gives each id's text to show and the notice:
    name, licence and city repeat what the text and the id already say, and a model pays for every word."""
    res = run(server, lambda c: c.read_resource("credits://all"))
    body = json.loads(res.contents[0].text)
    tool = run(server, lambda c: c.call_tool("credits", {})).structured_content
    assert [{"id": e["id"], "text": e["text"]} for e in body["credits"]] == tool["credits"]
    assert body["notice"] == tool["notice"] and "about" in body and "about" not in tool
    assert any("licence" in e for e in body["credits"])


def test_two_cities_with_the_same_date_key_are_told_apart(server):
    """Two cities' OSM dates (and feeds with the same key) get per-city keys instead of one overwriting the other."""
    plan = run(server, lambda c: c.call_tool("plan_trip", {"city": "alfa", "from": START, "to_city": "beta", "to": END,
                                                           "depart_at": "2026-11-10 08:40"})).structured_content
    keys = plan["as_of"]
    assert "osm" not in keys and {"osm_alfa", "osm_beta", "gtfs_alfa_T", "gtfs_beta_T"} <= set(keys)
    assert "gtfs_T" not in keys and "gtfs_PL" in keys  # a key only one city reports keeps its short name


def test_the_command_line_prints_the_set(files):
    alfa = files[0]
    out = subprocess.run([sys.executable, "-m", "poland_transit_mcp", "credits", "--data-dir", str(alfa.path.parent),
                          "--cities", "alfa"], capture_output=True, text=True, check=True).stdout
    body = json.loads(out)
    assert body["credits"][0]["id"] == "osm" and any(e["id"].startswith("alfa/") for e in body["credits"])


def test_as_of_is_a_date_per_source_and_the_full_dates_on_request(server):
    """Answers carry each source's publication date; detail="full" and list_cities give every date of a feed."""
    import re
    args = {"city": "alfa", "from": START, "to_city": "beta", "to": END, "depart_at": "2026-11-10 08:40"}
    lean = run(server, lambda c: c.call_tool("plan_trip", args)).structured_content["as_of"]
    assert lean and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}|unknown", v) for v in lean.values()), lean
    assert any(v != "unknown" for v in lean.values())
    full = run(server, lambda c: c.call_tool("plan_trip", {**args, "detail": "full"})).structured_content["as_of"]
    assert set(full) == set(lean)
    feeds = {k: v for k, v in full.items() if k.startswith("gtfs")}
    dates = {"published", "retrieved", "valid_from", "valid_to"}  # a date not known is left out
    assert feeds and all({"valid_from", "valid_to"} <= set(v) <= dates for v in feeds.values()), feeds
    assert all(v.get("published", "unknown").startswith(lean[k]) for k, v in feeds.items())
    cities = run(server, lambda c: c.call_tool("list_cities", {})).structured_content["cities"]
    listed = [f for c in cities for f in (c.get("timetable") or {}).get("feeds", []) if f["status"] == "ok"]
    assert listed and all(f["valid_from"] and f["valid_to"] for f in listed) and all("valid" not in f for f in listed)


def test_as_of_values_match_the_schema_and_name_only_feeds_used(city, monkeypatch):
    """A feed that is not ok gave no data: it has no date in as_of (list_cities shows its status). Every value is a
    date or "unknown", as the schema says."""
    import re
    feeds = [dict(city.feeds[0]), {**city.feeds[0], "key": "X", "status": "failed"}]
    monkeypatch.setattr(city, "feeds", feeds)
    as_of = city.provenance(("osm", "gtfs"))["as_of"]
    assert "gtfs_X" not in as_of and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}|unknown", v) for v in as_of.values()), as_of
