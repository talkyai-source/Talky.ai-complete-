"""Live tenant-minutes computation for the auth / profile / billing paths.

The `tenants.minutes_used` column is not the source of truth — it is zero
for every tenant in production because no call-end hook ever writes it.
Reading it made every login response, profile read and billing summary
report `minutes_remaining` as the full plan allocation regardless of usage.

WHY THIS MODULE NO LONGER CARRIES ITS OWN SQL (2026-08-03)
----------------------------------------------------------
It used to run its own `SUM(duration_seconds)` with the predicate

    status IN ('answered', 'completed', 'in_progress')

and its docstring claimed that was "the same predicate the dashboard summary
endpoint uses". That stopped being true: the dashboard moved to keying on
`outcome` and dropped the status filter entirely, because the filter "missed
every 'ended' call, so minutes were a small fraction of reality" (see the
comment in `dashboard.py`). This module was never updated, so the two drifted
back into exactly the disagreement it was created to prevent.

Only two values have ever been written to `calls.status`: `completed` and
`ended`. `answered` and `in_progress` have never existed in the table. So of
the three values in the predicate, two matched nothing and the third excluded
`ended` — the majority of recorded call time.

The gate that actually *blocks* calls
(`minutes_quota.compute_minutes_status`) has no such filter. Comparing the two
against production data showed the billing figure landing anywhere from a
small fraction of the gate's number down to zero for a tenant whose calls all
finished as `ended`. A tenant could therefore be blocked for exhausting their
plan while every screen they can see said they had most of it left.

No status filter is needed, and adding one back is a regression: an
unconnected call has no duration, so `duration_seconds` already filters
itself. Checked against the full production history, every non-connected
outcome sums to zero seconds apart from a negligible handful of `no_answer`
rows that round away entirely.

This module is now a thin adapter over `minutes_quota.compute_minutes_status`
so there is exactly ONE definition of "minutes used" in the codebase, shared
by the quota gate, the dashboard, auth, profile and billing.

Returns minutes (int, floored), not seconds. Errors are swallowed and treated
as "0 used" so a transient DB issue can't lock a tenant out of placing calls.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


def _start_of_current_month_utc() -> datetime:
    """First instant of the current calendar month in UTC.

    Kept for callers that need the boundary itself. It agrees with the
    `date_trunc('month', now())` used by `compute_minutes_status`: the
    production database runs `TimeZone = UTC` and `calls.created_at` is
    `timestamptz`, so the two resolve to the same instant.
    """
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def compute_tenant_minutes_used(
    db_pool,
    tenant_id: Optional[str],
) -> int:
    """Live monthly minutes used for `tenant_id`, from the `calls` table.

    Delegates to `minutes_quota.compute_minutes_status` — the same
    computation the dialer's quota gate enforces against — so a tenant can
    never be blocked by a number their invoice does not show.

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

    from app.domain.services.minutes_quota import compute_minutes_status

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # The WHERE clause already scopes to tenant_id, and this is a
                # platform-internal aggregation. Preserved from the previous
                # implementation so behaviour under RLS is unchanged.
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                status = await compute_minutes_status(conn, tenant_uuid)
    except Exception as exc:
        logger.warning(
            "compute_tenant_minutes_used failed tenant=%s err=%s",
            str(tenant_id)[:8], exc,
        )
        return 0

    return int(status.used_minutes)


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
