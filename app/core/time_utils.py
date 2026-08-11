from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
FALLBACK_TIMEZONE = "America/Chicago"


def day_bounds_utc(day: date, tz_name: str | None) -> tuple[datetime, datetime]:
    """
    Converts a calendar day, interpreted in the given IANA timezone, into
    the UTC datetime range that covers it. `appointment_datetime` is stored
    in UTC, so a naive UTC-day query would clip early morning / late
    evening local appointments - this must be done per-clinic-timezone.
    """
    tz = ZoneInfo(tz_name or FALLBACK_TIMEZONE)
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day, time.max, tzinfo=tz)

    return start_local.astimezone(UTC), end_local.astimezone(UTC)
