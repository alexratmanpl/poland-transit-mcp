"""docs/TOOLS.md's reference part is generated from tools/list (scripts/gen_tools_doc.py): it must match what the
server publishes now, so the documented arguments and answer fields cannot drift from the code."""
import importlib.util
from pathlib import Path

from contract import contract_server, published  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("gen_tools_doc", ROOT / "scripts" / "gen_tools_doc.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_tools_doc_is_up_to_date(published):  # noqa: F811
    tools = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in published.values()]
    doc = gen.DOC.read_text(encoding="utf-8")
    assert gen.splice(doc, gen.render(tools)) == doc, \
        "docs/TOOLS.md is out of date: run uv run python scripts/gen_tools_doc.py"


def test_every_tool_has_a_section(published):  # noqa: F811
    doc = gen.DOC.read_text(encoding="utf-8")
    assert all(f"### `{name}`" in doc for name in published)
