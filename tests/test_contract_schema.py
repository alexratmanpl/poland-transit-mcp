"""The published tools/list is the contract. These tests read it as a client receives it and check that it says
everything a caller needs (every text, list and number bounded), that it does not change unseen (a golden copy), and
that the SDK internals the server adjusts to publish it are still where the server expects them."""
import json
import os
from pathlib import Path

import pytest
from contract import KEYWORDS, VALID, contract_server, dumps, published, readable, session, text_of, walk  # noqa: F401

from poland_transit_mcp.server import _BriefErrors, _TextStaysText

GOLDEN = Path(__file__).parent / "golden" / "tools_list.json"

# Nodes that may be open (no `additionalProperties: false`), each with its reason. Paths as `walk` writes them.
OPEN = {
    ("render_map", "style"): "keys are layer names; bounded by maxProperties and propertyNames, values by maxLength",
}


def test_every_string_list_and_number_is_bounded(published):
    """Each string has maxLength (or is an enum), each array maxItems, each number a lower and an upper bound, each
    object refuses unknown keys. A schema that says this is one a caller can check before sending."""
    problems = []
    for name, tool in published.items():
        for where, node in walk(tool.input_schema):
            kind = node.get("type")
            if kind == "string" and "enum" not in node and "maxLength" not in node:
                problems.append(f"{name}.{where}: string without maxLength")
            elif kind == "array" and "maxItems" not in node:
                problems.append(f"{name}.{where}: array without maxItems")
            elif kind in ("number", "integer"):
                if not {"minimum", "exclusiveMinimum"} & node.keys():
                    problems.append(f"{name}.{where}: {kind} without a lower bound")
                if not {"maximum", "exclusiveMaximum"} & node.keys():
                    problems.append(f"{name}.{where}: {kind} without an upper bound")
            elif kind == "object" and node.get("additionalProperties") is not False and (name, where) not in OPEN:
                problems.append(f"{name}.{where or '(arguments)'}: object open to unknown keys")
    assert not problems, "\n".join(problems)


def test_only_json_schema_keywords_are_published(published):
    """A keyword a client does not know is a bound it cannot see (pydantic writes "ge"/"le" when a constraint follows
    a validator)."""
    found = [f"{name}.{where}: {sorted(set(node) - KEYWORDS)}" for name, tool in published.items()
             for where, node in walk(tool.input_schema) if set(node) - KEYWORDS]
    assert not found, "\n".join(found)


def test_the_open_list_names_only_nodes_that_exist(published):
    nodes = {(name, where) for name, tool in published.items() for where, _ in walk(tool.input_schema)}
    assert set(OPEN) <= nodes, set(OPEN) - nodes


def _golden(published) -> str:
    return dumps([t.model_dump(mode="json", by_alias=True, exclude_none=True)
                  for t in sorted(published.values(), key=lambda t: t.name)]) + "\n"


def test_tools_list_matches_the_golden_copy(published):
    """The whole tools/list (names, titles, descriptions, input and output schemas, annotations) as a client receives
    it. A change by the code, the SDK or pydantic shows up here as a diff to review. After a deliberate change:

        POLAND_TRANSIT_GOLDEN=update uv run pytest tests/test_contract_schema.py -k golden
    """
    now = _golden(published)
    if os.environ.get("POLAND_TRANSIT_GOLDEN") == "update":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(now, encoding="utf-8")
    assert GOLDEN.exists(), "no golden copy yet: run with POLAND_TRANSIT_GOLDEN=update"
    assert now == GOLDEN.read_text(encoding="utf-8"), \
        "tools/list changed; review the diff, then POLAND_TRANSIT_GOLDEN=update"


def test_every_tool_has_a_valid_call(published):
    assert set(VALID) == set(published), "add a row to tests/contract.py VALID for each new tool"


@pytest.mark.parametrize("name", sorted(VALID))
def test_each_valid_call_answers_in_its_declared_shape(contract_server, published, name):
    import jsonschema
    with session(contract_server) as (call, _, _c):
        r, _ = call(name, VALID[name])
    assert not r.is_error, text_of(r)
    jsonschema.validate(r.structured_content, published[name].output_schema)


# ---------------------------------------------------------------- the SDK parts the server adjusts

def test_the_sdk_parts_the_server_adjusts_are_still_there(contract_server, published):
    """`_strict_arguments` reaches into the SDK: `_tool_manager.list_tools()`, each tool's `fn_metadata.arg_model`
    and `parameters`. If an SDK release moves them, this fails before a caller notices."""
    tools = contract_server._tool_manager.list_tools()
    assert {t.name for t in tools} == set(published)
    for t in tools:
        assert isinstance(t, _BriefErrors), t.name
        assert isinstance(t.fn_metadata, _TextStaysText), t.name
        assert hasattr(t.fn_metadata.arg_model, "model_fields"), t.name
        assert "refuse_unknown" in t.fn_metadata.arg_model.__pydantic_decorators__.model_validators, t.name
        assert t.parameters == published[t.name].input_schema, f"{t.name}: tools/list does not send tool.parameters"
        assert t.parameters.get("additionalProperties") is False, t.name


@pytest.mark.parametrize("name", sorted(VALID))
def test_every_tool_refuses_an_unknown_argument_by_name(contract_server, name):
    with session(contract_server) as (call, _, _c):
        r, _ = call(name, {**VALID[name], "zz_not_an_argument": 1})
    text = text_of(r)
    assert r.is_error and readable(name, text) and "zz_not_an_argument" in text, text

