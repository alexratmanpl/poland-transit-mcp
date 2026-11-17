"""Generates the README demo assets from a real run of the server over stdio:
  docs/demo-plan-trip.svg  animated terminal recording of a plan_trip call
  docs/trip-gdansk.svg     render_map of the first itinerary, default style

  python scripts/demo.py [--date 2026-11-16] [--data-dir data/cities]
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--date", default="2026-11-16")
ap.add_argument("--data-dir", default=str(ROOT / "data" / "cities"))
a = ap.parse_args()
REQ = {"city": "gdansk", "from": "Archikatedra Oliwska", "to": "Westerplatte", "depart_at": f"{a.date} 10:00"}


async def run() -> tuple[dict, str]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "poland_transit_mcp", "--data-dir", a.data_dir, "--cities", "gdansk", "--no-download"])
    async with Client(params) as c:
        plan = json.loads((await c.call_tool("plan_trip", REQ)).content[0].text)
        it = next(i for i in plan["itineraries"] if any(l["mode"] != "walk" for l in i["legs"]))
        svg = json.loads((await c.call_tool("render_map", {"city": "gdansk", "itinerary": it, "width": 820})).content[0].text)["svg"]
    return plan, svg


plan, svg = anyio.run(run)
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "trip-gdansk.svg").write_text(svg, encoding="utf-8")

lines = [("dim", "# Claude calls the MCP tool:"), ("cmd", f"plan_trip {json.dumps(REQ, ensure_ascii=False)}"), ("", "")]
for i in plan["itineraries"][:2]:
    t = i["transfers"]
    lines.append(("hl", f'▸ {i["depart"]} → {i["arrive"]}   {i["duration_min"]} min · {t} transfer{"" if t == 1 else "s"} · {i["walk_m"]} m on foot'))
    for l in i["legs"]:
        if l["mode"] == "walk":
            lines.append(("walk", f'    walk {l["minutes"]:>2} min  {l["from"]["name"]} → {l["to"]["name"]}'))
        else:
            lines.append(("ride", f'    {l["mode"]:<4} {l["line"]:<4} {l["depart"]} {l["from"]["name"]} → {l["arrive"]} {l["to"]["name"]}  ({l["stops"]} stops, {l["time"]})'))
    lines.append(("", ""))
lines.append(("dim", "source: " + " · ".join(plan["source"])))
lines.append(("dim", "as_of:  " + " · ".join(f"{k}: {v}" for k, v in plan["as_of"].items())))
lines = [(c, t if len(t) <= 118 else t[:117] + "…") for c, t in lines]

W, LH = 960, 19
H = 46 + len(lines) * LH
step = 0.35
total = len(lines) * step + 6
body = []
for i, (cls, t) in enumerate(lines):
    begin = i * step / total
    body.append(f'<text class="{cls}" x="16" y="{40 + i * LH}" style="animation-name:l{i}">{escape(t)}</text>'
                f"<style>@keyframes l{i}{{0%,{begin * 100:.2f}%{{opacity:0}}{min(99.9, begin * 100 + 0.01):.2f}%,100%{{opacity:1}}}}</style>")
term = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n<style>\n'
        "text{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;fill:#d8dee9;"
        f"animation-duration:{total:.1f}s;animation-iteration-count:infinite;animation-fill-mode:both}}\n"
        ".cmd{fill:#a3be8c}.hl{fill:#ebcb8b;font-weight:bold}.walk{fill:#9aa5b8}.ride{fill:#88c0d0}.dim{fill:#7b8496}\n</style>\n"
        '<rect width="100%" height="100%" rx="8" fill="#2e3440"/>\n'
        '<circle cx="18" cy="14" r="5" fill="#bf616a"/><circle cx="34" cy="14" r="5" fill="#ebcb8b"/><circle cx="50" cy="14" r="5" fill="#a3be8c"/>\n'
        + "\n".join(body) + "\n</svg>\n")
(ROOT / "docs" / "demo-plan-trip.svg").write_text(term, encoding="utf-8")
print(f"wrote docs/trip-gdansk.svg ({len(svg)} B) and docs/demo-plan-trip.svg ({len(term)} B)")
