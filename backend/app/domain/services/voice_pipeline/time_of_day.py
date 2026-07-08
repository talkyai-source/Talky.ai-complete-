"""Callee-local time-of-day for greetings.

The agent said "Morning" on every call regardless of the actual hour where the
person picked up (LLM-generated, no clock in context). This computes the
correct greeting word from the CALLEE's timezone (the campaign's configured
timezone; UK campaigns are Europe/London) so "Morning / Afternoon / Evening"
matches reality — a 5pm "Morning" instantly reads as a bot.

Pure + fail-soft: an unknown timezone falls back to a neutral hint that lets
the model pick a safe, non-time-specific greeting.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TZ = "Europe/London"


def greeting_for_hour(hour: int) -> str:
    """UK-natural greeting word for a 0-23 local hour."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "evening"  # late night: "evening" is the safest natural choice


def local_hour(tz_name: Optional[str], *, _now: Optional[datetime] = None) -> Optional[int]:
    """Current hour (0-23) in ``tz_name``; None if the zone can't be resolved.
    ``_now`` is injectable for tests (avoids the sandbox clock restriction)."""
    name = (tz_name or _DEFAULT_TZ).strip() or _DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo
        now = _now or datetime.now(timezone.utc)
        return now.astimezone(ZoneInfo(name)).hour
    except Exception as exc:  # noqa: BLE001 — unknown tz / no tzdata
        logger.debug("time_of_day: tz resolve failed tz=%s err=%s", tz_name, exc)
        return None


def time_of_day_line(tz_name: Optional[str], *, _now: Optional[datetime] = None) -> str:
    """One prompt line telling the agent the callee's local time-of-day, so a
    greeting matches the hour. Empty string if the timezone is unknown (the
    model then simply avoids a time-specific greeting)."""
    hour = local_hour(tz_name, _now=_now)
    if hour is None:
        return ""
    word = greeting_for_hour(hour)
    return (
        f"- It is currently {word} for the person you're calling (their local "
        f"time, ~{hour:02d}:00). If you greet by time of day, say \"{word}\" — "
        f"never a greeting that contradicts their clock."
    )
