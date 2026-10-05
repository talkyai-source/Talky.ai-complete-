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

Returns the canonical known/unlimited/unavailable result. Unknown usage never
becomes zero or a full remaining allowance.
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


async def compute_tenant_minutes_status(db_pool, tenant_id: Optional[str]):
    """Auth/profile adapter over the existing canonical meter, never a fallback."""
    from app.domain.services.minutes_quota import compute_minutes_status, unavailable_minutes
    from app.core.db_utils import acquire_with_tenant
    if db_pool is None or not tenant_id:
        return unavailable_minutes("allowance_unavailable")
    try:
        tenant_uuid = UUID(str(tenant_id))
        async with acquire_with_tenant(db_pool, str(tenant_uuid)) as conn:
            return await compute_minutes_status(conn, tenant_uuid)
    except Exception as exc:
        logger.warning("profile_metering_unavailable error_type=%s", type(exc).__name__)
        return unavailable_minutes()


async def compute_tenant_minutes_used(db_pool, tenant_id: Optional[str]) -> int | None:
    return (await compute_tenant_minutes_status(db_pool, tenant_id)).used_minutes


async def compute_tenant_minutes_remaining(
    db_pool, *, tenant_id: Optional[str], minutes_allocated: Optional[int],
) -> int | None:
    """Compatibility adapter; passed allocation is not independent authority."""
    meter = await compute_tenant_minutes_status(db_pool, tenant_id)
    return meter.allowance()["minutes_remaining"]
