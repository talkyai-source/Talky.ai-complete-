"""Phone-number → timezone lookup for callee-aware business-hours
checks (T1.5).

Before this module, `CallGuard._check_business_hours` compared the
current time against the **tenant's** business hours in the tenant's
configured timezone. For a tenant in America/New_York dialling a
number in California, calls placed at 5 PM Eastern would show as
"in business hours" — but to the California callee it's only 2 PM,
and more importantly TCPA measures business hours at the CALLEE's
location, not the caller's. A tenant in Europe dialling US east
coast at their own 3 PM local would be hitting Americans at 9 AM —
illegal under TCPA if the tenant's local is used.

This module looks up the timezone for a given E.164 number using the
Google libphonenumber geocoder. Results are cached in Redis (1-hour
TTL) because the lookup is static per-number and libphonenumber's
import overhead is non-trivial.

Fail-safe: if the lookup fails (unknown number, library error, Redis
error), we return the tenant's configured timezone so the existing
CallGuard behaviour is preserved — the check never accidentally
blocks when it shouldn't or accidentally allows when it shouldn't.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


_CACHE_KEY_PREFIX = "phone_tz:"
_CACHE_TTL_SECONDS = 3600  # 1 hour — number → tz mapping is static enough


def lookup_timezone_sync(e164: str) -> Optional[str]:
    """Pure-Python lookup, no cache. Returns an IANA tz name or None.

    Prefer `resolve_timezone()` which adds Redis caching; this variant
    exists so tests can exercise the parse path without standing up a
    Redis stub.
    """
    if not e164:
        return None
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone
    except ImportError:
        logger.debug("phonenumbers not installed — falling back to tenant tz")
        return None

    try:
        parsed = phonenumbers.parse(e164, None)
    except Exception as exc:
        logger.debug("phonenumbers_parse_failed e164=%s err=%s", e164, exc)
        return None

    try:
        tzs = pn_timezone.time_zones_for_number(parsed)
    except Exception as exc:
        logger.debug("phonenumbers_tz_lookup_failed e164=%s err=%s", e164, exc)
        return None

    if not tzs:
        return None

    # Some numbers resolve to multiple tz (e.g. Kiribati, Russia).
    # libphonenumber returns "Etc/Unknown" when it cannot be specific.
    for candidate in tzs:
        if candidate and candidate != "Etc/Unknown":
            return candidate
    return None


async def resolve_timezone(
    e164: str,
    *,
    redis_client: Any = None,
    tenant_fallback_tz: str = "UTC",
) -> str:
    """Return the IANA timezone to use for business-hours checks for
    a call to `e164`.

    Order of resolution:
      1. Redis cache (1-hour TTL).
      2. libphonenumber geocoder lookup.
      3. Tenant's configured timezone — last-resort fallback so the
         guard never silently loses its business-hours semantics.
    """
    if not e164:
        return tenant_fallback_tz

    cache_key = f"{_CACHE_KEY_PREFIX}{e164}"

    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                value = cached.decode() if isinstance(cached, bytes) else cached
                if value:
                    return value
        except Exception as exc:
            logger.debug("phone_tz_cache_read_failed err=%s", exc)

    tz = lookup_timezone_sync(e164)
    if not tz:
        return tenant_fallback_tz

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, tz, ex=_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("phone_tz_cache_write_failed err=%s", exc)

    return tz
