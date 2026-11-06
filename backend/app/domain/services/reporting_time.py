"""Reporting-only calendar boundaries; billing and dialing policies are separate."""
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.db import DATABASE_RETRYABLE_ERRORS


SINCE_DESCRIPTION = "Inclusive lower bound: YYYY-MM-DD in the effective IANA timezone, or an aware RFC3339 timestamp with an explicit offset or Z."
UNTIL_DESCRIPTION = "Exclusive upper bound: YYYY-MM-DD local midnight, or an aware RFC3339 timestamp. Use the next day's date to include a whole final day."
TZ_DESCRIPTION = "IANA reporting timezone; defaults to the selected campaign, then account setting, then UTC. Explicit timestamp offsets remain absolute instants."
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_RFC3339 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z")


class ReportingTimeUnavailable(RuntimeError):
    """A failed settings read must not become a successful UTC fallback."""


def reporting_zone(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("tz must be a valid IANA timezone.")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("tz must be a valid IANA timezone.") from exc


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _saved_zone(value):
    try:
        return reporting_zone(_object(value).get("timezone"))
    except ValueError:
        return None


def _settings_row(query):
    try:
        response = query.limit(1).execute()
        if getattr(response, "error", None):
            raise ReportingTimeUnavailable("Reporting timezone unavailable.")
        rows = response.data or []
        if not isinstance(rows, list) or (rows and not isinstance(rows[0], dict)):
            raise ReportingTimeUnavailable("Reporting timezone unavailable.")
        return rows[0] if rows else {}
    except DATABASE_RETRYABLE_ERRORS:
        # Keep the driver's retry contract for the shared HTTP error handler.
        raise
    except Exception as exc:
        raise ReportingTimeUnavailable("Reporting timezone unavailable.") from exc


def resolve_reporting_zone(db_client, tenant_id, *, campaign_id=None, requested=None):
    """Explicit zone > campaign calling_config > account calling_rules > UTC."""
    if requested is not None:
        return reporting_zone(requested)
    if campaign_id:
        row = _settings_row(db_client.table("campaigns").select("calling_config")
                            .eq("tenant_id", str(tenant_id)).eq("id", str(campaign_id)))
        zone = _saved_zone(row.get("calling_config"))
        if zone is not None:
            return zone
    # tenants.id itself is the tenant boundary; this table has no tenant_id.
    row = _settings_row(db_client.table("tenants").select("calling_rules").eq("id", str(tenant_id)))
    return _saved_zone(row.get("calling_rules")) or reporting_zone("UTC")


def _bound(value):
    if value is None:
        return None
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Date bounds must be valid calendar dates.") from exc
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("Date bounds must be YYYY-MM-DD or timezone-aware RFC3339 timestamps (at most six fractional digits).")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Date bounds must be valid timezone-aware RFC3339 timestamps.") from exc


def validate_range(since=None, until=None):
    """Refuse malformed/comparably reversed bounds before any settings query."""
    start, end = _bound(since), _bound(until)
    if start is not None and end is not None and type(start) is type(end) and start >= end:
        raise ValueError("since must be earlier than until.")


def _midnight(day, zone):
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def normalize_range(since=None, until=None, *, zone=None):
    zone = zone or reporting_zone("UTC")
    try:
        start, end = _bound(since), _bound(until)
        if start is not None and not isinstance(start, datetime):
            start = _midnight(start, zone)
        if end is not None and not isinstance(end, datetime):
            end = _midnight(end, zone)
        if start is not None and end is not None and start >= end:
            raise ValueError("since must be earlier than until.")
        return start.isoformat() if start is not None else None, end.isoformat() if end is not None else None
    except OverflowError as exc:
        raise ValueError("Date bounds are outside the supported calendar.") from exc


def calendar_range(from_date=None, to_date=None, *, zone=None, default_days=30):
    """Inclusive local date labels become independently constructed UTC bounds."""
    zone = zone or reporting_zone("UTC")
    try:
        for value in (from_date, to_date):
            if value is not None and (not isinstance(value, str) or not _DATE.fullmatch(value)):
                raise ValueError("Dates must use YYYY-MM-DD.")
        end = date.fromisoformat(to_date) if to_date is not None else datetime.now(zone).date()
        start = date.fromisoformat(from_date) if from_date is not None else end - timedelta(days=default_days)
        if start > end:
            raise ValueError("'from' date cannot be later than 'to' date")
        return _midnight(start, zone), _midnight(end + timedelta(days=1), zone)
    except (TypeError, OverflowError) as exc:
        raise ValueError("Dates must be valid YYYY-MM-DD calendar dates.") from exc


def local_day_range(date_str=None, *, zone=None):
    start, end = calendar_range(date_str, date_str, zone=zone, default_days=0)
    return start.isoformat(), end.isoformat()


def utc_day_range(date_str=None):
    return local_day_range(date_str, zone=reporting_zone("UTC"))


def local_month_range(*, zone):
    today = datetime.now(zone).date()
    start = today.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return _midnight(start, zone).isoformat(), _midnight(end, zone).isoformat()


def apply_range(query, field, since, until):
    if since is not None:
        query = query.gte(field, since)
    if until is not None:
        query = query.lt(field, until)
    return query
