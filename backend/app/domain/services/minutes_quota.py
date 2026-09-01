"""Single source of truth for a tenant's monthly call-minute quota.

The same computation gates three places, so it lives here once rather than
being re-derived (and drifting) in each:

  * the dialer worker — skips a queued job when the tenant is over quota
    (``dialer_worker._tenant_minutes_exhausted``),
  * the start-campaign endpoint — refuses to *start* a campaign at all
    when the tenant is already out of minutes,
  * the frontend — shows remaining minutes + disables the Start button
    (via ``GET /campaigns/minutes/status``).

Definition (mirrors the dashboard's live figure): this month's parent-call
durations plus only ``finalized`` transfer-leg actual durations, divided by
60, versus ``tenants.minutes_allocated``. Live child reservations are enforced
by inbound admission but are not presented as already-used customer minutes.

``minutes_allocated <= 0`` means **unlimited** — never blocked. This is a
deliberate sentinel: the ``tenants.minutes_used`` column is intentionally
NOT consulted (no call-end hook writes it, so it always reads 0).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MinutesStatus:
    allocated: int          # plan allocation; 0 ⇒ unlimited
    used_minutes: int       # current-month settled parent + transfer seconds
    remaining_minutes: int  # max(0, allocated - used); 0 when unlimited (see remaining())
    unlimited: bool
    exhausted: bool         # used >= allocated (always False when unlimited)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocated": self.allocated,
            "used_minutes": self.used_minutes,
            "remaining_minutes": self.remaining_minutes,
            "unlimited": self.unlimited,
            "exhausted": self.exhausted,
        }


def _status_from(allocated: Any, used_seconds: Any) -> MinutesStatus:
    used_minutes = int(used_seconds or 0) // 60
    alloc = int(allocated or 0)
    if alloc <= 0:
        # Unlimited plan — never blocked. remaining is reported as 0 but
        # `unlimited` is the field callers should branch on.
        return MinutesStatus(
            allocated=0, used_minutes=used_minutes, remaining_minutes=0,
            unlimited=True, exhausted=False,
        )
    remaining = max(0, alloc - used_minutes)
    return MinutesStatus(
        allocated=alloc, used_minutes=used_minutes, remaining_minutes=remaining,
        unlimited=False, exhausted=used_minutes >= alloc,
    )


async def compute_minutes_status(conn: Any, tenant_id: str) -> MinutesStatus:
    """Compute the quota status for ``tenant_id`` over an asyncpg connection.

    Two cheap indexed lookups. The caller owns the connection (so this
    composes inside an existing transaction, e.g. the dialer's). Never
    raises for a missing tenant — an absent allocation reads as unlimited.
    """
    allocated = await conn.fetchval(
        "SELECT minutes_allocated FROM tenants WHERE id = $1", tenant_id
    )
    used_seconds = await conn.fetchval(
        """
        SELECT
            COALESCE((
                SELECT SUM(duration_seconds)
                FROM calls c
                WHERE c.tenant_id=$1
                  AND c.created_at >= date_trunc('month',NOW())
                  -- Campaign test sessions are real rows so they can be
                  -- reviewed, but are never customer traffic (Alembic 0017).
                  AND NOT c.is_test
                  -- Outbound rows predate inbound billing_status and retain
                  -- their legacy ``none`` value. Inbound reservations/holds
                  -- are not usage: charge the parent only after its immutable
                  -- finalize transaction commits authoritative seconds.
                  AND (
                    c.direction IS DISTINCT FROM 'inbound'
                    OR c.billing_status='finalized'
                  )
            ),0)
            + COALESCE((
                SELECT SUM(COALESCE(leg.duration_seconds,0))
                FROM call_legs leg
                JOIN calls parent ON parent.id=leg.call_id
                WHERE parent.tenant_id=$1
                  AND parent.created_at >= date_trunc('month',NOW())
                  AND NOT parent.is_test
                  AND leg.leg_type='transfer'
                  -- A live reservation protects admission capacity, but the
                  -- display/invoice total advances only after terminal proof.
                  AND leg.billing_status='finalized'
            ),0)
        """,
        tenant_id,
    )
    return _status_from(allocated, used_seconds)


async def tenant_minutes_status(tenant_id: str) -> MinutesStatus:
    """Convenience wrapper that acquires the global pool itself — for
    request handlers that don't already hold a connection.

    Fails OPEN: on any error (pool not ready, query failure) returns an
    *unlimited* status so a quota-lookup glitch never blocks a legitimate
    campaign start. The dialer's per-job gate remains as the backstop.
    """
    try:
        from app.core.db import get_pool
        from app.core.db_utils import acquire_with_tenant
        pool = get_pool()
        # `calls`/`call_legs` are RLS-FORCEd (Alembic 0013) and the app role is
        # no longer BYPASSRLS, so a bare pool.acquire() here summed ZERO seconds
        # for every tenant and reported "0 used" — the gate never fired. The
        # tenant GUC must be set for the usage read to see any rows at all.
        async with acquire_with_tenant(pool, tenant_id) as conn:
            return await compute_minutes_status(conn, tenant_id)
    except Exception as exc:  # noqa: BLE001
        # Still fails OPEN, deliberately unchanged here. NOTE: since the read
        # above now returns real usage, this branch is the only remaining way
        # for the gate to silently pass an over-quota tenant. Flipping it to
        # fail-closed is an operational decision that must be paired with a
        # per-tenant usage review first.
        logger.warning("minutes status lookup failed for tenant %s: %s", tenant_id, exc)
        return MinutesStatus(
            allocated=0, used_minutes=0, remaining_minutes=0,
            unlimited=True, exhausted=False,
        )
