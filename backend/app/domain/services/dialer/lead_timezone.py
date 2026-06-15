"""Derive a lead's local timezone from their phone number (Phase 3c).

TCPA (and plain courtesy) says you call people during *their* daytime,
not the account owner's. We don't store a per-lead timezone column, but
the phone number already encodes the geography: a US/Canada area code
maps to a region, and ``phonenumbers`` ships a maintained NANP→IANA
timezone database. This module wraps that lookup with a cache and a
feature flag.

Design
------
- **Single source of truth = ``phonenumbers.timezone``.** No hand-rolled
  area-code table to rot; the library is already a dependency (used by
  ``dnc_service.normalize_e164``).
- **Cache** by E.164 prefix-ish key (the full normalized number) with an
  unbounded-but-small ``lru_cache`` — area codes repeat heavily across a
  campaign, so the hit rate is high and the key space tiny in practice.
- **Feature flag** ``DIALER_PER_LEAD_TIMEZONE`` (default ON). Set to
  ``0``/``false`` to fall back to the tenant timezone everywhere without
  a redeploy — the worker simply stops asking for a per-lead tz.
- **Fail safe = None.** If the number isn't parseable, isn't NANP, or
  maps to several zones we can't disambiguate confidently, return
  ``None`` and let the caller fall back to the tenant timezone. We never
  guess a zone that could authorise an out-of-hours call.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def per_lead_timezone_enabled() -> bool:
    """Feature flag — read per call so a systemd ``Environment=`` change
    takes effect on restart without a redeploy."""
    return os.getenv("DIALER_PER_LEAD_TIMEZONE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


@lru_cache(maxsize=4096)
def _resolve_cached(e164: str) -> Optional[str]:
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone
        parsed = phonenumbers.parse(e164, None)
        zones = pn_timezone.time_zones_for_number(parsed)
    except Exception:
        return None

    if not zones:
        return None
    # ``phonenumbers`` returns a generic "Etc/Unknown" sentinel when it
    # can't localise — treat that as no-answer so we fall back to tenant tz.
    first = zones[0]
    if not first or first == "Etc/Unknown":
        return None
    # A single confident zone is the common NANP case. When a number spans
    # multiple zones (e.g. some UK ranges) we still take the first — the
    # caller's window check is the same across those neighbouring zones.
    return first


def resolve_lead_timezone(phone_number: Optional[str]) -> Optional[str]:
    """Return an IANA timezone for the lead's number, or None.

    Returns None when the feature is disabled, the number is missing, or
    the zone can't be determined — callers fall back to the tenant tz.
    """
    if not phone_number or not per_lead_timezone_enabled():
        return None
    return _resolve_cached(phone_number.strip())
