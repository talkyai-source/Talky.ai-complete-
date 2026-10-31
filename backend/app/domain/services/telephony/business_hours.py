"""Deterministic tenant-local business-hours evaluation for inbound calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class BusinessHoursDecision:
    valid: bool
    is_after_hours: bool
    reason: str
    timezone: str
    local_datetime: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "is_after_hours": self.is_after_hours,
            "reason": self.reason,
            "timezone": self.timezone,
            "local_datetime": self.local_datetime,
        }


def _minute(value: Any) -> int:
    text = str(value or "").strip()
    if not _TIME_RE.fullmatch(text):
        raise ValueError("time windows must use 24-hour HH:MM")
    hour, minute = text.split(":", 1)
    return int(hour) * 60 + int(minute)


def _holiday_dates(schedule: Mapping[str, Any]) -> set[date]:
    values = schedule.get("holidays", schedule.get("holiday_dates", []))
    if values is None:
        return set()
    if not isinstance(values, list):
        raise ValueError("holiday dates must be a list")
    dates: set[date] = set()
    for value in values:
        try:
            dates.add(date.fromisoformat(str(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError("holiday dates must use YYYY-MM-DD") from exc
    return dates


def _weekly_windows(schedule: Mapping[str, Any]) -> dict[int, list[tuple[int, int]]]:
    raw = schedule.get("weekly_schedule", schedule.get("windows"))
    if raw is None:
        # An entirely empty policy is explicitly 24/7 for backward
        # compatibility. A non-empty object missing its schedule is malformed.
        if not schedule:
            return {day: [(0, 24 * 60)] for day in range(7)}
        raise ValueError("weekly_schedule is required")
    if not isinstance(raw, list):
        raise ValueError("weekly_schedule must be a list")

    result: dict[int, list[tuple[int, int]]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("weekly schedule entries must be objects")
        try:
            day = int(entry.get("day", entry.get("day_of_week")))
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule day must be an integer from 0 to 6") from exc
        if not 0 <= day <= 6 or day in result:
            raise ValueError("schedule days must be unique integers from 0 to 6")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("schedule enabled must be boolean")
        result[day] = []
        if not enabled:
            continue
        raw_windows = entry.get("windows")
        if raw_windows is None:
            raw_windows = [{"start": entry.get("start"), "end": entry.get("end")}]
        if not isinstance(raw_windows, list) or not raw_windows:
            raise ValueError("enabled schedule days require at least one window")
        for window in raw_windows:
            if not isinstance(window, Mapping):
                raise ValueError("schedule windows must be objects")
            start = _minute(window.get("start", window.get("start_time")))
            end = _minute(window.get("end", window.get("end_time")))
            if start == end:
                raise ValueError("schedule window start and end must differ")
            result[day].append((start, end))
    return result


def evaluate_business_hours(
    timezone_name: str,
    business_hours: Mapping[str, Any] | str | None,
    *,
    now: Optional[datetime] = None,
) -> BusinessHoursDecision:
    """Evaluate one instant using Monday=0 windows and end-exclusive bounds.

    Overnight windows (for example 22:00-06:00) cover late time on their
    configured day and early time on the following day.
    """

    name = str(timezone_name or "").strip()
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return BusinessHoursDecision(False, True, "invalid_timezone", name, None)
    if business_hours is None:
        schedule: Mapping[str, Any] = {}
    elif isinstance(business_hours, Mapping):
        schedule = business_hours
    else:
        return BusinessHoursDecision(False, True, "invalid_schedule", name, None)

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(zone)
    rendered = local.isoformat()
    try:
        windows = _weekly_windows(schedule)
        holiday_policy = str(schedule.get("holiday_policy", "closed")).strip().lower()
        if holiday_policy not in {"closed", "regular_hours"}:
            raise ValueError("holiday_policy must be closed or regular_hours")
        holidays = _holiday_dates(schedule)
        if holiday_policy == "closed" and local.date() in holidays:
            return BusinessHoursDecision(True, True, "holiday_closed", name, rendered)
    except (TypeError, ValueError):
        return BusinessHoursDecision(False, True, "invalid_schedule", name, rendered)

    minute = local.hour * 60 + local.minute
    day = local.weekday()
    previous_day = (day - 1) % 7
    within = any(
        (start < end and start <= minute < end)
        or (start > end and minute >= start)
        for start, end in windows.get(day, [])
    ) or any(
        start > end and minute < end
        for start, end in windows.get(previous_day, [])
    )
    return BusinessHoursDecision(
        True,
        not within,
        "within_business_hours" if within else "outside_business_hours",
        name,
        rendered,
    )
