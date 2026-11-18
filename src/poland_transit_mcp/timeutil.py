"""Europe/Warsaw local time. GTFS times are seconds after the service day's midnight and may
exceed 24:00 for night services. Dates are ints YYYYMMDD."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import Refused
from .text import quoted

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
_D = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_OFFSET = re.compile(r"([zZ]|[+-]\d{2}:?\d{2})$")
YEARS = (2000, 2100)  # the dates a timetable question can be about; others are refused, not answered for today
MAX_HOUR = 47  # a time on a service day can pass midnight (25:10 is 01:10 the next morning)
TIME_FORMS = ("use HH:MM or YYYY-MM-DD HH:MM (local time; up to 47:59 for the night after a day), or ISO 8601 with "
              f"an offset; years {YEARS[0]}-{YEARS[1]}")


def _day(y: str, m: str, d: str) -> int | None:
    """YYYYMMDD of a real calendar day within YEARS, or None."""
    try:
        day = Date(int(y), int(m), int(d))
    except ValueError:
        return None
    return day.year * 10000 + day.month * 100 + day.day if YEARS[0] <= day.year <= YEARS[1] else None


def _secs(h: str, m: str, s: str | None) -> int | None:
    h_, m_, s_ = int(h), int(m), int(s or 0)
    return h_ * 3600 + m_ * 60 + s_ if h_ <= MAX_HOUR and m_ < 60 and s_ < 60 else None


def parse_when(s: str | None, now: LocalTime | None = None) -> LocalTime:
    """None is now; 'HH:MM' (today), 'YYYY-MM-DD[ HH:MM]' (local) or ISO 8601 with Z/offset. Anything else,
    "" included, is a ValueError with one message naming the forms."""
    if s is None:
        return now or now_local()
    t = s.strip()
    bad = Refused(f"time {quoted(s)} is not a valid time: {TIME_FORMS}")
    m = _HM.fullmatch(t)
    if m:
        secs = _secs(m[1], m[2], m[3])
        if secs is None:
            raise bad
        return LocalTime((now or now_local()).date, secs)
    if _OFFSET.search(t):
        m = re.match(r"^(\d{4})-", t)
        if not m or not YEARS[0] <= int(m[1]) <= YEARS[1]:
            raise bad
        try:
            return to_local(datetime.fromisoformat(t.replace("Z", "+00:00").replace("z", "+00:00")))
        except (ValueError, OverflowError):
            raise bad from None
    m = _DT.fullmatch(t)
    if not m:
        raise bad
    day = _day(m[1], m[2], m[3])
    secs = _secs(m[4], m[5], m[6]) if m[4] else 0
    if day is None or secs is None:
        raise bad
    return LocalTime(day, secs)


def parse_date(s: str | None, now: LocalTime | None = None) -> int:
    """None is today; 'YYYY-MM-DD' (or YYYYMMDD) of a real day in YEARS; anything else, "" included, a ValueError."""
    if s is None:
        return (now or now_local()).date
    m = _D.fullmatch(s.strip())
    day = _day(m[1], m[2], m[3]) if m else None
    if day is None:
        raise Refused(f"date {quoted(s)} is not a valid date: use YYYY-MM-DD, years {YEARS[0]}-{YEARS[1]}")
    return day


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


def same_clock(zone: str, start: int, end: int) -> bool:
    """Whether a time zone keeps the same UTC offset as Europe/Warsaw at every hour from date start to date end
    (YYYYMMDD; at most about three years are checked). The server reads every timetable on Polish time, so a
    data file in another zone is usable only where the two clocks agree (e.g. Europe/Prague)."""
    try:
        other = ZoneInfo(zone)
    except Exception:  # noqa: BLE001 - an unknown or malformed zone name
        return False
    utc = ZoneInfo("UTC")
    t = datetime.combine(_to_date(start), datetime.min.time(), utc) - timedelta(days=1)
    stop = min(datetime.combine(_to_date(end), datetime.min.time(), utc) + timedelta(days=2), t + timedelta(days=1100))
    while t < stop:
        if t.astimezone(other).utcoffset() != t.astimezone(TZ).utcoffset():
            return False
        t += timedelta(hours=1)
    return True
