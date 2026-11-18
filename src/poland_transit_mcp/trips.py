"""Trip references: a short string that names one itinerary of a `plan_trip` answer, so a later call (render_map)
can get the trip again instead of the model copying the whole itinerary back.

A reference holds the planning call as it was answered, with its time made absolute (never "now"), the
itinerary's position, and a fingerprint of that itinerary (its times and the trips it rides). Reading it plans again
and checks the fingerprint, so new data or another day gives a clear "plan again", never a different trip. Nothing is
stored on the server, so it works across restarts and several server processes.

The form is readable, like a URL query (half the tokens of the base64 it replaced):
`t2?c=krakow&f=50.06165,19.93732&t=Wieliczka&d=2026-11-19T08:30:00&x=2&i=0&h=1a2b3c4d5e6f`, with `tc` (to_city)
and `l` (layer) for a trip between cities, and `a` instead of `d` for arrive_by.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, quote, urlencode

from .errors import Refused

PREFIX = "t2?"
MAX_LENGTH = 2000
TOOLS = ("plan_trip",)  # what render_map can draw
NOT_A_REFERENCE = "not a trip reference: pass the `trip` value of an itinerary from plan_trip"
_KEYS = {"c": "city", "f": "from", "t": "to", "d": "depart_at", "a": "arrive_by", "x": "max_transfers",
         "tc": "to_city", "l": "layer"}
_POINT = re.compile(r"^(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$")
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


def fingerprint(itinerary: dict) -> str:
    """The itinerary's times and the timetable trips it rides, hashed."""
    parts = [str(itinerary.get("depart")), str(itinerary.get("arrive"))]
    parts += [f'{leg.get("mode")}:{leg.get("trip_id", "")}' for leg in itinerary.get("legs", [])]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _place(p: object) -> str:
    return f"{p[0]},{p[1]}" if isinstance(p, list) else str(p)


def make(tool: str, args: dict, index: int, itinerary: dict) -> str:
    q = {"c": args["city"], "f": _place(args["from"]), "t": _place(args["to"])}
    if args.get("to_city"):
        q["tc"] = args["to_city"]
    if args.get("layer"):
        q["l"] = args["layer"]
    q["d" if "depart_at" in args else "a"] = (args.get("depart_at") or args["arrive_by"]).replace(" ", "T")
    q.update(x=args["max_transfers"], i=index, h=fingerprint(itinerary))
    return PREFIX + urlencode(q, safe=",:", quote_via=quote)


def read(ref: str) -> tuple[str, dict, int, str]:
    """(tool, args, index, fingerprint). Raises ValueError with a sentence an agent can act on."""
    if not isinstance(ref, str) or not ref.startswith(PREFIX) or len(ref) > MAX_LENGTH:
        raise Refused(NOT_A_REFERENCE)
    try:
        pairs = parse_qsl(ref[len(PREFIX):], keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    except ValueError:
        raise Refused(NOT_A_REFERENCE) from None
    q = dict(pairs)
    if len(q) != len(pairs) or not set(q) <= {*_KEYS, "i", "h"}:
        raise Refused(NOT_A_REFERENCE)
    whens = [k for k in ("d", "a") if k in q]
    if not ({"c", "f", "t", "x", "i", "h"} <= set(q) and len(whens) == 1 and _WHEN.fullmatch(q[whens[0]])
            and _ID.fullmatch(q["c"]) and all(_ID.fullmatch(q[k]) for k in ("tc", "l") if k in q)
            and q["x"] in ("0", "1", "2", "3", "4") and q["i"] in tuple("0123456789")
            and re.fullmatch(r"[0-9a-f]{12}", q["h"]) and all(0 < len(q[k]) <= 200 for k in ("f", "t"))
            and q.get("tc") != q["c"]):  # plan_trip never writes a trip "between" a city and itself
        raise Refused(NOT_A_REFERENCE)
    args: dict = {}
    for k, name in _KEYS.items():
        if k not in q:
            continue
        v = q[k]
        if k in ("f", "t"):
            m = _POINT.fullmatch(v)
            v = [float(m[1]), float(m[2])] if m else v
        elif k in ("d", "a"):
            v = v.replace("T", " ")
        elif k == "x":
            v = int(v)
        args[name] = v
    return "plan_trip", args, int(q["i"]), q["h"]
