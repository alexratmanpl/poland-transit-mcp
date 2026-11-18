"""The boundary table: for each tool, its valid call (contract.VALID) and every change the published schema suggests
(a missing required argument, a wrong type, one step below and above every bound, a value outside an enum, null,
NaN, Infinity), down into lists and nested objects. Each changed call is judged twice, by the schema and by the
server, and three rules must hold:

1. the schema refuses it => the server refuses it, in a readable line that names the argument;
2. the schema accepts it => an answer in the declared shape, or a readable refusal, never a crash;
3. the server never answers what the schema refuses, except where contract.LOOSER says why.
"""
import json
import math
from typing import Any, Iterator

import jsonschema
import pytest
from contract import LOOSER, VALID, contract_server, published, readable, schema_accepts, session, text_of  # noqa: F401

MISSING = object()
WRONG = [12345, 1.5, "text", True, [1], {"a": 1}]
JSON_TYPE = {int: "integer", float: "number", str: "string", bool: "boolean", list: "array", dict: "object"}


def _types(s: dict) -> set[str]:
    t = s.get("type")
    return {t} if isinstance(t, str) else set(t or ())


def example(s: dict) -> Any:
    """A small value the node accepts."""
    if "enum" in s:
        return s["enum"][0]
    if "anyOf" in s:
        return example(s["anyOf"][0])
    t = s.get("type")
    if t == "string":
        return "a" * max(s.get("minLength", 1), 1)
    if t in ("integer", "number"):
        lo = s.get("minimum", s.get("exclusiveMinimum", 0) + 1 if "exclusiveMinimum" in s else 0)
        hi = s.get("maximum", lo)
        v = (lo + hi) / 2 if t == "number" else int(lo)
        return v
    if t == "array":
        return [example(s.get("items", {}))] * s.get("minItems", 1)
    if t == "object":
        return {k: example(p) for k, p in s.get("properties", {}).items() if k in s.get("required", ())}
    return None


def variants(s: dict, v: Any, path: str) -> Iterator[tuple[str, Any]]:
    """(label, changed value) for the node `s` whose valid value is `v`."""
    if "anyOf" in s:
        for option in s["anyOf"]:
            fits = jsonschema.Draft202012Validator(option).is_valid(v)
            yield from variants(option, v if fits else example(option), path)
        return
    for w in WRONG:
        if JSON_TYPE[type(w)] not in _types(s) and not (type(w) is int and "number" in _types(s)):
            yield f"{path}: wrong type {type(w).__name__}", w
    if "enum" in s:
        yield f"{path}: outside the enum", "not-an-option"
    t = s.get("type")
    if t == "string":
        if "maxLength" in s:
            yield f"{path}: at maxLength", "a" * s["maxLength"]
            yield f"{path}: over maxLength", "a" * (s["maxLength"] + 1)
        if s.get("minLength", 0) > 0:
            yield f"{path}: under minLength", "a" * (s["minLength"] - 1)
    elif t in ("integer", "number"):
        for key, step, word in (("minimum", -1, "below"), ("maximum", 1, "above")):
            if key in s:
                yield f"{path}: at {key}", s[key]
                yield f"{path}: {word} {key}", s[key] + step
        if "exclusiveMinimum" in s:
            yield f"{path}: at exclusiveMinimum", s["exclusiveMinimum"]
        yield f"{path}: NaN", math.nan
        yield f"{path}: Infinity", math.inf
        yield f"{path}: -Infinity", -math.inf
        if t == "integer":
            yield f"{path}: a fraction", (v if isinstance(v, int) else example(s)) + 0.5
            yield f"{path}: a whole-number float", float(v if isinstance(v, int) else example(s))
    elif t == "array":
        items = s.get("items", {})
        v = v if isinstance(v, list) and v else example(s) or [example(items)]
        if "maxItems" in s:
            yield f"{path}: over maxItems", [v[0]] * (s["maxItems"] + 1)
        if s.get("minItems", 0) > 0:
            yield f"{path}: under minItems", v[: s["minItems"] - 1]
        for label, item in variants(items, v[0], f"{path}[0]"):
            yield label, [item, *v[1:]]
    elif t == "object":
        v = v if isinstance(v, dict) else example(s)
        for name, sub in s.get("properties", {}).items():
            where = f"{path}.{name}" if path else name
            if name in s.get("required", ()):
                yield f"{where}: missing", {k: x for k, x in v.items() if k != name}
            yield f"{where}: null ({'required' if name in s.get('required', ()) else 'optional'})", {**v, name: None}
            for label, x in variants(sub, v.get(name, MISSING) if name in v else example(sub), where):
                yield label, {**v, name: x}


def looser(name: str, label: str, required: list[str]) -> str | None:
    """The reason the server may answer this call although the schema refuses it. NaN and Infinity reach the server
    as null on this transport (the client writes JSON, where they do not exist); raw NaN in an HTTP body is the wire
    test's (test_contract_wire.py)."""
    arg, _, what = label.partition(": ")
    top = "." not in arg and "[" not in arg
    nulled = what == "null (optional)" or (what in ("NaN", "Infinity", "-Infinity") and arg not in required)
    kind = "null-optional" if top and nulled else what
    for key in ((name, arg, kind), (name, "*", kind), ("*", "*", kind)):
        if key in LOOSER:
            return LOOSER[key]
    return None


@pytest.mark.parametrize("name", sorted(VALID))
def test_the_server_keeps_to_its_published_schema(contract_server, published, name):
    schema, output = published[name].input_schema, published[name].output_schema
    seen, problems, count = set(), [], 0
    with session(contract_server) as (call, _, _c):
        for label, args in variants(schema, VALID[name], ""):
            if not isinstance(args, dict):  # the arguments themselves are always an object (the client sees to that)
                continue
            key = json.dumps(args, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            count += 1
            r, _ = call(name, args)
            text = text_of(r)
            arg = label.split(":")[0].split(".")[0].split("[")[0]
            if not schema_accepts(schema, args):
                if not r.is_error:
                    if not looser(name, label, schema.get("required", [])):
                        problems.append(f"{label}: the schema refuses it, the server answered")
                elif not readable(name, text) or (arg and arg not in text):
                    problems.append(f"{label}: refused without naming {arg!r}: {text[:160]}")
            elif r.is_error:
                if not readable(name, text):
                    problems.append(f"{label}: a crash or an unreadable refusal: {text[:160]}")
            else:
                errors = sorted(jsonschema.Draft202012Validator(output).iter_errors(r.structured_content), key=str)
                if errors:
                    problems.append(f"{label}: answer outside the outputSchema: {errors[0].message[:160]}")
    assert count > 5 or not schema["properties"], count
    assert not problems, f"{len(problems)} of {count} calls:\n" + "\n".join(problems)
