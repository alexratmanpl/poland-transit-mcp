"""Europe/Warsaw local time. GTFS times are seconds after the service day's midnight and may
exceed 24:00 for night services. Dates are ints YYYYMMDD."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True)
class LocalTime:
    date: int
    secs: int


def to_local(dt: datetime) -> LocalTime:
    d = dt.astimezone(TZ)
    return LocalTime(d.year * 10000 + d.month * 100 + d.day, d.hour * 3600 + d.minute * 60 + d.second)


def now_local() -> LocalTime:
    return to_local(datetime.now(TZ))


_HM = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_DT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_OFFSET = re.compile(r"([zZ]|[+-]\d{2}:?\d{2})$")


def parse_when(s: str | None, now: LocalTime | None = None) -> LocalTime | None:
    """'HH:MM' (today), 'YYYY-MM-DD[ HH:MM]' (local) or ISO 8601 with Z/offset."""
    now = now or now_local()
    if not s:
        return now
    t = s.strip()
    m = _HM.match(t)
    if m:
        return LocalTime(now.date, int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3] or 0))
    if _OFFSET.search(t):
        try:
            return to_local(datetime.fromisoformat(t.replace("Z", "+00:00").replace("z", "+00:00")))
        except ValueError:
            return None
    m = _DT.match(t)
    if m:
        secs = int(m[4]) * 3600 + int(m[5]) * 60 + int(m[6] or 0) if m[4] else 0
        return LocalTime(int(m[1] + m[2] + m[3]), secs)
    return None


def parse_date(s: str | None, now: LocalTime | None = None) -> int | None:
    if not s:
        return (now or now_local()).date
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", s.strip())
    return int(m[1] + m[2] + m[3]) if m else None


def _to_date(d: int) -> Date:
    return Date(d // 10000, d // 100 % 100, d % 100)


def add_days(d: int, n: int) -> int:
    x = _to_date(d) + timedelta(days=n)
    return x.year * 10000 + x.month * 100 + x.day


def weekday(d: int) -> int:
    """0 = Monday ... 6 = Sunday."""
    return _to_date(d).weekday()


def fmt_date(d: int) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def fmt_hm(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}"


def fmt_wall(d: int, secs: int) -> tuple[str, str]:
    """Service day + GTFS seconds → (wall-clock date, HH:MM), normalising times over 24:00."""
    extra = secs // 86400
    return fmt_date(add_days(d, extra)), fmt_hm(secs - extra * 86400)


def parse_gtfs_time(s: str) -> int:
    if not s:
        return -1
    p = s.strip().split(":")
    if len(p) < 2:
        return -1
    return int(p[0]) * 3600 + int(p[1]) * 60 + (int(p[2]) if len(p) > 2 and p[2] else 0)
