"""The shape of every tool's answer, published as the tool's `outputSchema` (MCP 2025-06-18 and later) and returned
as `structuredContent`, with the same JSON as text for clients that read text only.

The shapes name the fields an agent or a program needs; every object also allows more fields, so an answer can
grow without breaking a client. A key that is sometimes left out is not required: answers drop empty (None)
values. Points are `[lat, lon]`.

Answers are lean by default: what only code needs (line geometry, trip ids, colours, the coordinates of a leg's ends,
the full data dates) comes with `detail="full"`. Each such field is not required and its description says so.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import ConfigDict, Field
from typing_extensions import NotRequired, TypedDict


def _clean(schema: dict, *_: Any) -> None:
    """No generated titles: smaller on the wire. (Models never read an outputSchema; clients use it to check answers
    and code generators to type them.)"""
    schema.pop("title", None)
    if schema.get("additionalProperties") is True:
        del schema["additionalProperties"]  # true is JSON Schema's default: more fields are allowed anyway
    for p in schema.get("properties", {}).values():
        p.pop("title", None)


CONFIG = ConfigDict(extra="allow", json_schema_extra=_clean)
Point = list[float]
FULL = 'only with detail="full"'


def full(t: Any, what: str = "") -> Any:
    """A field an answer carries only with detail="full" (`what` says more)."""
    return NotRequired[Annotated[t, Field(description=f"{what}; {FULL}" if what else FULL)]]


class Answer(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    credits: list[str]  # ids of the data used (osm, <city>/<feed>): their full text from the credits tool
    as_of: Annotated[dict[str, Any], Field(description=(
        'publication date per source (YYYY-MM-DD or "unknown"; gtfs_<key>: credit <city>/<key>); detail="full": '
        '{published, retrieved, valid_from, valid_to} per feed'))]
    note: NotRequired[str]


class Named(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    name: str
    at: Point
    stop_id: NotRequired[str]
    platform: NotRequired[str]
    track: NotRequired[str]
    city: NotRequired[str]


class LegEnd(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    name: str
    at: full(Point)
    stop_id: NotRequired[str]  # for departures
    platform: NotRequired[str]
    track: NotRequired[str]
    city: full(str)


# "from" is a Python keyword: the functional form gives it as a field name.
_Ends = TypedDict("_Ends", {"from": Named, "to": Named})
_LegEnds = TypedDict("_LegEnds", {"from": LegEnd, "to": LegEnd})


class Leg(_LegEnds):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    mode: str  # walk, bus, tram, rail, metro, trolleybus, ferry
    depart: NotRequired[str]  # HH:MM, or YYYY-MM-DD HH:MM on another day
    arrive: NotRequired[str]
    minutes: NotRequired[int]
    distance_m: NotRequired[int]
    line: NotRequired[str]
    operator: NotRequired[str]
    direction: NotRequired[str]
    train: NotRequired[str]
    category: Annotated[NotRequired[str], Field(description='left out when it repeats line, unless detail="full"')]
    status: Annotated[NotRequired[str], Field(description='"scheduled" ' + FULL + "; frequency-based: estimated")]
    color: full(str)
    stops: NotRequired[int]  # stops passed on this leg
    trip_id: full(str)
    path: full(list[Point])


class Itinerary(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    depart: str
    arrive: str
    duration_min: int
    transfers: int
    legs: list[Leg]
    walk_m: NotRequired[int]
    trip: NotRequired[str]  # pass to render_map to draw this trip


class CityEntry(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    city: str  # the id other tools take
    name: str
    features: dict[str, Any]
    kind: NotRequired[str]
    bbox: NotRequired[list[float]]
    timetable: NotRequired[dict[str, Any]]  # feeds with their credit ids and dates
    built_at: NotRequired[str]  # when this data file was built, UTC (YYYY-MM-DDTHH:MM:SSZ)


class ListCities(Answer):
    cities: list[CityEntry]


class Place(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    name: str
    kind: str
    at: Point
    stop_ids: NotRequired[list[str]]  # a stop: its platforms' ids, for departures


class FindPlaces(Answer):
    places: list[Place]


class NearStop(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    stop_id: str
    name: str
    at: Point
    walk_min: float
    lines: list[str]


class StopsNear(Answer):
    point: Point
    date: NotRequired[str]
    stops: list[NearStop]


class Departure(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    time: str  # HH:MM local; with `date` when not on the day asked
    line: str
    direction: str
    status: NotRequired[str]  # scheduled, or live (then `scheduled` holds the timetable time)
    trip_id: full(str)
    color: full(str)
    date: NotRequired[str]
    mode: NotRequired[str]
    operator: NotRequired[str]
    stop_id: NotRequired[str]
    platform: NotRequired[str]
    track: NotRequired[str]
    train: NotRequired[str]  # national layer: the train's number or name, and its category
    category: NotRequired[str]


class Departures(Answer):
    at: NotRequired[str]  # the date and time the board starts from
    departures: list[Departure]
    live: NotRequired[str]  # whether live estimates were used
    stop: NotRequired[dict[str, Any]]  # name, at and the stop_ids asked about


class LineInfo(Answer):
    date: NotRequired[str]
    line: NotRequired[str]
    directions: Annotated[NotRequired[list[dict[str, Any]]], Field(description=(
        'a city line: stop names, first and last run, frequency; detail="full": stops as {stop_id, name}, and `path`'))]
    color: full(str)
    train: NotRequired[str]
    runs: Annotated[NotRequired[list[dict[str, Any]]], Field(description="a train's stops with times; `trip_id`, `path` " + FULL)]


class WalkRoute(Answer, _Ends):
    distance_m: NotRequired[int]
    minutes: NotRequired[int]
    path: NotRequired[list[Point]]


class PlanTrip(Answer, _Ends):
    itineraries: list[Itinerary]
    depart_at: NotRequired[str]
    arrive_by: NotRequired[str]
    assumptions: NotRequired[list[str]]


class StationTimeAnswer(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    stop: str
    minutes: int


_StationEnds = TypedDict("_StationEnds", {"from": list[StationTimeAnswer], "to": list[StationTimeAnswer]})


class StationItinerary(Itinerary):
    start: dict[str, Any]  # the start stop used: stop, name, stop_id, minutes
    end: dict[str, Any]


class PlanStations(Answer, _StationEnds):
    itineraries: list[StationItinerary]
    depart_at: NotRequired[str]
    arrive_by: NotRequired[str]


class MapExtract(Answer):
    bbox: list[float]
    layers: Annotated[dict[str, Any], Field(description=(
        "per layer: lines as [[lat, lon], ...]; polygons as lists of rings (outer parts and holes), filled even-odd; "
        "stops as {id, name, at}"))]


class RenderMap(Answer):
    format: str  # png: the image follows the text block; svg: the source is in `svg`
    image: NotRequired[str]  # where the PNG is: the next content block
    width: NotRequired[int]
    height: NotRequired[int]
    svg: NotRequired[str]


class CreditEntry(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    id: str
    text: str  # the credit to show: maker, licence, links
    licence: NotRequired[str]
    city: NotRequired[str]


class Credits(TypedDict):
    __pydantic_config__ = CONFIG  # type: ignore[misc]
    credits: list[CreditEntry]
    notice: str  # shown with the timetable credits
    about: str
    note: NotRequired[str]
