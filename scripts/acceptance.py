"""Prints the acceptance-check queries (departures + trip plans) for docs/CHECKS.md.

  python scripts/acceptance.py [--date YYYY-MM-DD] [--data-dir data/cities]

Without --date, each city is queried on the first day its timetable file is valid (City.valid_from).
docs/CHECKS.md used --date 2026-11-16.
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from poland_transit_mcp.city import City
from poland_transit_mcp.plan import plan_trip
from poland_transit_mcp.timeutil import parse_when
from poland_transit_mcp.transit import resolve_stops, scheduled_departures

ap = argparse.ArgumentParser()
ap.add_argument("--date", help="YYYY-MM-DD (default: the first day each city's timetable is valid)")
ap.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / "data" / "cities"))
a = ap.parse_args()

CHECKS = {
    "gdansk": {"departures": [("Brama Wyżynna", "08:00", "8"), ("Dworzec Główny", "13:00", "106"), ("Oliwa", "17:30", "6")],
               "trips": [("main station → old town", "Dworzec PKP Gdańsk Główny", "Ratusz Głównego Miasta", "09:00"),
                         ("trip needing a transfer", "Katedra Oliwska", "Westerplatte", "10:00"),
                         ("late night after 23:30", "Dworzec Główny", "Zaspa", "23:40")]},
    "krakow": {"departures": [("Teatr Bagatela", "08:00", "18"), ("Rondo Mogilskie", "12:00", "9"), ("Czerwone Maki P+R", "22:30", "11")],
               "trips": [("main station → old town", "Kraków Główny", "Rynek Główny", "09:00"),
                         ("trip needing a transfer", "Wieliczka Rynek-Kopalnia", "Bronowice Małe", "10:00"),
                         ("late night after 23:30", "Rynek Główny", "Plac Centralny im. R. Reagana", "23:40")]},
    "wroclaw": {"departures": [("Plac Grunwaldzki", "08:00", "16"), ("Galeria Dominikańska", "12:00", "D"), ("Dworzec Główny", "22:30", "23")],
                "trips": [("main station → old town", "Wrocław Główny", "Rynek", "09:00"),
                          ("trip needing a transfer", "Port Lotniczy", "Hala Stulecia", "10:00"),
                          ("late night after 23:30", "Rynek", "Kozanów", "23:40")]},
}

for cid, chk in CHECKS.items():
    if not (Path(a.data_dir) / f"{cid}.sqlite").exists():
        continue
    c = City.open(Path(a.data_dir), cid)
    date = a.date or c.valid_from or datetime.date.today().isoformat()
    feeds = "; ".join(f'{f["key"]}: {f["status"]}' + (f', published {f["last_modified"]}' if f.get("last_modified") else "") for f in c.feeds)
    print(f"\n## {cid}  (OSM {c.osm['timestamp']}; {feeds})")
    for stop, time_, line in chk["departures"]:
        if not c.has_transit:
            print(f"departures {stop}: {c.transit_note()}")
            continue
        deps = scheduled_departures(c, resolve_stops(c, stop), parse_when(f"{date} {time_}"), line, 3)
        shown = "; ".join(f'{d["line"]} {(d["date"] + " ") if d["date"] else ""}{d["time"]} → {d["direction"]} '
                          f'[{d["stop_id"]}{" / " + d["platform"] if d["platform"] else ""}]' for d in deps) or "none"
        ops = sorted({d["operator"] for d in deps if d.get("operator")})
        print(f"departures {stop} {date} from {time_}{' line ' + line if line else ''}: {shown}"
              + (f" (operator: {', '.join(ops)})" if ops else ""))
    for label, frm, to, time_ in chk["trips"]:
        r = plan_trip(c, frm, to, depart_at=f"{date} {time_}")
        print(f'trip "{label}": {r["from"]["name"]} → {r["to"]["name"]}, depart {date} {time_}' + (f' — {r["note"]}' if r.get("note") else ""))
        for it in r["itineraries"]:
            legs = " | ".join(f'walk {l["minutes"]} min' if l["mode"] == "walk" else
                              f'{l["mode"]} {l["line"]} [{l.get("operator") or "?"}] "{l["direction"]}" {l["from"]["name"]} {l["depart"]} → {l["to"]["name"]} {l["arrive"]}'
                              for l in it["legs"])
            print(f'  {it["depart"]}→{it["arrive"]} ({it["duration_min"]} min, {it["transfers"]} transfers): {legs}')
