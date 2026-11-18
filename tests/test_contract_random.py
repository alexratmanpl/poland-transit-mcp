"""Random valid input: arguments drawn from each tool's published input schema (hypothesis-jsonschema). Whatever the
schema allows must come back as an answer in the declared shape or as a readable refusal, never as a crash, within
a time limit and a size limit. This is the test that finds the next input that freezes the server.

25 examples per tool by default; set POLAND_TRANSIT_CONTRACT_EXAMPLES=200 for the full run."""
import json
import os

import jsonschema
import pytest
from contract import VALID, contract_server, published, readable, session, text_of  # noqa: F401
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

EXAMPLES = int(os.environ.get("POLAND_TRANSIT_CONTRACT_EXAMPLES", "25"))
SECONDS = 2.0  # one call, the city already loaded
CHARACTERS = 600_000  # one answer (an SVG map at the largest size is the biggest; real cities stay under 1 MB)


def arguments(schema: dict, valid: dict):
    """Drawn from the schema, with some arguments taken from the tool's valid call (a loaded city, a real stop name),
    so most calls reach the tool and not only the "unknown city" refusal."""
    drawn = from_schema(schema)
    if not valid:
        return drawn
    keep = st.sets(st.sampled_from(sorted(valid)))
    return st.tuples(drawn, keep).map(lambda p: {**p[0], **{k: valid[k] for k in p[1]}})


@pytest.mark.parametrize("name", sorted(VALID))
def test_whatever_the_schema_allows_is_answered_or_refused_in_a_line(contract_server, published, name):
    tool = published[name]
    problems: list[str] = []
    with session(contract_server) as (call, _, _c):
        call(name, VALID[name])  # load the cities first: the time limit is for a warm call

        @settings(max_examples=EXAMPLES, deadline=None, database=None, derandomize=True,
                  suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                                         HealthCheck.filter_too_much, HealthCheck.large_base_example])
        @given(arguments(tool.input_schema, VALID[name]))
        def check(args):
            r, seconds = call(name, args)
            text = text_of(r)
            shown = json.dumps(args, ensure_ascii=False)[:200]
            if seconds > SECONDS:
                problems.append(f"{seconds:.1f} s: {shown}")
            if len(text) > CHARACTERS:
                problems.append(f"{len(text)} characters: {shown}")
            if r.is_error:
                if not readable(name, text):
                    problems.append(f"crash or unreadable refusal {text[:120]!r}: {shown}")
            else:
                error = next(jsonschema.Draft202012Validator(tool.output_schema).iter_errors(r.structured_content), None)
                if error is not None:
                    problems.append(f"answer outside the outputSchema ({error.message[:120]}): {shown}")

        check()
    assert not problems, "\n".join(problems[:20])
