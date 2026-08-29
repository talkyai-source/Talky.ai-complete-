"""Resolve the timezone a lead's calling window must be evaluated in.

TCPA (and plain courtesy) says you call people during *their* daytime,
not the account owner's.

Two sources, in strict precedence order:

1. **``leads.timezone``** — an IANA name the customer supplied (CSV
   import or the manual contact form; added by migration 0020). This is
   stated fact about the person, so it outranks anything we infer. It is
   still *customer* data, so it is validated before use and a garbage
   value is ignored rather than raised.
2. **The phone number.** A US/Canada area code maps to a region, and
   ``phonenumbers`` ships a maintained NANP→IANA timezone database. This
   is a *guess*, so it sits behind a feature flag.

Anything else returns ``None``, which callers hand to
``CallingRules.is_within_time_window(tz_override=...)`` — its
``_resolve_tz`` then falls back to the campaign/tenant timezone and
finally UTC. So "no answer" and "use the campaign tz" are the same
value, and there is exactly one fallback ladder.

Design
------
- **Single source of truth for the derived zone = ``phonenumbers.timezone``.**
  No hand-rolled area-code table to rot; the library is already a
  dependency (used by ``dnc_service.normalize_e164``).
- **Validation uses ``pytz``**, deliberately the same library
  ``CallingRules._resolve_tz`` will evaluate the window with — a name we
  accept here can never be a name that layer rejects.
- **Cache** by E.164 prefix-ish key (the full normalized number) with an
  unbounded-but-small ``lru_cache`` — area codes repeat heavily across a
  campaign, so the hit rate is high and the key space tiny in practice.
  The explicit-value validator is cached too, so a bad row logs once
  rather than once per dial attempt.
- **Feature flag** ``DIALER_PER_LEAD_TIMEZONE`` (default ON) gates the
  *derived* path ONLY. Set to ``0``/``false`` to stop inferring zones
  from phone numbers without a redeploy. It does not disable
  ``leads.timezone``: suppressing data the customer explicitly gave us
  would be a bug, not a kill-switch.
- **Fail safe = None.** If the number isn't parseable, isn't NANP, or
  maps to several zones we can't disambiguate confidently, return
  ``None`` and let the caller fall back to the tenant timezone. We never
  guess a zone that could authorise an out-of-hours call, and we never
  raise — a timezone lookup must not drop a dial.
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
    """Return an IANA timezone derived from the lead's number, or None.

    Returns None when the feature is disabled, the number is missing, or
    the zone can't be determined — callers fall back to the tenant tz.
    """
    if not phone_number or not per_lead_timezone_enabled():
        return None
    try:
        return _resolve_cached(phone_number.strip())
    except Exception as exc:  # pragma: no cover - defensive
        # ``_resolve_cached`` already swallows phonenumbers errors; this
        # catches anything the library raises outside that block (import
        # side effects, C-extension faults). Never let it reach the dialer.
        logger.warning("lead_timezone_derive_failed number=%r err=%s", phone_number, exc)
        return None


@lru_cache(maxsize=2048)
def _normalize_cached(name: str) -> Optional[str]:
    try:
        import pytz
        pytz.timezone(name)
    except Exception:
        # Customer-supplied data. A typo in a CSV column must never block
        # or crash a dial — log once per distinct bad value (lru_cache) and
        # fall through to the derived / campaign timezone.
        logger.warning(
            "lead_timezone_invalid value=%r — ignoring, falling back", name,
        )
        return None
    return name


def normalize_lead_timezone(value: Optional[str]) -> Optional[str]:
    """Validate a customer-supplied IANA name from ``leads.timezone``.

    Returns the cleaned name, or None when it is missing, blank, or not a
    real zone. Never raises.
    """
    if not value or not isinstance(value, str):
        return None
    name = value.strip()
    if not name:
        return None
    return _normalize_cached(name)


def resolve_effective_lead_timezone(
    explicit_timezone: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> Optional[str]:
    """The timezone the calling window should be evaluated in for one lead.

    Precedence: validated ``leads.timezone`` > phone-derived zone (only
    when ``DIALER_PER_LEAD_TIMEZONE`` is on) > ``None``, which means "use
    the campaign/tenant timezone" to every caller.
    """
    explicit = normalize_lead_timezone(explicit_timezone)
    if explicit:
        return explicit
    return resolve_lead_timezone(phone_number)
