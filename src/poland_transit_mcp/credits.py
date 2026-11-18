"""Credits: who made the data, under which licence. Answers carry only short ids (`credits`) and the data dates
(`as_of`); the full text of every id is one set, built from the loaded data files and offered three ways: the
`credits` tool, the MCP resource `credits://all`, and `poland-transit-mcp credits` (a JSON file to publish next to
the data). Ids: `osm` for OpenStreetMap, `<city>/<feed key>` for a timetable feed, `<city>/live` for live estimates.
"""
from __future__ import annotations

from collections.abc import Iterable

from .city import City
from .live import LIVE_CREDITS
from .text import quoted

URI = "credits://all"
# some publishers (e.g. ZTM Poznań) require saying that their data was processed
PROCESSED = ("Timetables processed by poland-transit-mcp (feeds merged, shapes simplified); check the operator for "
             "changes")
ABOUT = ("Answers name their data by these ids in `credits`, with the data dates in `as_of`. Show the text of each "
         "id, and this notice, wherever the data is shown to people.")


def feed_id(city: City, feed: dict) -> str:
    return f'{city.id}/{feed["key"]}'


def feed_text(feed: dict) -> str:
    extra = "".join(f'; {c["role"]}: {c["name"]}' + (f' <{c["url"]}>' if c.get("url") else "")
                    for c in feed.get("credits", ()))
    return f'{feed["name"]} — {feed["attribution"]} ({feed["licence"]}){extra}'


def entries(cities: Iterable[City]) -> list[dict]:
    """One entry per id, in the order the files list them."""
    out: dict[str, dict] = {}
    for c in cities:
        if c.osm.get("attribution"):
            out.setdefault("osm", {"id": "osm", "text": c.osm["attribution"]})
        for f in c.feeds:
            if f["status"] == "ok":
                out[feed_id(c, f)] = {"id": feed_id(c, f), "text": feed_text(f), "name": f["name"],
                                      "licence": f.get("licence"), "city": c.id}
        live = LIVE_CREDITS.get(c.cfg.live) if c.cfg and c.cfg.live else None
        if live:
            out[f"{c.id}/live"] = {"id": f"{c.id}/live", "text": live, "city": c.id}
    return list(out.values())


def credit_set(cities: Iterable[City], ids: list[str] | None = None, brief: bool = False) -> dict:
    """The full set, or only the ids asked for (and which of them are unknown). `brief` (the credits tool) gives each
    entry as its id and the text to show: name, licence and city are in the text and the id, and a model pays for
    every repeated word. The resource and the published file keep every field."""
    cities = list(cities)
    every = entries(cities)
    if brief:
        every = [{"id": e["id"], "text": e["text"]} for e in every]
    about = {} if brief else {"about": ABOUT}
    if ids is None:
        return {"credits": every, "notice": PROCESSED, **about}
    # a city id stands for every source of that city: a caller can ask before it has an answer's ids
    of_city = {c.id: [e["id"] for e in entries([c])] for c in cities}
    ids = [x for i in ids for x in of_city.get(i, [i])]
    known = {e["id"] for e in every}
    body = {"credits": [e for e in every if e["id"] in ids], "notice": PROCESSED, **about}
    unknown = [i for i in ids if i not in known]
    if unknown:  # the caller's own text: a few of them, each cut, never the whole list back
        more = f" and {len(unknown) - 5} more" if len(unknown) > 5 else ""
        body["note"] = (f"unknown ids: {', '.join(quoted(i) for i in unknown[:5])}{more} "
                        f"(this server has: {', '.join(sorted(known))})")
    return body
