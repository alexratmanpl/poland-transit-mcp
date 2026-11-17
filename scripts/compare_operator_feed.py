"""Compares the national layer's trains (PKP PLK data via mkuran.pl) with an operator's own GTFS on one
date: trains are matched by number, and times compared at stations with the same name.

  python scripts/compare_operator_feed.py <operator feed .zip> <YYYYMMDD> [--data-dir data/cities]

Used for docs/CHECKS.md ("Trips between cities"). Prints how many trains matched and how many stop times
are identical, with a few differences as examples.
"""
import collections
import csv
import io
import sys
import zipfile
from datetime import date as D
from pathlib import Path
from poland_transit_mcp.city import City
from poland_transit_mcp.text import fold

zpath, day = sys.argv[1], int(sys.argv[2])
data = Path(sys.argv[sys.argv.index("--data-dir") + 1]) if "--data-dir" in sys.argv else Path(__file__).resolve().parents[1] / "data" / "cities"
z = zipfile.ZipFile(zpath)
def rows(n):
    if n not in z.namelist():
        return []
    return list(csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig")))
def gt(s):
    h, m, *_ = s.split(":"); return int(h) * 60 + int(m)
on = set()
wd = D(day // 10000, day // 100 % 100, day % 100)
for r in rows("calendar.txt"):
    if int(r["start_date"]) <= day <= int(r["end_date"]) and r[wd.strftime("%A").lower()] == "1":
        on.add(r["service_id"])
for r in rows("calendar_dates.txt"):
    if int(r["date"]) == day:
        (on.add if r["exception_type"] == "1" else on.discard)(r["service_id"])
stops = {r["stop_id"]: r["stop_name"] for r in rows("stops.txt")}
trips = {r["trip_id"]: r["trip_short_name"].split()[0] for r in rows("trips.txt") if r["service_id"] in on and r.get("trip_short_name")}
op = collections.defaultdict(list)
for r in rows("stop_times.txt"):
    if r["trip_id"] in trips:
        op[r["trip_id"]].append((int(r["stop_sequence"]), fold(stops[r["stop_id"]]), gt(r["arrival_time"]), gt(r["departure_time"])))

nat = City.open(data, "poland")
tt = nat.tt
nday = tt.day(day)
by_num = collections.defaultdict(list)
for t in nday.trips:
    if t.service_date != day:
        continue
    info = tt.trip_details(t.trip_idx)
    if info and info["train"]:
        num = info["train"].split()[0]
        for n in {num, num.split("/")[0]}:
            by_num[n].append(t)
matched = unmatched = compared = same = dep_same = dep_1 = 0
diffs = collections.Counter()
examples = []
for tid, num in trips.items():
    cands = by_num.get(num) or by_num.get(num.split("/")[0])
    if not cands:
        unmatched += 1
        continue
    seq = sorted(op[tid])
    names = {s[1] for s in seq}
    best = max(cands, key=lambda t: len(names & {fold(tt.stops[s].name) for s in t.pattern.stops}))
    nat_times = {}
    for pos, s in enumerate(best.pattern.stops):
        nat_times.setdefault(fold(tt.stops[s].name), (best.arr[pos] // 60, best.dep[pos] // 60))
    common = [s for s in seq if s[1] in nat_times]
    if len(common) < 2:
        unmatched += 1
        continue
    matched += 1
    for _, name, a, d in common:
        na, nd = nat_times[name]
        compared += 1
        delta = nd - d if d is not None else 0
        dep_same += nd == d
        dep_1 += abs(nd - d) <= 1
        if na == a and nd == d:
            same += 1
        else:
            diffs[max(abs(na - a), abs(nd - d))] += 1
            if len(examples) < 6:
                examples.append((num, name, f"{a // 60:02d}:{a % 60:02d}/{d // 60:02d}:{d % 60:02d}", f"{na // 60:02d}:{na % 60:02d}/{nd // 60:02d}:{nd % 60:02d}"))
print(f"{Path(zpath).name} on {day}: {len(trips)} trains in the operator feed; matched by number {matched}, not matched {unmatched}")
print(f"  stop times compared: {compared}; identical {same} ({100 * same / max(compared, 1):.1f} %)")
print(f"  departures identical {100 * dep_same / max(compared, 1):.1f} %, within 1 min {100 * dep_1 / max(compared, 1):.1f} %")
print(f"  differences by minutes: {sorted(diffs.items())[:10]}")
for e in examples:
    print("   e.g. train", e[0], e[1], "operator", e[2], "national layer", e[3])
