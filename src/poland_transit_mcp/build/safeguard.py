"""Build safeguards: a new city file replaces the last good one only when no timetable got worse.

A rebuild can go wrong without failing: a feed's file is missing or unreadable (for example its first download
failed, or a local input is gone), a server sends a truncated or nearly empty feed, or a feed ends soon. Each of these
used to replace a good city file with a worse one. Before the new file replaces the current one in the data dir, its
feeds are compared with the current file's feeds:

- a feed that was `ok` and is not `ok` now (missing, broken, skipped), or
- a feed whose stored runs fell below MIN_RUNS_RATIO of the current file's

stops the replacement: the current file stays, the new one is kept beside it as `<city>.sqlite.rejected` for a look,
and the build exits with EXIT_REJECTED. `--force` replaces anyway. A feed of the current file that has already ended
is not protected (there is nothing left to keep), and a feed removed from the config is not compared. After writing,
feeds that are not `ok` or end within WARN_DAYS are named. Without a current file (a first build, or a fresh
machine) nothing is compared, so a build job should start from its last published file.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

MIN_RUNS_RATIO = 0.5  # a feed with fewer than half the runs of the last good file is treated as gutted
WARN_DAYS = 14  # warn when a feed ends within this many days
EXIT_REJECTED = 3


def read_feeds(path: Path) -> list[dict] | None:
    """The `feeds` record of a city file, or None when there is no readable file."""
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key = 'feeds'").fetchone()
        finally:
            con.close()
        feeds = json.loads(row[0]) if row else []
    except (sqlite3.Error, ValueError):
        return None  # an unreadable current file gives nothing to compare with
    return feeds if isinstance(feeds, list) and all(isinstance(f, dict) for f in feeds) else None


def _day(yyyymmdd) -> date | None:
    if not isinstance(yyyymmdd, int) or yyyymmdd <= 0:
        return None
    return date(yyyymmdd // 10000, yyyymmdd // 100 % 100, yyyymmdd % 100)


def worse_feeds(old: list[dict] | None, new: list[dict], today: date | None = None) -> list[str]:
    """Why the new feeds are worse than the current file's, one line per feed; empty when they are not."""
    if old is None:
        return []
    today = today or datetime.now().date()
    by_key = {f.get("key"): f for f in old}
    out = []
    for f in new:
        before = by_key.get(f.get("key"))
        if not before or before.get("status") != "ok":
            continue
        ended = _day(before.get("end_date"))
        if ended and ended < today:
            continue  # the current file's timetable has ended: nothing left to protect
        name = f"{f.get('name')} ({f.get('key')})"
        if f.get("status") != "ok":
            out.append(f"{name}: was ok in the current file, now {f.get('status')}")
            continue
        runs, runs_before = f.get("runs"), before.get("runs")
        if isinstance(runs, int) and isinstance(runs_before, int) and runs_before > 0 \
                and runs < MIN_RUNS_RATIO * runs_before:
            out.append(f"{name}: {runs} runs, down from {runs_before} in the current file "
                       f"(below {MIN_RUNS_RATIO:.0%}): the feed looks gutted")
    return out


def expiry_warnings(feeds: list[dict], today: date | None = None, days: int = WARN_DAYS) -> list[str]:
    """Feeds that are not ok, or end within `days` (or already ended)."""
    today = today or datetime.now().date()
    out = []
    for f in feeds:
        name = f"{f.get('name')} ({f.get('key')})"
        if f.get("status") != "ok":
            out.append(f"{name}: {f.get('status')}, so this file has no timetable from it")
            continue
        end_day = _day(f.get("end_date"))
        if end_day is None:
            continue
        left = (end_day - today).days
        if left < 0:
            out.append(f"{name}: ended on {end_day.isoformat()}; rebuild with a newer feed")
        elif left <= days:
            out.append(f"{name}: ends on {end_day.isoformat()}, in {left} days; rebuild before then")
    return out
