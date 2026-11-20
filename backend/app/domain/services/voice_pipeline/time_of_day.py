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
    """Current hour (0-23) in ``tz_name``; None if the zone is missing or
    can't be resolved. ``_now`` is injectable for tests (avoids the sandbox
    clock restriction).

    A None/empty ``tz_name`` returns None directly — it used to silently
    fall back to Europe/London, so a non-UK campaign with no configured
    timezone got a confidently-wrong UK time-of-day greeting (a 5pm call
    greeted with "Morning"). An unknown-but-present tz string (e.g. a typo)
    still goes through ZoneInfo and falls back to None via the except below,
    same as before.
    """
    name = (tz_name or "").strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        now = _now or datetime.now(timezone.utc)
        return now.astimezone(ZoneInfo(name)).hour
    except Exception as exc:  # noqa: BLE001 — unknown tz / no tzdata
        logger.debug("time_of_day: tz resolve failed tz=%s err=%s", tz_name, exc)
        return None


UNKNOWN_TIME_LINE = (
    "You do not know the local time of the person you are speaking with. "
    "Use greetings and goodbyes that do not depend on the time of day."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _person_phone(session) -> Optional[str]:
    """The number of the PERSON on the phone: the dialled number (outbound) or
    the caller's ANI (inbound). ``bind_telephony_call`` / the pre-originate
    stamp / ``bind_inbound_call`` put it on the telephony VoiceSession as
    ``_dialer_phone``; the CallSession the streamer holds is found through
    that VoiceSession (same walk as ``resolve_call_binding``)."""
    phone = getattr(session, "_dialer_phone", None)
    if phone:
        return str(phone)
    try:
        from app.domain.services.telephony.lifecycle import _state

        for _chan, vs in _state().iter_voice_session_items():
            if getattr(vs, "call_session", None) is session:
                phone = getattr(vs, "_dialer_phone", None)
                return str(phone) if phone else None
    except Exception:  # noqa: BLE001 - non-telephony session / teardown
        pass
    return None


def person_zone(session) -> tuple[Optional[str], str]:
    """(zone, source) for the person on the phone. Never the account's zone.

    Order: a zone stamped on the session for this lead (``_dialer_lead_timezone``)
    -> the dialler's own number-to-zone function on the person's number ->
    (None, "none"). Same function as the dialler's calling window, so the
    clock the agent talks about is the clock the window was judged on.
    """
    from app.domain.services.dialer.lead_timezone import (
        normalize_lead_timezone,
        resolve_lead_timezone,
    )

    lead_zone = normalize_lead_timezone(getattr(session, "_dialer_lead_timezone", None))
    if lead_zone:
        return lead_zone, "lead"
    zone = resolve_lead_timezone(_person_phone(session))
    if zone:
        return zone, "number"
    return None, "none"


def person_time_of_day_line(session, *, _now: Optional[datetime] = None) -> str:
    """The time-of-day prompt line for the person on this call (when their zone
    is unknown, ``UNKNOWN_TIME_LINE``: no clock, a neutral instruction). Logs one
    ``time_of_day_hint`` line per call."""
    cached = getattr(session, "_tod_person_zone", None)
    if cached is None:
        cached = person_zone(session)
        try:
            session._tod_person_zone = cached
        except Exception:  # noqa: BLE001
            pass
    zone, source = cached
    now = _now or _utcnow()
    known = time_of_day_line(zone, _now=now)
    if not getattr(session, "_tod_hint_logged", False):
        hour = local_hour(zone, _now=now)
        part = greeting_for_hour(hour) if (known and hour is not None) else "none"
        logger.info(
            "time_of_day_hint call=%s zone=%s source=%s part=%s",
            getattr(session, "call_id", None), zone if known else None,
            source if known else "none", part,
        )
        try:
            session._tod_hint_logged = True
        except Exception:  # noqa: BLE001
            pass
    return known or UNKNOWN_TIME_LINE


def time_of_day_line(tz_name: Optional[str], *, _now: Optional[datetime] = None) -> str:
    """One prompt line telling the agent the callee's local time-of-day, so a
    greeting matches the hour. Empty string if the timezone is unknown (the
    model then simply avoids a time-specific greeting)."""
    hour = local_hour(tz_name, _now=_now)
    if hour is None:
        return ""
    word = greeting_for_hour(hour)
    # The date too: without it the model cannot tell whether "the tenth of
    # October" is inside an offer window (test call f5dcac8e, 8 Oct 2026, asked
    # "is that October this year, or did you mean November?").
    from zoneinfo import ZoneInfo
    local = (_now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name.strip()))
    today = f"{local:%A} {local.day} {local:%B %Y}"
    return (
        f"- Today is {today}. It is currently {word} for the person you're calling "
        f"(their local time, ~{hour:02d}:00). If you greet by time of day, say \"{word}\" — "
        f"never a greeting that contradicts their clock."
    )
