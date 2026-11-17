"""Draws docs/engine-speed.svg: plan_trip p95, Python search vs Rust engine, per city.

  python scripts/engine_chart.py

The numbers are the output of `scripts/compare_engines.py --plans 100` on 2026-11-16 (in-process
plan_trip, 100 random plans per city on Monday 2026-11-16, both engines on the same queries).
Colours are validated for colour-vision deficiencies in light and dark mode.
"""
from pathlib import Path

ROWS = [  # city, Python p95, Rust p95 (ms)
    ("Warszawa", 375, 118),
    ("Katowice", 326, 85),
    ("Kraków", 149, 65),
    ("Gdańsk", 117, 44),
]
TARGET, MAX = 300, 400
W, LEFT, RIGHT, TOP = 680, 92, 40, 74
BAR, GAP, GROUP = 14, 2, 46
plot_w = W - LEFT - RIGHT
H = TOP + GROUP * len(ROWS) + 34


def x(ms: float) -> float:
    return LEFT + plot_w * ms / MAX


def bar(y: float, ms: float, cls: str) -> str:
    """Horizontal bar: square at the baseline, 4px rounded data end."""
    x0, x1, r = LEFT, x(ms), 4
    return (f'<path class="{cls}" d="M{x0},{y} H{x1 - r:.1f} Q{x1:.1f},{y} {x1:.1f},{y + r} V{y + BAR - r} '
            f'Q{x1:.1f},{y + BAR} {x1 - r:.1f},{y + BAR} H{x0} Z"/>')


parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img"
 aria-labelledby="t d" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif">
<title id="t">plan_trip p95 latency: Python search vs Rust engine</title>
<desc id="d">{'; '.join(f'{c}: Python {p} ms, Rust {r} ms' for c, p, r in ROWS)}. Target 300 ms.</desc>
<style>
  .bg {{ fill: #fcfcfb; }} .t1 {{ fill: #0b0b0b; }} .t2 {{ fill: #52514e; }}
  .grid {{ stroke: #e4e3df; }} .target {{ stroke: #52514e; }}
  .py {{ fill: #2a78d6; }} .rs {{ fill: #eb6834; }}
  @media (prefers-color-scheme: dark) {{
    .bg {{ fill: #1a1a19; }} .t1 {{ fill: #f0efec; }} .t2 {{ fill: #c3c2b7; }}
    .grid {{ stroke: #33322f; }} .target {{ stroke: #c3c2b7; }}
    .py {{ fill: #3987e5; }} .rs {{ fill: #d95926; }}
  }}
</style>
<rect class="bg" width="{W}" height="{H}" rx="8"/>
<text class="t1" x="{LEFT - 76}" y="28" font-size="15" font-weight="600">plan_trip, 95th percentile (lower is better)</text>
<g font-size="12">
  <rect class="py" x="{LEFT - 76}" y="42" width="12" height="12" rx="2"/><text class="t2" x="{LEFT - 58}" y="52">Python search</text>
  <rect class="rs" x="{LEFT + 30}" y="42" width="12" height="12" rx="2"/><text class="t2" x="{LEFT + 48}" y="52">Rust engine</text>
</g>''']
for ms in range(0, MAX + 1, 100):
    parts.append(f'<line class="grid" x1="{x(ms):.1f}" x2="{x(ms):.1f}" y1="{TOP - 6}" y2="{H - 30}" stroke-width="1"/>')
    parts.append(f'<text class="t2" x="{x(ms):.1f}" y="{H - 14}" font-size="11" text-anchor="middle">{ms} ms</text>')
parts.append(f'<line class="target" x1="{x(TARGET):.1f}" x2="{x(TARGET):.1f}" y1="{TOP - 10}" y2="{H - 30}" '
             f'stroke-width="1.5" stroke-dasharray="4 3"/>')
parts.append(f'<text class="t2" x="{x(TARGET) + 4:.1f}" y="{TOP - 12}" font-size="11">target 300 ms</text>')
for i, (city, py, rs) in enumerate(ROWS):
    y = TOP + i * GROUP
    parts.append(f'<text class="t1" x="{LEFT - 8}" y="{y + BAR + 5}" font-size="13" text-anchor="end">{city}</text>')
    parts.append(bar(y, py, "py"))
    parts.append(bar(y + BAR + GAP, rs, "rs"))
    parts.append(f'<text class="t2" x="{x(py) + 6:.1f}" y="{y + 11}" font-size="11">{py} ms</text>')
    parts.append(f'<text class="t1" x="{x(rs) + 6:.1f}" y="{y + BAR + GAP + 11}" font-size="11" font-weight="600">'
                 f'{rs} ms · {py / rs:.1f}× faster</text>')
parts.append("</svg>\n")
out = Path(__file__).resolve().parents[1] / "docs" / "engine-speed.svg"
out.write_text("\n".join(parts), encoding="utf-8")
print(out)
