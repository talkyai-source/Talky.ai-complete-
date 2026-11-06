"""Explicit assistant ranges: aware RFC3339 instants and UTC half-open bounds.

Local calendar dates and stored IANA timezone selection belong to the separate
local-day contract. Do not silently interpret a naive timestamp as UTC here.
"""
from datetime import date, datetime, time, timedelta, timezone
import re


SINCE_DESCRIPTION = "Inclusive lower bound: RFC3339 timestamp with an explicit timezone offset or Z (for example 2026-10-01T00:00:00Z)."
UNTIL_DESCRIPTION = "Exclusive upper bound: RFC3339 timestamp with an explicit timezone offset or Z. Use the next day's midnight to include a whole final day."
_RFC3339 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z")


def _instant(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("Date bounds must be timezone-aware RFC3339 timestamps (at most six fractional digits).")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Date bounds must be valid timezone-aware RFC3339 timestamps.") from exc


def normalize_range(since=None, until=None):
    start, end = _instant(since), _instant(until)
    if start is not None and end is not None and start >= end:
        raise ValueError("since must be earlier than until.")
    return start.isoformat() if start is not None else None, end.isoformat() if end is not None else None


def utc_day_range(date_str=None):
    try:
        if date_str is not None and (not isinstance(date_str, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_str)):
            raise ValueError("invalid calendar date")
        day = date.fromisoformat(date_str) if date_str is not None else datetime.now(timezone.utc).date()
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        return start.isoformat(), (start + timedelta(days=1)).isoformat()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("date_str must be a valid YYYY-MM-DD calendar date.") from exc


def apply_range(query, field, since, until):
    if since is not None:
        query = query.gte(field, since)
    if until is not None:
        query = query.lt(field, until)
    return query
