"""Small answers: plans leave out line geometry unless asked (`detail`), a trip is named by a short reference
instead of being copied back, and render_map returns an image rather than SVG text."""
import base64
import json

import anyio
import pytest
from fixture import at
from mcp import Client

from poland_transit_mcp import trips
from poland_transit_mcp.server import create_server

PLAN = {"city": "testowo", "from": at(2, 1), "to": at(18, 12), "depart_at": "2026-11-10 07:58"}


@pytest.fixture(scope="module")
def server(city):
    return create_server({"testowo": city})


def call(server, name, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args)
    return anyio.run(go)


def text(r):
    return next(b.text for b in r.content if b.type == "text")


def legs(answer):
    return [leg for it in answer["itineraries"] for leg in it["legs"]]


def test_plans_leave_out_line_geometry_unless_asked(server):
    summary = call(server, "plan_trip", PLAN).structured_content
    full = call(server, "plan_trip", {**PLAN, "detail": "full"}).structured_content
    assert not any("path" in leg for leg in legs(summary))
    assert all("path" in leg for leg in legs(full))
    assert len(json.dumps(summary)) < len(json.dumps(full))
    lean = lambda a: [[{k: v for k, v in leg.items() if k not in ("path", "trip_id", "color", "status")}
                       for leg in it["legs"]] for it in a["itineraries"]]
    ends = lambda a: [[(leg["from"]["name"], leg["to"]["name"]) for leg in it["legs"]] for it in a["itineraries"]]
    assert ends(summary) == ends(full)  # the same plan
    assert [[set(leg) for leg in it] for it in lean(summary)] == [[set(leg) for leg in it] for it in lean(full)]


def test_a_plan_leaves_out_what_only_code_needs_unless_asked(server):
    summary = call(server, "plan_trip", PLAN).structured_content
    full = call(server, "plan_trip", {**PLAN, "detail": "full"}).structured_content
    rides = [leg for leg in legs(full) if leg["mode"] != "walk"]
    assert rides and all("trip_id" in leg and "color" in leg and "at" in leg["from"] for leg in rides)
    for leg in legs(summary):
        assert not {"trip_id", "color", "path"} & set(leg), leg
        assert "at" not in leg["from"] and "at" not in leg["to"] and "city" not in leg["from"]
        assert leg.get("status") != "scheduled"
    assert "at" in summary["from"] and "at" in summary["to"]  # the plan's own ends keep their points


def test_departures_and_lines_leave_out_ids_and_colours_unless_asked(server):
    board = {"city": "testowo", "stop_id": "T:A", "at": "2026-11-10 08:00"}
    summary = call(server, "departures", board).structured_content["departures"]
    full = call(server, "departures", {**board, "detail": "full"}).structured_content["departures"]
    assert summary and all(not {"trip_id", "color"} & set(d) and "status" in d for d in summary)
    assert all("trip_id" in d for d in full)
    line = {"city": "testowo", "line": "2", "date": "2026-11-10"}
    summary = call(server, "line_info", line).structured_content
    full = call(server, "line_info", {**line, "detail": "full"}).structured_content
    assert "color" not in summary and all(isinstance(s, str) for d in summary["directions"] for s in d["stops"])
    assert all(isinstance(s, dict) and "stop_id" in s for d in full["directions"] for s in d["stops"])


def test_each_itinerary_names_itself_and_render_map_draws_it(server):
    plan = call(server, "plan_trip", PLAN).structured_content
    it = next(i for i in plan["itineraries"] if any(leg["mode"] != "walk" for leg in i["legs"]))
    assert it["trip"].startswith("t2?c=testowo&") and len(it["trip"]) < 200
    r = call(server, "render_map", {"city": "testowo", "trip": it["trip"]})
    assert not r.is_error, text(r)
    assert r.content[0].type == "text"  # text first: readers of content[0] get the answer
    image = r.content[1]
    assert image.type == "image" and image.mime_type == "image/png"
    assert base64.b64decode(image.data)[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.structured_content["format"] == "png" and "svg" not in r.structured_content
    assert len(text(r)) < 3000  # the model reads metadata and credits, not the drawing


def test_a_trip_reference_holds_an_absolute_time(server):
    plan = call(server, "plan_trip", {k: v for k, v in PLAN.items() if k != "depart_at"} | {"depart_at": "07:58"}).structured_content
    _, args, _, _ = trips.read(plan["itineraries"][0]["trip"])
    assert args["depart_at"].count("-") == 2  # YYYY-MM-DD HH:MM, never "now" or a bare time


@pytest.mark.parametrize("ref, says", [
    ("t1.bm90IGpzb24", "not a trip reference"),
    ("hello", "not a trip reference"),
    ("t2?", "not a trip reference"),
])
def test_bad_references_are_refused_with_a_sentence(server, ref, says):
    r = call(server, "render_map", {"city": "testowo", "trip": ref})
    assert r.is_error and says in text(r)


def test_a_changed_trip_asks_to_plan_again(server):
    plan = call(server, "plan_trip", PLAN).structured_content
    tool, args, index, _ = trips.read(plan["itineraries"][0]["trip"])
    stale = trips.make(tool, args, index, {"depart": "00:00", "arrive": "00:01", "legs": []})
    r = call(server, "render_map", {"city": "testowo", "trip": stale})
    assert r.is_error and "call plan_trip again" in text(r)


def test_svg_on_request(server):
    plan = call(server, "plan_trip", PLAN).structured_content
    r = call(server, "render_map", {"city": "testowo", "trip": plan["itineraries"][0]["trip"], "output": "svg"})
    assert r.structured_content["svg"].startswith("<svg") and r.content[0].type == "text"


@pytest.mark.parametrize("when", [
    {"depart_at": "2026-11-10 07:58:31"},  # seconds are kept, not lost
    {"depart_at": "2026-11-10T07:58:31+01:00"},
    {"depart_at": "2026-11-10 25:10"},  # after midnight, on the service day of the 10th
    {"arrive_by": "2026-11-10 09:00:30"},
    {},  # now
])
def test_every_itinerary_draws_from_its_reference(server, when):
    args = {k: v for k, v in PLAN.items() if k != "depart_at"} | when
    plan = call(server, "plan_trip", args).structured_content
    for it in plan["itineraries"]:
        r = call(server, "render_map", {"city": "testowo", "trip": it["trip"], "output": "svg"})
        assert not r.is_error, (when, text(r))


def test_a_time_after_midnight_reads_as_one_date_and_time(server):
    plan = call(server, "plan_trip", {**PLAN, "depart_at": "2026-11-10 25:10"}).structured_content
    assert plan["depart_at"] == "2026-11-11 01:10"


@pytest.mark.parametrize("bbox", [[50.0, 19.0, 50.02, 19.00001], [50.0, 19.0, 50.00001, 19.02]])
def test_a_thin_box_is_widened_so_the_map_stays_small(server, bbox):
    """Security: a valid 2 km x 1 m box asked for about 4 million pixel rows; a map is now at most 4:1."""
    r = call(server, "render_map", {"city": "testowo", "bbox": bbox, "size": 2000, "output": "svg"})
    assert not r.is_error, text(r)
    w, h = r.structured_content["width"], r.structured_content["height"]
    assert max(w, h) <= 2001 and max(w, h) / min(w, h) <= 4.01


@pytest.mark.parametrize("bbox", [[50.0, 19.0, 50.02, 19.005], [50.0, 19.0, 50.005, 19.02], [50.0, 19.0, 50.02, 19.03]])
def test_size_is_the_longer_side_and_a_png_stays_within_what_a_model_reads(server, bbox):
    """Tall, wide and square maps at the largest size: the longer side is `size`, and a PNG has at most 4,784
    patches of 28 x 28 px (a larger image is scaled down by the model anyway)."""
    import math
    svg = call(server, "render_map", {"city": "testowo", "bbox": bbox, "size": 2000, "output": "svg"}).structured_content
    assert max(svg["width"], svg["height"]) in (1999, 2000, 2001)
    png = call(server, "render_map", {"city": "testowo", "bbox": bbox, "size": 2000}).structured_content
    assert math.ceil(png["width"] / 28) * math.ceil(png["height"] / 28) <= 4784
    assert png["image"] == "the next content block"


def test_a_swapped_box_and_unknown_style_are_refused(server):
    r = call(server, "render_map", {"city": "testowo", "bbox": [19.0, 50.0, 19.02, 50.02]})
    assert r.is_error and "swap" in text(r)
    r = call(server, "map_extract", {"city": "testowo", "bbox": [19.0, 50.0, 19.02, 50.02]})
    assert r.is_error and "swap" in text(r)
    r = call(server, "render_map", {"city": "testowo", "bbox": [50.0, 19.0, 50.01, 19.01], "style": {"rivers": "#00f"}})
    assert r.is_error and '"rivers"' in text(r) and "water" in text(r)
    r = call(server, "render_map", {"city": "testowo", "bbox": [50.0, 19.0, 50.01, 19.01], "style": {"water": "url(#x)"}})
    assert r.is_error and "colour" in text(r)


def test_walk_route_gives_its_path_only_when_asked(server):
    args = {"city": "testowo", "from": at(2, 6), "to": at(2, 9)}
    assert "path" not in call(server, "walk_route", args).structured_content
    assert call(server, "walk_route", {**args, "detail": "full"}).structured_content["path"]


GOOD = "c=testowo&f=50.0,19.0&t=50.01,19.01&d=2026-11-10T07:58:00&x=2&i=0&h=0123456789ab"


@pytest.mark.parametrize("query", [
    GOOD.replace("x=2", "x=9"), GOOD.replace("x=2", "x=1.5"), GOOD.replace("x=2", "x=-1"), GOOD.replace("i=0", "i=-1"),
    GOOD.replace("i=0", "i=10"), GOOD.replace("d=2026-11-10T07:58:00", "d=now"), GOOD + "&d=2026-11-10T07:58:00",
    GOOD + "&a=2026-11-10T07:58:00", GOOD.replace("&d=2026-11-10T07:58:00", ""), GOOD + "&extra=1", GOOD + "&c=other",
    GOOD.replace("h=0123456789ab", "h=xyz"), GOOD.replace("c=testowo", "c=../etc"), GOOD.replace("f=50.0,19.0", "f="),
    GOOD.replace("f=50.0,19.0", "f=" + "a" * 300), GOOD + "&tc=" + "B" * 3, GOOD.replace("&", ";"), "&" * 1500,
])
def test_crafted_references_get_a_clean_refusal(server, query):
    r = call(server, "render_map", {"city": "testowo", "trip": trips.PREFIX + query})
    assert r.is_error and "not a trip reference" in text(r)


def test_a_well_formed_reference_to_another_trip_asks_to_plan_again(server):
    r = call(server, "render_map", {"city": "testowo", "trip": trips.PREFIX + GOOD})
    assert r.is_error and "call plan_trip again" in text(r)


def test_trip_and_itinerary_together_are_refused_and_a_full_itinerary_still_draws(server):
    plan = call(server, "plan_trip", {**PLAN, "detail": "full"}).structured_content
    it = plan["itineraries"][0]
    r = call(server, "render_map", {"city": "testowo", "trip": it["trip"], "itinerary": it})
    assert r.is_error and "not both" in text(r)
    r = call(server, "render_map", {"city": "testowo", "itinerary": it, "output": "svg"})
    assert not r.is_error and r.structured_content["svg"].startswith("<svg")


def test_a_trip_longer_than_one_map_is_drawn_from_its_start_with_the_rest_marked(server):
    """The part of the trip from its start that fits the 18 km limit, and an arrow at the edge naming the end
    and its distance, instead of "bbox too large" about a box the caller never gave."""
    start, far = at(2, 1), [at(2, 1)[0], at(2, 1)[1] + 0.6]  # about 43 km east
    itinerary = {"legs": [{"mode": "rail", "line": "IC", "from": {"name": "Alfa", "at": start},
                           "to": {"name": "Daleko", "at": far},
                           "path": [[start[0], start[1] + k * 0.01] for k in range(61)]}]}
    r = call(server, "render_map", {"city": "testowo", "itinerary": itinerary, "output": "svg"})
    assert not r.is_error, text(r)
    body = r.structured_content
    assert "Daleko" in body["note"] and "beyond this map" in body["note"]
    assert "Daleko: " in body["svg"] and " km</text>" in body["svg"]


def test_a_trip_that_fits_has_no_arrow(server):
    plan = call(server, "plan_trip", PLAN).structured_content
    it = next(i for i in plan["itineraries"] if any(leg["mode"] != "walk" for leg in i["legs"]))
    r = call(server, "render_map", {"city": "testowo", "trip": it["trip"], "output": "svg"})
    assert "note" not in r.structured_content


def test_line_info_leaves_out_geometry_unless_asked_and_caps_directions(server, monkeypatch):
    import poland_transit_mcp.server as srv
    args = {"city": "testowo", "line": "2", "date": "2026-11-10"}
    summary = call(server, "line_info", args).structured_content
    full = call(server, "line_info", {**args, "detail": "full"}).structured_content
    assert summary["directions"] and not any("path" in d for d in summary["directions"])
    assert all("path" in d for d in full["directions"])
    real = srv._line_info

    def three_directions(*a):  # the fixture's lines run one way; the busiest directions are kept, in their order
        body = real(*a)
        d = body["directions"][0]
        body["directions"] = [{**d, "direction": "A", "trips": 1}, {**d, "direction": "B", "trips": 9},
                              {**d, "direction": "C", "trips": 5}]
        return body
    monkeypatch.setattr(srv, "_line_info", three_directions)
    monkeypatch.setattr(srv, "MAX_DIRECTIONS", 2)
    cut = call(server, "line_info", args).structured_content
    assert [d["direction"] for d in cut["directions"]] == ["B", "C"]
    assert cut["note"].startswith("the 2 directions with the most trips of 3; 1 more (1 trip) left out")


def test_a_huge_label_in_an_itinerary_is_cut_before_drawing(server, city):
    """Security: a 500,000-character line label took 302 s to draw as a PNG. The argument refuses it, and the
    drawing cuts labels too (a trip reference's names come from the data, not the caller)."""
    import time
    from poland_transit_mcp.render import render_map
    start = at(2, 1)
    itinerary = {"legs": [{"mode": "bus", "line": "X" * 500_000, "from": {"name": "N" * 100_000, "at": start},
                           "to": {"name": "Y" * 100_000, "at": at(2, 6)}, "path": [start, at(2, 6)]}]}
    t = time.perf_counter()
    r = call(server, "render_map", {"city": "testowo", "itinerary": itinerary})
    assert r.is_error and "at most 60 characters" in text(r) and len(text(r)) < 400
    svg = render_map(city, None, itinerary, 800)
    assert "X" * 12 not in svg and "N" * 60 not in svg
    assert time.perf_counter() - t < 5


def test_an_option_of_a_default_plan_points_to_its_trip_reference(server):
    """A default (lean) plan has no leg paths or leg-end points: passed as an itinerary it is refused, with a bbox
    too (else the map would show the area without the trip), and the message names `trip`."""
    plan = call(server, "plan_trip", PLAN).structured_content
    option = next(i for i in plan["itineraries"] if any(leg["mode"] != "walk" for leg in i["legs"]))
    for extra in ({}, {"bbox": [*at(0, 0), *at(4, 4)]}):
        r = call(server, "render_map", {"city": "testowo", "itinerary": option, **extra})
        assert r.is_error and "`trip`" in text(r), text(r)
    full = call(server, "plan_trip", {**PLAN, "detail": "full"}).structured_content
    option = next(i for i in full["itineraries"] if any(leg["mode"] != "walk" for leg in i["legs"]))
    r = call(server, "render_map", {"city": "testowo", "itinerary": option})
    assert not r.is_error, text(r)
    assert not call(server, "render_map", {"city": "testowo", "trip": option["trip"]}).is_error


def _patches(r):
    import math
    return math.ceil(r["width"] / 28) * math.ceil(r["height"] / 28)


@pytest.mark.parametrize("when", [{"depart_at": "2026-11-10 07:58"}, {"depart_at": "2026-11-10 12:00"},
                                  {"arrive_by": "2026-11-10 09:00"}])
def test_a_trip_map_without_size_stays_within_about_300_image_patches(server, when):
    """A trip map's picture repeats what the plan says: by default it costs at most about 300 image tokens, with the
    longer side at most 784 px. `size` still asks for more."""
    args = {k: v for k, v in PLAN.items() if k != "depart_at"} | when
    for it in call(server, "plan_trip", args).structured_content["itineraries"]:
        r = call(server, "render_map", {"city": "testowo", "trip": it["trip"]}).structured_content
        assert _patches(r) <= 300 and max(r["width"], r["height"]) <= 784, r
        big = call(server, "render_map", {"city": "testowo", "trip": it["trip"], "size": 1000}).structured_content
        assert max(big["width"], big["height"]) == 1000


def test_an_area_map_without_size_is_784_px(server):
    for bbox in ([*at(0, 0), *at(4, 4)], [*at(0, 0), *at(2, 8)]):
        r = call(server, "render_map", {"city": "testowo", "bbox": bbox, "output": "svg"}).structured_content
        assert max(r["width"], r["height"]) in (783, 784, 785), r


def test_small_parks_are_left_out(city, monkeypatch):
    """A park under 10 px across adds bytes, not information; water is kept down to 4 px."""
    from poland_transit_mcp import render
    from poland_transit_mcp.mapdata import Feature

    def square(lat, lon, side_deg):
        return [[lat, lon], [lat + side_deg, lon], [lat + side_deg, lon + side_deg], [lat, lon + side_deg], [lat, lon]]

    box = [*at(0, 0), *at(4, 4)]  # about 560 m across, drawn 784 px wide: about 0.7 m per px
    m_px = (box[3] - box[1]) * 0.64 * 111320 / 784
    side = 7 * m_px / 111320  # a 7 px square
    feats = [Feature("parks", "park", "polygon", [square(box[0] + 0.001, box[1] + 0.001, side)]),
             Feature("water", "area", "polygon", [square(box[0] + 0.002, box[1] + 0.002, side)])]
    monkeypatch.setattr(render, "map_features", lambda *a, **k: (feats, None))
    svg = render.render_map(city, box, None, 784)
    assert render.DEFAULT_THEME["parks"] not in svg and render.DEFAULT_THEME["water"] in svg
    feats[0] = Feature("parks", "park", "polygon", [square(box[0] + 0.001, box[1] + 0.001, side * 2)])
    assert render.DEFAULT_THEME["parks"] in render.render_map(city, box, None, 784)


def test_a_png_has_a_palette_with_pillow_and_is_unchanged_without(server, monkeypatch):
    """Pillow (the small-png extra) saves the PNG with 256 colours: far fewer bytes, re-sent with every later message.
    Without Pillow the PNG is the one resvg drew."""
    import builtins
    import io
    pil = pytest.importorskip("PIL.Image")
    args = {"city": "testowo", "bbox": [*at(0, 0), *at(4, 4)]}
    small = base64.b64decode(call(server, "render_map", args).content[1].data)
    with pil.open(io.BytesIO(small)) as im:
        assert im.mode == "P"
    real_import = builtins.__import__

    def no_pillow(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    plain = base64.b64decode(call(server, "render_map", args).content[1].data)
    monkeypatch.undo()
    assert plain[:8] == b"\x89PNG\r\n\x1a\n" and len(small) < len(plain) * 0.6
    with pil.open(io.BytesIO(plain)) as im:
        assert im.mode in ("RGB", "RGBA")


def test_a_full_line_answer_stays_within_its_size_by_simplifying_paths():
    """A national category such as TLK: 10 directions with long paths reached 54,736 characters with detail=full,
    over what clients read in one go; the paths are simplified until the answer fits, and the note says so."""
    import json
    import math

    from poland_transit_mcp.server import MAX_LINE_CHARS, _fit_line

    wiggle = [[50 + i * 0.001, 19 + 0.01 * math.sin(i / 3)] for i in range(4000)]
    body = {"line": "X", "directions": [{"to": f"D{i}", "stops": [{"stop_id": f"s{j}", "name": f"Stop {j}"}
                                                                   for j in range(20)], "path": wiggle}
                                        for i in range(10)], "note": "the 10 directions with the most trips"}
    assert len(json.dumps(body)) > MAX_LINE_CHARS
    out = _fit_line(body)
    assert len(json.dumps(out, ensure_ascii=False, separators=(",", ":"))) <= MAX_LINE_CHARS
    assert all(len(x["path"]) >= 2 for x in out["directions"]) and "simplified" in out["note"]
    small = {"line": "Y", "directions": [{"to": "Z", "stops": [], "path": wiggle[:50]}]}
    assert _fit_line(json.loads(json.dumps(small))) == small  # an answer that fits is not touched
