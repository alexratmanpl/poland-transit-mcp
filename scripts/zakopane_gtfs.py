"""Builds a GTFS feed for Zakopane's city buses from a transcription of the city's PDF timetables.

The city publishes lines 10–18 only as image PDFs (https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy).
Two independent transcriptions of every page (JSON, one file per page) are compared cell by cell
before this script runs; it reads one of them and refuses to run if the other differs.

  python scripts/zakopane_gtfs.py [--data-dir data] [--start 2026-11-15] [--end 2027-02-28]

Inputs (all under data/, none of them in git):
  raw/zakopane/transcribe/A/*.json, raw/zakopane/transcribe/B/*.json   the two transcriptions
  work/zakopane/hw.geojsonseq                                           street geometry (from the city build)
  cities/zakopane.sqlite                                                places (landmarks) from the city build
Output: raw/zakopane/Z/ (a GTFS directory the city build imports) and raw/zakopane/Z-report.txt.

What it decides, and says so in the report and in the feed:
- Stop positions come in three tiers: an OpenStreetMap stop with the same (or a clearly equivalent)
  name; a landmark the stop is named after (approximate); a point on the named street between the
  neighbouring stops, in proportion to the timetable's minutes (estimated). Every stop's tier is in
  the report and in stop_desc.
- A printed time that is clearly a misprint (out of order, or an hour or more away from what the
  stop-to-stop minutes of every other trip imply) is left out; the importer interpolates that stop.
- Footnote marks become dates: a trip runs on the union of its "runs" marks (or every day if it has
  none), minus its "does not run" marks. The PDFs state no validity dates, so the dates cover
  --start..--end and the feed says that this window is assumed.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from poland_transit_mcp.shared_db import connect_read_only

# ---- names: printed variants -> one canonical stop -------------------------------------------
VARIANTS = {
    "Bachledy — PKL": "Bachledy - PKL", "Pardałówka I": "Pardałówka 1", "Pardałówka II": "Pardałówka 2",
    "Pardałówa 2": "Pardałówka 2", "Karłowicza skrzyżowanie": "Karłowicza Skrzyżowanie",
    "Rówień Krupowa": "Równia Krupowa", "Zamoyskiego Watra": "Watra",
}
# tier 1: OpenStreetMap stops (public_transport/highway=bus_stop) under another spelling of the same name
OSM_STOP = {
    "Dworzec st. 16": "Rejon Dworca st. 16", "Dworzec st. 17": "Rejon Dworca st. 17", "Dworzec st. 18": "Zakopane Dworzec",
    "Bachledy - PKL": "Bachledy PKL", "Bogdańskiego do Doliny Strążyskiej": "Bogdańskiego",
    "Bogdańskiego do centrum": "Bogdańskiego", "Do Wantuli": "Strążyska - Do Wantuli",
    "Regle do Doliny Strążyskiej": "Regle", "Guty Muzeum Kasprowicza": "Guty Museum Kasprowicza",
    "Krzeptówki": "Zakopane Krzeptówki", "Mała Łąka": "Zakopane Mała Łąka", "Skibówki II": "Zakopane Skibówki II",
    "Spyrkówka I": "Zakopane Spyrkówka", "Ustup": "Zakopane Ustup", "Chramcówki": "Zakopane ul. Chramcówki",
    "Kasprowicza": "Zakopane ul. Kasprowicza", "Olcza Mrowce": "Mrowce", "Olcza Huty": "Huty",
    "Skocznia": "B. Czecha Skocznia", "Kuźnice": "Peron 1",
}
# tier 2: the landmark a stop is named after (OSM places in the city file)
LANDMARK = {
    "Murowanica": "Hotel Murowanica",
    "Szymony Teatr im. St. Witkiewicza": "Teatr im. Stanisława Ignacego Witkiewicza - Scena Witkacego",
    "Tetmajera Muzeum K. Makuszyńskiego": "Muzeum Kornela Makuszyńskiego w willi Opolanka",
    "Chałubińskiego Rondo": "Pomnik Tytusa Chałubińskiego",
    "Piłsudskiego „Start”": "Ośrodek Wypoczynkowy Start",
    "Grunwaldzka Ornak": "Willa Ornak",
    "Cyrhla Kościół": "Kościół pw. Miłosierdzia Bożego w Zakopanem (Cyrhla)",
    "Olcza Kościół": "Kościół pw. Najświętszej Maryi Panny Niepokalanej Objawiającej Cudowny Medalik",
}
# tier 3: only the street is known
STREET = {
    "Aleje 3-go Maja Dolne": ["Aleja 3 Maja"], "Aleje 3-go Maja Górne": ["Aleja 3 Maja"],
    "Br. Czecha COS": ["Bronisława Czecha"], "Grunwaldzka Z.S. Hotelarskich": ["Grunwaldzka"],
    "Kasprusie ZCE": ["Kasprusie"], "Olcza Stachonie": ["Stachonie"], "Walkosze 1": ["Walkosze"],
    "Walkosze 2": ["Walkosze"], "Hrube": ["Hrube Niżne", "Hrube Wyżne"], "Hrube Niżne": ["Hrube Niżne"],
    "Hrube Wyżne": ["Hrube Wyżne"], "Gawlaki": ["Gawlaki"], "Za Strugiem": ["Za Strugiem"],
    "Makuszyńskiego": ["Kornela Makuszyńskiego"], "Skibówki I": ["Skibówki"],
    "Przewodników Tatrzańskich Rondo": ["Przewodników Tatrzańskich"], "Watra": ["Władysława Zamoyskiego"],
    "Krupówki Pomnik hr. Wł. Zamoyskiego": ["Krupówki"], "Targowica": [], "Równia Krupowa": [],
}

# ---- footnote marks (legends verbatim in the transcription) ---------------------------------------
RANGES = {  # (from MM-DD, to MM-DD) inclusive; a range may wrap the new year
    "Q": [("05-01", "09-30")], "n": [("07-01", "08-31")],
    "ł": [("05-01", "05-03"), ("07-01", "08-31"), ("12-24", "01-06")],
    "s": [("09-01", "06-30")], "y": [("09-01", "06-30")],
    "q": [("12-24", "01-06"), ("05-01", "05-03")], "g": [("12-24", "12-24")],
    "w": [("07-01", "08-31"), ("12-24", "01-06")],
}
WEEKDAYS = {"s": {0, 1, 2, 3, 4}, "y": {5, 6}}
RUNS, NOT_RUNS, MODIFIERS = {"Q", "n", "ł", "s", "y"}, {"q", "g"}, {"w", "&"}
W_SKIPPED = {"Nowotarska Szkoła", "Aleje 3-go Maja Dolne", "Równia Krupowa"}


def in_ranges(d: date, ranges) -> bool:
    md = d.strftime("%m-%d")
    return any((a <= md <= b) if a <= b else (md >= a or md <= b) for a, b in ranges)


def mark_applies(m: str, d: date) -> bool:
    return in_ranges(d, RANGES[m]) and d.weekday() in WEEKDAYS.get(m, set(range(7)))


def run_dates(marks: set[str], days: list[date]) -> list[date]:
    unknown = marks - RUNS - NOT_RUNS - MODIFIERS
    if unknown:
        sys.exit(f"unknown footnote marks {unknown}")
    pos, neg = marks & RUNS, marks & NOT_RUNS
    return [d for d in days if (not pos or any(mark_applies(m, d) for m in pos)) and not any(mark_applies(m, d) for m in neg)]


# ---- geometry helpers ------------------------------------------------------------------------
def dist(a, b) -> float:
    k = math.cos(math.radians(a[0]))
    return 111320 * math.hypot(a[0] - b[0], (a[1] - b[1]) * k)


def to_min(t: str) -> int | None:
    parts = t.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]) * 60 + int(parts[1])


def clean_times(times: list[str], usual: list[int]) -> tuple[list[int | None], list[int]]:
    """Minutes per stop (None = not served or misprinted) and the indexes left out as misprints."""
    ms = [to_min(t) if t not in ("—", "–", "-", "") else None for t in times]
    served = [i for i, t in enumerate(times) if t not in ("—", "–", "-", "")]
    bad = [i for i in served if ms[i] is None]  # e.g. "7:9:38"
    # anchor on the stop whose time agrees with the most other stops given the usual gaps
    cum = [0]
    for g in usual:
        cum.append(cum[-1] + g)
    ok = [i for i in served if ms[i] is not None]
    best = max(ok, key=lambda a: sum(1 for b in ok if abs((ms[b] - ms[a]) - (cum[b] - cum[a])) < 30))
    for i in ok:
        if abs((ms[i] - ms[best]) - (cum[i] - cum[best])) >= 30:
            bad.append(i)
    # order errors smaller than 30 min (e.g. 14:24, 14:35, 14:27): drop the cell that breaks the order
    good = [i for i in ok if i not in bad]
    for j in range(1, len(good) - 1):
        a, b, c = ms[good[j - 1]], ms[good[j]], ms[good[j + 1]]
        if b > c and a <= c:
            bad.append(good[j])
    for i in bad:
        ms[i] = None
    return ms, sorted(set(bad))


def read_osm_stops(work: Path) -> dict[str, list[tuple[float, float]]]:
    """Named bus stops and platforms from the city's clipped OSM extract (made by the city build)."""
    pbf, gj = work / "stops.osm.pbf", work / "stops.geojsonseq"
    subprocess.run(["osmium", "tags-filter", str(work / "city.osm.pbf"), "nw/highway=bus_stop", "nw/public_transport=platform",
                    "--overwrite", "-o", str(pbf)], check=True, capture_output=True)
    subprocess.run(["osmium", "export", str(pbf), "-f", "geojsonseq", "--overwrite", "-o", str(gj)], check=True, capture_output=True)
    out: dict[str, list[tuple[float, float]]] = {}
    for line in gj.open(encoding="utf-8"):
        line = line.lstrip("\x1e").strip()
        if not line:
            continue
        f = json.loads(line)
        name, g = f["properties"].get("name"), f["geometry"]
        if not name:
            continue
        c = g["coordinates"]
        pts = {"Point": lambda: [c], "LineString": lambda: c, "Polygon": lambda: c[0], "MultiPolygon": lambda: c[0][0]}[g["type"]]()
        out.setdefault(name, []).append((sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--start", default="2026-11-15")
    ap.add_argument("--end", default="2027-02-28")
    a = ap.parse_args()
    root = Path(a.data_dir)
    tdir = root / "raw" / "zakopane" / "transcribe"
    out = root / "raw" / "zakopane" / "Z"
    out.mkdir(parents=True, exist_ok=True)
    report: list[str] = []

    pages = {}
    for f in sorted((tdir / "A").glob("*.json")):
        other = tdir / "B" / f.name
        if not other.exists() or json.loads(other.read_text("utf-8")) ["blocks"] != json.loads(f.read_text("utf-8"))["blocks"]:
            sys.exit(f"{f.name}: the two transcriptions differ or B is missing; reconcile them first")
        pages[f.stem] = json.loads(f.read_text("utf-8"))
    report.append(f"pages: {len(pages)} (A and B identical cell by cell)")

    # ---- stop positions --------------------------------------------------------------------
    city = connect_read_only(root / "cities" / "zakopane.sqlite")
    osm_stops = read_osm_stops(root / "work" / "zakopane")
    streets: dict[str, list[tuple[float, float]]] = {}
    for line in (root / "work" / "zakopane" / "hw.geojsonseq").open(encoding="utf-8"):
        line = line.lstrip("\x1e").strip()
        if not line:
            continue
        f = json.loads(line)
        name, g = f["properties"].get("name"), f["geometry"]
        if name and g["type"] == "LineString":
            streets.setdefault(name, []).extend((c[1], c[0]) for c in g["coordinates"])
    places = {n: (la, lo) for n, la, lo in city.execute("SELECT name, lat, lon FROM places")}

    def canon(n: str) -> str:
        return VARIANTS.get(n, n)

    # sequences of canonical stops with usual minutes between them, from every block
    seqs = []
    for pid, p in pages.items():
        for b in p["blocks"]:
            names = [canon(s["name"]) for s in b["stops"]]
            gaps = []
            for i in range(len(names) - 1):
                g = [to_min(t["times"][i + 1]) - to_min(t["times"][i]) for t in b["trips"]
                     if to_min(t["times"][i]) is not None and to_min(t["times"][i + 1]) is not None
                     and 0 <= to_min(t["times"][i + 1]) - to_min(t["times"][i]) < 30]
                gaps.append(statistics.median(g) if g else 2)
            seqs.append((pid, names, gaps))

    pos: dict[str, tuple[float, float]] = {}
    tier: dict[str, str] = {}
    all_names = sorted({n for _, names, _ in seqs for n in names})
    for n in all_names:
        key = OSM_STOP.get(n, n)
        if key in osm_stops:
            pts = osm_stops[key]
            pos[n] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            tier[n] = f"osm stop '{key}'" + (f" (mean of {len(pts)} platforms)" if len(pts) > 1 else "")
        elif n in LANDMARK and LANDMARK[n] in places:
            pos[n] = places[LANDMARK[n]]
            tier[n] = f"approximate: landmark '{LANDMARK[n]}'"

    def estimate(n: str) -> tuple[float, float] | None:
        """Interpolated position between the nearest placed neighbours, by timetable minutes."""
        cands = []
        for _, names, gaps in seqs:
            for i, x in enumerate(names):
                if x != n:
                    continue
                lo = next((j for j in range(i - 1, -1, -1) if names[j] in pos), None)
                hi = next((j for j in range(i + 1, len(names)) if names[j] in pos), None)
                if lo is None or hi is None:
                    continue
                t_lo, t_hi = sum(gaps[lo:i]), sum(gaps[i:hi])
                f = t_lo / (t_lo + t_hi) if t_lo + t_hi else 0.5
                A, B = pos[names[lo]], pos[names[hi]]
                cands.append((A[0] + f * (B[0] - A[0]), A[1] + f * (B[1] - A[1])))
        if not cands:
            return None
        return (sum(c[0] for c in cands) / len(cands), sum(c[1] for c in cands) / len(cands))

    for _ in range(3):  # a few passes: estimates can depend on each other
        for n in all_names:
            if n in pos and not tier[n].startswith("estimated"):
                continue
            guess = estimate(n)
            if guess is None:
                continue
            on = [p for s in STREET.get(n, []) for p in streets.get(s, [])]
            if on:
                pos[n] = min(on, key=lambda p: dist(p, guess))
                tier[n] = f"estimated: on '{' / '.join(STREET[n])}' between the neighbouring stops"
            else:
                pos[n] = guess
                tier[n] = "estimated: between the neighbouring stops (no street or landmark of that name)"
    missing = [n for n in all_names if n not in pos]
    if missing:
        sys.exit(f"no position for {missing}")

    report.append("\nstops (canonical name; tier):")
    for n in all_names:
        report.append(f"  {n}: {tier[n]} {pos[n][0]:.5f},{pos[n][1]:.5f}")
    report.append("\nchecks: stop-to-stop distance against the timetable's minutes (flag > 40 km/h or > 1.5 km for 1 min):")
    for pid, names, gaps in seqs:
        for i in range(len(names) - 1):
            d = dist(pos[names[i]], pos[names[i + 1]])
            mins = max(gaps[i], 0.5)
            if d / (mins * 60) * 3.6 > 40:
                report.append(f"  {pid}: {names[i]} -> {names[i+1]}: {d:.0f} m in {gaps[i]} min")

    # ---- trips, dates, stop times -------------------------------------------------------------
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    days = [d0 + timedelta(n) for n in range((d1 - d0).days + 1)]
    stop_id = {n: f"Z{i:03d}" for i, n in enumerate(all_names, 1)}
    services: dict[tuple, str] = {}
    trips, stop_times, routes = [], [], set()
    dropped = []
    for pid, p in sorted(pages.items()):
        line = p["line"]
        routes.add(line)
        for bi, b in enumerate(p["blocks"], 1):
            names = [s["name"] for s in b["stops"]]
            gaps = [g for (q, _, g) in [s for s in seqs if s[0] == pid]][bi - 1]
            for ti, t in enumerate(b["trips"], 1):
                ms, bad = clean_times(t["times"], [round(g) for g in gaps])
                for i in bad:
                    dropped.append(f"{pid} block {bi} trip {t['times'][0]} stop {i+1} {names[i]}: printed {t['times'][i]!r}")
                marks = set(t["mark"].split())
                dates = run_dates(marks, days)
                variants = [(dates, set())]
                if "w" in marks:
                    wd = [d for d in dates if mark_applies("w", d)]
                    variants = [([d for d in dates if d not in wd], set()), (wd, W_SKIPPED)]
                for vi, (vdates, skip) in enumerate(variants):
                    if not vdates:
                        continue
                    key = tuple(sorted(vdates))
                    sid = services.setdefault(key, f"S{len(services) + 1}")
                    tid = f"{line}-{pid[4:]}-{bi}-{ti}" + (f"-w" if skip else "")
                    served = [i for i, x in enumerate(t["times"]) if x not in ("—", "–", "-", "") and names[i] not in skip]
                    if len(served) < 2:
                        continue
                    last = canon(names[served[-1]])
                    trips.append((line, sid, tid, last))
                    for seq, i in enumerate(served, 1):
                        hh = "" if ms[i] is None else f"{ms[i] // 60:02d}:{ms[i] % 60:02d}:00"
                        if seq in (1, len(served)) and not hh:
                            hh = None  # first/last stop must be timed
                        if hh is None:
                            break
                        stop_times.append((tid, hh, hh, stop_id[canon(names[i])], seq))
    report.append(f"\nmisprinted cells left out ({len(dropped)}); the importer interpolates those stops:")
    report += [f"  {x}" for x in dropped]
    w = lambda name, rows: (out / name).write_text("\n".join(",".join(f'"{c}"' if "," in str(c) else str(c) for c in r) for r in rows) + "\n", encoding="utf-8")
    w("agency.txt", [["agency_id", "agency_name", "agency_url", "agency_timezone"],
                     ["ZKP", "Komunikacja miejska Zakopane (Gmina Miasto Zakopane)", "https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy", "Europe/Warsaw"]])
    w("stops.txt", [["stop_id", "stop_name", "stop_lat", "stop_lon", "stop_desc"]] +
      [[stop_id[n], n, f"{pos[n][0]:.6f}", f"{pos[n][1]:.6f}", tier[n].replace(",", ";")] for n in all_names])
    w("routes.txt", [["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"]] +
      [[r, "ZKP", r, "", 3] for r in sorted(routes, key=int)])
    w("trips.txt", [["route_id", "service_id", "trip_id", "trip_headsign"]] + [list(t) for t in trips])
    w("stop_times.txt", [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]] + [list(s) for s in stop_times])
    w("calendar_dates.txt", [["service_id", "date", "exception_type"]] +
      [[sid, d.strftime("%Y%m%d"), 1] for key, sid in services.items() for d in key])
    w("feed_info.txt", [["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"],
                        ["poland-transit-mcp (transcription of the city's PDF timetables)", "https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy",
                         "pl", d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d"), f"transcribed; dates assumed {a.start}..{a.end}"]])
    report.insert(1, f"trips {len(trips)}, stop times {len(stop_times)}, services {len(services)}, dates {a.start}..{a.end} (assumed)")
    (root / "raw" / "zakopane" / "Z-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:3]))
    print(f"report: {root / 'raw' / 'zakopane' / 'Z-report.txt'}")


if __name__ == "__main__":
    main()
