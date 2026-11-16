"""Tenant-wide origination pacing — true "one call at a time" feel.

The per-campaign inter-call gap works exactly as configured (audited
2026-07-08: 305s like clockwork), but each campaign has its OWN clock, so two
running campaigns phase-lock and fire 2–11 seconds apart every cycle. Two
simultaneous calls double the load on the single voice pipeline at the same
instant — measured result: 12–23s reply latency, audio gaps ("buzz"), dead
air on live prospects. Separate PBX trunks don't help; the collision is
compute, not carrier.

This module adds a TENANT-level minimum spacing between ANY two originations,
regardless of campaign. It uses one atomic Redis ``SET NX EX`` as a claim —
atomic, so two workers (or two campaigns' jobs in the same tick) can never
both pass the gate in the same window. The per-campaign gap still applies on
top; this gate only staggers campaigns against each other.

``DIALER_TENANT_MIN_GAP_S`` (default 90) — 0 disables the gate entirely.
Fail-open: Redis trouble never blocks a call.

The voice process is shared by ALL tenants, so the tenant gap alone still lets
three tenants start three calls in the same second. A PLATFORM-wide slot
(``DIALER_PLATFORM_MIN_GAP_S``, default 10, 0 = off) spaces any two
originations across every tenant, using the same atomic claim.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def tenant_min_gap_s() -> int:
    """Minimum seconds between ANY two originations of one tenant (0 = off)."""
    try:
        return max(0, int(os.getenv("DIALER_TENANT_MIN_GAP_S", "90")))
    except (TypeError, ValueError):
        return 90


def _slot_key(tenant_id: str) -> str:
    return f"dialer:last_dial:tenant:{tenant_id}"


async def claim_tenant_dial_slot(redis, tenant_id: str) -> int:
    """Try to claim the tenant's origination slot. Returns 0 when claimed
    (proceed to dial), or the seconds to wait before retrying.

    Atomic: ``SET NX EX gap`` — whoever sets the key owns the slot for the
    gap window; everyone else reads the TTL and defers for that long (+1s
    cushion so the retry lands after expiry, not on it).

    Fail-open: any Redis problem returns 0 so pacing trouble can never stop
    a campaign from dialing.
    """
    gap = tenant_min_gap_s()
    if gap <= 0 or redis is None or not tenant_id:
        return 0
    try:
        claimed = await redis.set(_slot_key(str(tenant_id)), "1", ex=gap, nx=True)
        if claimed:
            return 0
        ttl = await redis.ttl(_slot_key(str(tenant_id)))
        wait = (ttl if ttl and ttl > 0 else gap) + 1
        return int(wait)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.debug("tenant_gap: claim failed tenant=%s err=%s", tenant_id, exc)
        return 0


async def release_tenant_dial_slot(redis, tenant_id: str) -> None:
    """Give the slot back when the origination did NOT actually happen
    (guard refusal after the claim, provider 503, originate error) so a
    failed attempt doesn't burn the whole gap window for the tenant."""
    if redis is None or not tenant_id:
        return
    try:
        await redis.delete(_slot_key(str(tenant_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("tenant_gap: release failed tenant=%s err=%s", tenant_id, exc)


def platform_min_gap_s() -> int:
    """Minimum seconds between ANY two originations on the platform (0 = off)."""
    try:
        return max(0, int(os.getenv("DIALER_PLATFORM_MIN_GAP_S", "10")))
    except (TypeError, ValueError):
        return 10


_PLATFORM_SLOT_KEY = "dialer:last_dial:platform"


async def claim_platform_dial_slot(redis) -> int:
    """Try to claim the platform-wide origination slot. Returns 0 when claimed,
    or the seconds to wait (remaining TTL + 1s cushion). Atomic ``SET NX EX``;
    fail-open on any Redis problem."""
    gap = platform_min_gap_s()
    if gap <= 0 or redis is None:
        return 0
    try:
        claimed = await redis.set(_PLATFORM_SLOT_KEY, "1", ex=gap, nx=True)
        if claimed:
            return 0
        ttl = await redis.ttl(_PLATFORM_SLOT_KEY)
        wait = (ttl if ttl and ttl > 0 else gap) + 1
        return int(wait)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.debug("platform_gap: claim failed err=%s", exc)
        return 0


async def release_platform_dial_slot(redis) -> None:
    """Give the platform slot back when the origination did NOT happen."""
    if redis is None:
        return
    try:
        await redis.delete(_PLATFORM_SLOT_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("platform_gap: release failed err=%s", exc)
