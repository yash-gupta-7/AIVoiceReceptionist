"""Clinic-local time. All stored datetimes are NAIVE clinic-local — one clinic,
one timezone, zero UTC-shift bugs ("today" must never become "tomorrow")."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from packages.shared.config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().clinic_tz)


def now() -> datetime:
    return datetime.now(tz()).replace(tzinfo=None)


def today() -> date:
    return now().date()


def to_local_naive(dt: datetime) -> datetime:
    """Accept aware or naive input; return naive clinic-local."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(tz()).replace(tzinfo=None)
