"""Live tenant-minutes computation.

The `tenants.minutes_used` column is no longer the source of truth — it
was historically left at zero (no call-end hook ever wrote to it) which
made every login response, profile read, and billing summary report a
caller's minutes_remaining as the full plan allocation regardless of
how many calls had completed.

The dashboard endpoint already computed minutes the right way: sum
`duration_seconds` from the `calls` table for the current billing
month. This module hoists that logic out so the auth / profile /
billing paths can share the exact same number — preventing the bug
where the dashboard says "120 minutes used" while /auth/me says "0".

Returns minutes (int, floored), not seconds. Errors are swallowed and
treated as "0 used" so a transient DB issue can't lock a tenant out
of placing calls. The dashboard's behaviour is preserved verbatim.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


def _start_of_current_month_utc() -> datetime:
    """First instant of the current calendar month in UTC.

    Mirrors the dashboard endpoint exactly so the two paths agree on
    when a billing window starts."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def compute_tenant_minutes_used(
    db_pool,
    tenant_id: Optional[str],
) -> int:
    """Live monthly minutes used for `tenant_id`, computed from the
    `calls` table.

    Sums `duration_seconds` across rows in the current billing month
    whose status is one of ('answered', 'completed', 'in_progress')
    — same predicate the dashboard summary endpoint uses.

    Args:
        db_pool: asyncpg pool. None → returns 0.
        tenant_id: UUID string. None / invalid → returns 0.

    Returns:
        Minutes used (int, floored). Never raises.
    """
    if db_pool is None or not tenant_id:
        return 0
    try:
        tenant_uuid = UUID(str(tenant_id))
    except (ValueError, TypeError):
        return 0

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # Bypass RLS — the WHERE clause already scopes to tenant_id,
                # and this is a platform-internal aggregation.
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                total_seconds = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(duration_seconds), 0)
                    FROM calls
                    WHERE tenant_id = $1
                      AND status = ANY($2::text[])
                      AND created_at >= $3
                    """,
                    tenant_uuid,
                    ["answered", "completed", "in_progress"],
                    _start_of_current_month_utc(),
                )
    except Exception as exc:
        logger.warning(
            "compute_tenant_minutes_used failed tenant=%s err=%s",
            str(tenant_id)[:8], exc,
        )
        return 0

    if not total_seconds:
        return 0
    return int(int(total_seconds) // 60)


async def compute_tenant_minutes_remaining(
    db_pool,
    *,
    tenant_id: Optional[str],
    minutes_allocated: Optional[int],
) -> int:
    """Convenience: allocation − live usage, floored at zero."""
    used = await compute_tenant_minutes_used(db_pool, tenant_id)
    allocated = int(minutes_allocated or 0)
    return max(0, allocated - used)
