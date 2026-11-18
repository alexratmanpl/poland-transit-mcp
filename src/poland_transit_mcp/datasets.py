"""Data sets defined in a file, beside the built-in Polish ones in config.py.

`poland-transit-mcp build ID --datasets FILE` (or POLAND_TRANSIT_DATASETS=FILE) builds a data set that is not in
config.py, e.g. another country's trains as a second national layer. The file is JSON:

    {"datasets": [{"id": "cz", "name": "Czechia: trains", "kind": "national", "timezone": "Europe/Prague",
                   "bbox": [48.5, 12.0, 51.1, 18.9],
                   "feeds": [{"key": "CZ", "name": "...", "url": "https://...", "licence": "...", "attribution": "..."}],
                   "notes": ["..."]}]}

Keys are the fields of CityConfig and Feed (config.py), with the same types; `kind` is "city" (needs `osm_region`,
a regional extract of Poland as Geofabrik names it: city data sets are Polish only) or "national". Unknown keys,
values of the wrong type and ids that are already built in are refused, so a typo never builds silently.

Only the build reads this file. What the server needs (name, kind, time zone, notes, feeds and their credits)
is written into the data file itself, so a server needs no definitions file to load it.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import types
import typing
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CITIES, CityConfig, Feed

ENV = "POLAND_TRANSIT_DATASETS"
_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_FEED_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]{0,7}")
_REGION = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _fields(cls) -> dict[str, dataclasses.Field]:
    return {f.name: f for f in dataclasses.fields(cls)}


def _required(cls) -> set[str]:
    return {n for n, f in _fields(cls).items()
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}


def _typed(v, t, where: str):
    """v as the field type t (str, bool, float, int, X | None, tuple[X, ...], tuple[X, Y]); JSON lists are tuples."""
    origin, args = typing.get_origin(t), typing.get_args(t)
    if origin in (typing.Union, types.UnionType):
        if v is None and type(None) in args:
            return None
        return _typed(v, next(a for a in args if a is not type(None)), where)
    if origin is tuple:
        n = None if args[-1] is Ellipsis else len(args)
        if not isinstance(v, list) or (n is not None and len(v) != n):
            raise ValueError(f"{where}: expected a list" + (f" of {n} values" if n else "") + f", got {json.dumps(v)}")
        return tuple(_typed(x, args[0] if n is None else args[i], f"{where}[{i}]") for i, x in enumerate(v))
    ok = {str: lambda x: isinstance(x, str), bool: lambda x: isinstance(x, bool),
          float: lambda x: isinstance(x, int | float) and not isinstance(x, bool),
          int: lambda x: isinstance(x, int) and not isinstance(x, bool)}[t]
    if not ok(v):
        raise ValueError(f"{where}: expected {t.__name__}, got {json.dumps(v)}")
    return float(v) if t is float else v


def _build(cls, raw: dict, where: str, skip: frozenset = frozenset()):
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object")
    known = set(_fields(cls)) - skip
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    missing = sorted((_required(cls) - skip) - set(raw))
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    hints = typing.get_type_hints(cls)
    return {k: v if k == "feeds" else _typed(v, hints[k], f"{where}.{k}") for k, v in raw.items()}


def _dataset(raw: dict, n: int) -> CityConfig:
    where = f"datasets[{n}]"
    kind = raw.get("kind", "city") if isinstance(raw, dict) else None
    if kind not in ("city", "national"):
        raise ValueError(f'{where}: kind must be "city" or "national"')
    body = {k: v for k, v in raw.items() if k != "kind"}
    if kind == "national":
        body.setdefault("osm_region", "")
    elif not body.get("osm_region"):
        raise ValueError(f"{where}: a city needs osm_region (the regional OpenStreetMap extract of Poland)")
    vals = _build(CityConfig, body, where, frozenset({"national", "live"}))
    cid = vals["id"]
    if not _ID.fullmatch(cid):
        raise ValueError(f"{where}: id must be lower-case letters, digits, - or _ (at most 32), got {cid!r}")
    if cid in CITIES:
        raise ValueError(f'{where}: "{cid}" is a built-in data set (config.py); pick another id')
    if kind == "city" and not _REGION.fullmatch(vals["osm_region"]):
        raise ValueError(f"{where}: osm_region must be a region name such as pomorskie, got {vals['osm_region']!r}")
    a, b, c, d = vals["bbox"]
    if not (-90 <= a < c <= 90 and -180 <= b < d <= 180):
        raise ValueError(f"{where}: bbox must be [min_lat, min_lon, max_lat, max_lon] with min < max")
    try:
        ZoneInfo(vals.get("timezone", "Europe/Warsaw"))
    except Exception:  # noqa: BLE001 - unknown or malformed zone names raise several types
        raise ValueError(f"{where}: unknown time zone {vals.get('timezone')!r}") from None
    if not isinstance(vals["feeds"], list) or not vals["feeds"]:
        raise ValueError(f"{where}: feeds must be a non-empty list")
    feeds = []
    for i, f in enumerate(vals["feeds"]):
        fw = f"{where}.feeds[{i}]"
        fv = _build(Feed, f, fw)
        if not _FEED_KEY.fullmatch(fv["key"]):
            raise ValueError(f"{fw}: key must be 1-8 letters or digits, starting with a letter")
        if not re.match(r"https?://", fv["url"]):
            raise ValueError(f"{fw}: url must start with http:// or https://")
        feeds.append(Feed(**fv))
    if len({f.key for f in feeds}) != len(feeds):
        raise ValueError(f"{where}: feed keys must differ")
    vals["feeds"] = tuple(feeds)
    return CityConfig(**vals, national=kind == "national")


def load(path: str | Path) -> dict[str, CityConfig]:
    """The data sets defined in a file, by id."""
    path = Path(path).expanduser()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read data set definitions {path}: {e}") from None
    if not isinstance(doc, dict) or set(doc) != {"datasets"} or not isinstance(doc["datasets"], list):
        raise ValueError(f'{path}: expected {{"datasets": [...]}}')
    out: dict[str, CityConfig] = {}
    for n, raw in enumerate(doc["datasets"]):
        d = _dataset(raw, n)
        if d.id in out:
            raise ValueError(f'{path}: data set "{d.id}" is defined twice')
        out[d.id] = d
    return out


def registry(path: str | Path | None = None) -> dict[str, CityConfig]:
    """The built-in data sets plus those of the file given (or named by POLAND_TRANSIT_DATASETS)."""
    path = path or os.environ.get(ENV)
    return {**CITIES, **load(path)} if path else dict(CITIES)
