"""Writes the reference part of docs/TOOLS.md from the server's own tools/list: each tool's arguments (from its
inputSchema) and the fields of its answer (from its outputSchema), so the document cannot drift from the code.

    uv run python scripts/gen_tools_doc.py          # rewrite the part between the markers
    uv run python scripts/gen_tools_doc.py --check  # exit 1 if docs/TOOLS.md is out of date

The server is built on the synthetic test cities (tests/fixture.py); the schemas do not depend on the data loaded.
tests/test_tools_doc.py fails when the file is out of date."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "TOOLS.md"
BEGIN = "<!-- BEGIN GENERATED from tools/list by scripts/gen_tools_doc.py: do not edit by hand -->"
END = "<!-- END GENERATED -->"
FULL = 'only with detail="full"'
# What a field means where its schema has no description (answers.py keeps the wire schema small), keyed by
# (tool or shape, field); "*" is a field of every answer. A key that names no published field stops the generator.
NOTES = {
    ("*", "credits"): "ids of the data used (`osm`, `<city>/<feed key>`, `<city>/live`): their full text from the credits tool",
    ("*", "note"): "what the data could not answer, or that the answer was cut and how to get the rest",
    ("stops_near", "point"): "[lat, lon] searched from",
    ("departures", "at"): "the date and time the board starts from",
    ("departures", "live"): "whether live estimates were used",
    ("departures", "to"): "the stop the departures go on to, when asked for",
    ("departures", "stop"): "name, at and the stop_ids asked about",
    ("walk_route", "path"): "[lat, lon] points",
    ("plan_trip", "assumptions"): "estimates the plan relies on, such as change times between cities",
    ("render_map", "format"): "png: the image follows the text block; svg: the source is in `svg`",
    ("render_map", "image"): "where the PNG is: the next content block",
    ("credits", "notice"): "shown with the timetable credits",
    ("CityEntry", "city"): "the id other tools take",
    ("CityEntry", "kind"): "set on a national layer",
    ("CityEntry", "bbox"): "[minLat, minLon, maxLat, maxLon]",
    ("CityEntry", "timetable"): "feeds with their credit ids and dates, and the dates they cover (`valid`)",
    ("CityEntry", "built_at"): "when the data file was built, UTC (YYYY-MM-DDTHH:MM:SSZ)",
    ("CreditEntry", "text"): "the credit to show: maker, licence, links",
    ("Departure", "time"): "HH:MM local; with `date` when not on the day asked",
    ("Departure", "status"): "scheduled, or live (then `scheduled` holds the timetable time)",
    ("Departure", "arrive"): "with `to`: the arrival time at that stop",
    ("Departure", "train"): "national layer: the train's number or name",
    ("Departure", "trip_id"): "the run's id in the feed",
    ("Leg", "trip_id"): "the run's id in the feed",
    ("LegEnd", "city"): "city id of this end (trips between cities)",
    ("Itinerary", "depart"): "HH:MM, or YYYY-MM-DD HH:MM on another day",
    ("Itinerary", "walk_m"): "metres walked in all",
    ("Itinerary", "trip"): "pass to render_map to draw this trip",
    ("Leg", "mode"): "walk, bus, tram, rail, metro, trolleybus, ferry",
    ("Leg", "depart"): "HH:MM, or YYYY-MM-DD HH:MM on another day",
    ("Leg", "stops"): "stops passed on this leg",
    ("Leg", "path"): "[lat, lon] points",
    ("Leg", "train"): "national layer: the train's number or name",
    ("LegEnd", "at"): "[lat, lon]",
    ("LegEnd", "stop_id"): "for departures",
    ("Named", "at"): "[lat, lon]",
    ("Named", "stop_id"): "for departures",
    ("NearStop", "at"): "[lat, lon]",
    ("NearStop", "walk_min"): "minutes on foot from the point",
    ("NearStop", "lines"): "lines serving it on that date",
    ("Place", "at"): "[lat, lon]",
    ("Place", "stop_ids"): "a stop: its platforms' ids, for departures",
    ("StationItinerary", "start"): "the start stop used: stop, name, stop_id, minutes",
    ("StationItinerary", "end"): "the end stop used: stop, name, stop_id, minutes",
}
NOT_DATA = {"credits"}  # tools whose answer is not data (no credits/as_of of its own): listed with all their fields


def _escape(text: str) -> str:
    """One table cell or paragraph: no line breaks, pipes escaped, `<` escaped outside code spans."""
    parts = " ".join(str(text).split()).replace("|", "\\|").split("`")
    return "`".join(p if i % 2 else p.replace("<", "\\<") for i, p in enumerate(parts))


def _type(s: dict) -> str:
    if "$ref" in s:
        name = s["$ref"].rsplit("/", 1)[-1]
        return f"[{name}](#{name.lower()})"
    if "anyOf" in s:
        return " or ".join(_type(x) for x in s["anyOf"])
    t = s.get("type")
    if t == "array":
        inner = _type(s.get("items", {})) or "any"
        return f"({inner})[]" if " or " in inner else f"{inner}[]"
    if t == "object":
        extra = s.get("additionalProperties")
        return f"object of {_type(extra)}" if isinstance(extra, dict) else "object"
    return t or "any"


def _range(lo: Any, hi: Any, unit: str = "") -> str:
    if lo is not None and hi is not None:
        return f"{lo}{unit}" if lo == hi else f"{lo} to {hi}{unit}"
    if hi is not None:
        return f"at most {hi}{unit}"
    return f"at least {lo}{unit}" if lo is not None else ""


def _limits(s: dict) -> str:
    if "anyOf" in s:
        return "; or ".join(x for x in (_limits(b) for b in s["anyOf"]) if x)
    out = []
    if "enum" in s:
        out.append("one of " + ", ".join(f"`{v}`" for v in s["enum"]))
    if "minLength" in s or "maxLength" in s:
        out.append("length " + _range(s.get("minLength"), s.get("maxLength")))
    if "exclusiveMinimum" in s:
        out.append(f"over {s['exclusiveMinimum']}" + (f", at most {s['maximum']}" if "maximum" in s else ""))
    elif "minimum" in s or "maximum" in s:
        out.append(_range(s.get("minimum"), s.get("maximum")))
    if "minItems" in s or "maxItems" in s:
        out.append(_range(s.get("minItems"), s.get("maxItems"), " items"))
    if "maxProperties" in s:
        out.append(f"at most {s['maxProperties']} keys")
    if isinstance(s.get("propertyNames"), dict) and _limits(s["propertyNames"]):
        out.append("keys: " + _limits(s["propertyNames"]))
    if isinstance(s.get("additionalProperties"), dict) and _limits(s["additionalProperties"]):
        out.append("values: " + _limits(s["additionalProperties"]))
    if s.get("type") == "array" and isinstance(s.get("items"), dict) and "properties" not in s["items"]:
        each = _limits(s["items"])
        if each:
            out.append("each " + each)
    return "; ".join(out)


def _arguments(schema: dict) -> list[str]:
    props = schema.get("properties", {})
    if not props:
        return ["No arguments."]
    rows = ["| Argument | Type | Default | Limits | Description |", "|---|---|---|---|---|"]

    def add(name: str, s: dict, required: bool) -> None:
        default = f"`{json.dumps(s['default'], ensure_ascii=False)}`" if "default" in s else ("required" if required else "")
        rows.append(f"| `{name}` | {_type(s)} | {default} | {_escape(_limits(s))} | {_escape(s.get('description', ''))} |")
        item = s.get("items", {}) if s.get("type") == "array" else {}
        for sub, ss in item.get("properties", {}).items():
            add(f"{name}[].{sub}", ss, sub in item.get("required", []))

    for name, s in props.items():
        add(name, s, name in schema.get("required", []))
    return rows


def _fields(schema: dict, owner: str, used: set, skip: set[str] = frozenset()) -> list[str]:
    rows = ["| Field | Type | Present | Description |", "|---|---|---|---|"]
    for name, s in schema.get("properties", {}).items():
        if name in skip:
            continue
        used.add((owner, name))
        desc = s.get("description", "")
        if name in schema.get("required", []):
            present = "always"
        elif desc == FULL or desc.endswith("; " + FULL):
            present = '`detail="full"`'
            desc = desc[: -len(FULL)].rstrip("; ")
        else:
            present = "optional"
        desc = desc or NOTES.get((owner, name), "")
        rows.append(f"| `{name}` | {_type(s)} | {present} | {_escape(desc)} |")
    return rows


def _signature(name: str, schema: dict) -> str:
    req = schema.get("required", [])
    return f"{name}(" + ", ".join(p if p in req else p + "?" for p in schema.get("properties", {})) + ")"


def render(tools: list[dict]) -> str:
    """The generated part of docs/TOOLS.md, from tools/list (each tool as a dict with inputSchema/outputSchema)."""
    outputs = [t["outputSchema"] for t in tools if t["name"] not in NOT_DATA]
    # fields every data answer carries the same way (credits, as_of, note): described once
    first = outputs[0].get("properties", {})
    common = [f for f in first if all(o.get("properties", {}).get(f) == first[f] for o in outputs)
              and all((f in o.get("required", [])) == (f in outputs[0].get("required", [])) for o in outputs)]
    common_schema = {"properties": {f: first[f] for f in common},
                     "required": [f for f in outputs[0].get("required", []) if f in common]}
    shapes: dict[str, dict] = {}
    for t in tools:
        for name, d in t["outputSchema"].get("$defs", {}).items():
            if shapes.setdefault(name, d) != d:
                raise SystemExit(f"two different shapes are both called {name}: rename one in answers.py")

    lines = [BEGIN, "", "## Reference", "",
             "Generated from the server's `tools/list`: arguments from each tool's `inputSchema`, answer fields from "
             "its `outputSchema`. `?` marks an optional argument. **Present**: `always` (required in the schema), "
             '`optional` (left out when empty or not relevant), `detail="full"` (only in the full answer). Every '
             "answer object may carry more fields than listed.", "", "```text"]
    lines += [_signature(t["name"], t["inputSchema"]) for t in tools]
    lines += ["```", "", "### Fields of every answer", "",
              "Every answer except that of " + ", ".join(f"`{n}`" for n in sorted(NOT_DATA)) + " has these, with the "
              "same meaning; the tables below leave them out.", ""]
    used: set = set()
    lines += _fields(common_schema, "*", used)
    for t in tools:
        lines += ["", f"### `{t['name']}`", "", f"**{t.get('title', t['name'])}.** {_escape(t.get('description', ''))}", ""]
        lines += _arguments(t["inputSchema"])
        lines += ["", "Answer:", ""]
        lines += _fields(t["outputSchema"], t["name"], used, set() if t["name"] in NOT_DATA else set(common))
    lines += ["", "### Shapes inside answers", ""]
    for name in sorted(shapes):
        lines += [f"#### `{name}`", ""] + _fields(shapes[name], name, used) + [""]
    lines.append(END)
    stale = sorted(set(NOTES) - used)
    if stale:
        raise SystemExit(f"NOTES names fields that are not published: {stale}")
    return "\n".join(lines) + "\n"


def splice(doc: str, generated: str) -> str:
    """doc with the part between the markers (markers included) replaced by `generated`."""
    head, sep, rest = doc.partition(BEGIN)
    _, sep2, tail = rest.partition(END)
    if not (sep and sep2):
        raise SystemExit(f"{DOC} has no generated part: add the markers\n{BEGIN}\n{END}")
    return head + generated.rstrip("\n") + tail


def published_tools() -> list[dict]:
    """tools/list of a server on the synthetic test cities, as a client receives it."""
    import tempfile

    import anyio
    sys.path.insert(0, str(ROOT / "tests"))
    from fixture import build_fixture, build_national_fixture
    from mcp import Client

    from poland_transit_mcp.city import City
    from poland_transit_mcp.pool import CityPool
    from poland_transit_mcp.server import create_server

    with tempfile.TemporaryDirectory() as tmp:
        city = City.open(build_fixture(Path(tmp) / "testowo"), "testowo")
        alfa, beta, nat = build_national_fixture(Path(tmp) / "national")
        server = create_server(CityPool({"testowo": city, "alfa": alfa, "beta": beta, "poland": nat}, 1000))

        async def go():
            async with Client(server) as c:
                return (await c.list_tools()).tools
        return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in anyio.run(go)]


def main() -> int:
    doc = DOC.read_text(encoding="utf-8")
    new = splice(doc, render(published_tools()))
    if "--check" in sys.argv:
        if new != doc:
            print(f"{DOC.relative_to(ROOT)} is out of date: run uv run python scripts/gen_tools_doc.py")
            return 1
        print("up to date")
        return 0
    DOC.write_text(new, encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
