# Contributing

Thanks for helping. Start with [AGENTS.md](AGENTS.md): it takes you from an empty machine to a working server and
explains the code map. It is written for AI coding agents and works just as well for people.

## Before you open a pull request

```bash
uv sync --extra dev --extra fast      # drop --extra fast if you have no Rust toolchain
uv run pytest                         # must pass
uv run python scripts/smoke.py        # if you have city data built
```

- Keep changes small and focused, with a test for each fix or feature.
- Never commit data: `data/`, `*.sqlite`, `*.pbf`, `*.zip` and `.mcp.json` are gitignored for a reason.
- Changes to the search must give the same answers in Python and Rust (`scripts/compare_engines.py`).

## Adding or changing a tool

Every tool follows one rule ([docs/TOOLS.md](docs/TOOLS.md#lean-by-default-more-on-demand) has it in full):

- The default answer holds what a person asking the question needs, complete enough that the usual question takes
  one call, and keeps every id another tool takes as input.
- More comes only through the three shared kinds of argument: `detail` (`summary` or `full`, for every field code
  needs), `limit` (more rows) and filters (a narrower question). Use those names; `full` adds fields and removes none.
- Add a knob only where more is large: at least about 1.5 times the answer, or about 500 tokens (1,000 characters).
  Below that, the field stays in the default or stays out. Every knob is text a model reads and a chance to call
  the tool wrong.
- An answer that was cut says so in `note`, with how to get the rest. An answer that was not cut says nothing.
- Every argument has a description. Where one call can grow several times, the description says by how much, in
  relative terms ("2 to 3 times larger"), measured on real data, never as a price per row or in tokens.
- `tests/test_contract_answer_rule.py` checks the rule, and the size of what a model loads; after a deliberate
  change to `tools/list`, review the golden copy's diff (`POLAND_TRANSIT_GOLDEN=update`), and regenerate the
  reference in [docs/TOOLS.md](docs/TOOLS.md) with `uv run python scripts/gen_tools_doc.py` (`tests/test_tools_doc.py`
  fails until you do).

## Adding a city or a feed

1. Read the feed's reuse terms first. Record the licence and the required credit.
2. Add the city or feed to `src/poland_transit_mcp/config.py`.
3. Add a section to [SOURCES.md](SOURCES.md) (URL, licence, size, quirks) and a row to
   [DATA-LICENSE.md](DATA-LICENSE.md) (licence, credit, whether a prebuilt file may be published).
4. Build it, run the smoke test, and check a few departures against the operator's own timetable
   ([docs/CHECKS.md](docs/CHECKS.md) shows how).

A data set that does not belong in this repository (another country's trains, a private feed) needs no change to
`config.py`: define it in a JSON file and build it with `poland-transit-mcp build ID --datasets FILE` (or
`POLAND_TRANSIT_DATASETS=FILE`). The format is in `src/poland_transit_mcp/datasets.py`. Unknown keys, values of the wrong
type and the ids of built-in data sets are refused. A city data set needs a regional extract of Poland (the OSM
downloads are Polish only); a national layer needs no map. The build writes the data set's name, time zone, notes and feed credits into the
file, so a server loads it without the definitions file. Its time zone must keep Polish time (for example
`Europe/Prague`): the build refuses a feed whose clock differs, and the server refuses such a file.

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md).
