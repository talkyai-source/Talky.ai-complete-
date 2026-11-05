"""Legal calling-hours floor: the hours no campaign setting can widen.

The campaign's calling window, and its "call anytime" switch, are the
customer's choice. This floor is not: a call goes out only when it is daytime
for the person being called. US rules (TCPA, 47 CFR 64.1200(c)(1)) allow
08:00-21:00 in the callee's local time, and UK and EU practice uses the same
hours. Australia's telemarketing standard allows 09:00-20:00 on weekdays and
09:00-17:00 on Saturdays, never Sundays. Public holidays are not modelled.

The callee's clock is the lead's stated timezone when there is one. Otherwise
it is every zone the number can ring in (``phonenumbers``), narrowed to the
number's own country when the list spans several (NANP toll-free lists Guam
and Bermuda), or the country's zones when the number has no geographic zone.
Every candidate zone must be inside the floor, and with no zone at all the
floor fails closed.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import lru_cache
from typing import Optional, Tuple

import pytz

_EVERY_DAY = {day: (time(8), time(21)) for day in range(7)}
_AUSTRALIA = {**{day: (time(9), time(20)) for day in range(5)}, 5: (time(9), time(17))}


def _utcnow() -> datetime:
    # One dial decision, one instant: read the clock the campaign window
    # (CallingRules) reads, so the floor and the window never disagree on "now".
    from app.domain.models import calling_rules

    return calling_rules.datetime.now(pytz.UTC)


def _known(zone: Optional[str]) -> bool:
    if not zone or zone == "Etc/Unknown":
        return False
    try:
        pytz.timezone(zone)
    except pytz.exceptions.UnknownTimeZoneError:
        return False
    return True


@lru_cache(maxsize=4096)
def _number_zones(phone_number: str) -> tuple:
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone

        parsed = phonenumbers.parse(phone_number, None)
        zones = [z for z in pn_timezone.time_zones_for_number(parsed) if _known(z)]
        # A number the library cannot place still has a country code; its
        # main country is where it rings (+44 -> GB, +1 -> US).
        region = phonenumbers.region_code_for_number(parsed) or phonenumbers.region_code_for_country_code(
            parsed.country_code
        )
    except Exception:
        return ()
    try:
        country = [z for z in pytz.country_timezones[region] if _known(z)] if region else []
    except KeyError:
        country = []
    if zones and country:
        zones = [z for z in zones if z in country] or zones
    return tuple(zones or country)


def candidate_zones(phone_number: Optional[str], lead_timezone: Optional[str] = None) -> tuple:
    if _known(lead_timezone):
        return (lead_timezone,)
    if not phone_number:
        return ()
    return _number_zones(phone_number.strip())


def within_legal_hours(
    phone_number: Optional[str],
    lead_timezone: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    zones = candidate_zones(phone_number, lead_timezone)
    if not zones:
        return False, "legal_hours_unknown_timezone"
    now = now or _utcnow()
    for zone in zones:
        local = now.astimezone(pytz.timezone(zone))
        hours = (_AUSTRALIA if zone.startswith("Australia/") else _EVERY_DAY).get(local.weekday())
        if hours is None or not hours[0] <= local.time() < hours[1]:
            return False, f"outside_calling_window_legal_hours_{zone}"
    return True, "within_legal_calling_hours"


def seconds_until_legal(
    phone_number: Optional[str],
    lead_timezone: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Seconds until the floor opens for this callee, in 5-minute steps over
    the next eight days; None when no zone is known (it never opens)."""
    if not candidate_zones(phone_number, lead_timezone):
        return None
    now = now or _utcnow()
    step = timedelta(minutes=5)
    for i in range(8 * 24 * 12 + 1):
        moment = now + i * step
        if within_legal_hours(phone_number, lead_timezone, moment)[0]:
            return int((moment - now).total_seconds())
    return None
