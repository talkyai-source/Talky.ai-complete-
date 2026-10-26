"""Top-up minutes are used once: after the plan's monthly minutes, kept until used.

``tenants.minutes_allocated`` holds the plan's monthly minutes plus the signed
``billing_ledger`` sum (``apply_plan_allocation``, ``credit_paid_order`` and
``reverse`` all keep that true). The meter compares it with the current
calendar month's usage, so a top-up left in the allocation would be granted
again every month.

Each closed calendar month therefore uses up
``min(top-up balance, usage beyond the plan's minutes)``. That figure is
recorded as a negative ``adjustment`` ledger row keyed
``topup-usage:<tenant>:<YYYY-MM>`` and removed from ``minutes_allocated`` in
the same transaction. A zero row still marks the month closed.

Until the reminder worker records a closed month, :func:`pending_topup_usage`
computes the same figure from the same rows, so the meter never grants a
used minute twice. A top-up counts only for months that ended after it was
bought, and a reversal only from the moment it was recorded.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

USAGE_EVENT_PREFIX = "topup-usage:"


def usage_event_id(tenant_id: Any, month_key: str) -> str:
    """``month_key`` is ``YYYY-MM`` as PostgreSQL renders the calendar month."""
    return f"{USAGE_EVENT_PREFIX}{tenant_id}:{month_key}"


# Calendar months come from PostgreSQL (date_trunc in the session time zone),
# the same months the meter's ``date_trunc('month', NOW())`` uses. Python
# arithmetic on the UTC instants asyncpg returns would shift them.
_MONTH_KEY = "to_char(date_trunc('month', {}), 'YYYY-MM')"


async def _monthly_usage_minutes(conn: Any, tenant_id: str, start: datetime, end: datetime) -> dict[str, int]:
    """Whole used minutes per calendar month, by the meter's own definition."""
    rows = await conn.fetch(
        f"""
        SELECT month, SUM(seconds) AS seconds FROM (
            SELECT {_MONTH_KEY.format('c.created_at')} AS month, COALESCE(c.duration_seconds, 0) AS seconds
              FROM calls c
             WHERE c.tenant_id=$1::uuid AND c.created_at >= $2 AND c.created_at < $3
               AND NOT c.is_test
               AND (c.direction IS DISTINCT FROM 'inbound' OR c.billing_status='finalized')
            UNION ALL
            SELECT {_MONTH_KEY.format('parent.created_at')}, COALESCE(leg.duration_seconds, 0)
              FROM call_legs leg JOIN calls parent ON parent.id=leg.call_id
             WHERE parent.tenant_id=$1::uuid AND parent.created_at >= $2 AND parent.created_at < $3
               AND NOT parent.is_test AND leg.leg_type='transfer' AND leg.billing_status='finalized'
        ) usage GROUP BY month
        """,
        str(tenant_id), start, end,
    )
    return {row["month"]: int(row["seconds"] or 0) // 60 for row in rows}


async def unrecorded_topup_usage(conn: Any, tenant_id: str, allocated: int) -> list[tuple[str, int]]:
    """Closed months with no usage row yet, oldest first, with minutes used up."""
    rows = await conn.fetch(
        "SELECT minutes_delta, created_at, provider_event_id FROM billing_ledger WHERE tenant_id=$1::uuid",
        str(tenant_id),
    )
    if not rows:
        return []
    # Every closed calendar month since the first ledger row.
    months = await conn.fetch(
        f"""SELECT {_MONTH_KEY.format('m')} AS key, m AS start, m + interval '1 month' AS end
              FROM generate_series(date_trunc('month', $1::timestamptz),
                                   date_trunc('month', NOW()) - interval '1 month',
                                   interval '1 month') AS m
             ORDER BY m""",
        min(row["created_at"] for row in rows),
    )
    prefix = f"{USAGE_EVENT_PREFIX}{tenant_id}:"
    recorded: dict[str, int] = {}
    purchases: list[tuple[datetime, int]] = []
    for row in rows:
        event_id = row["provider_event_id"] or ""
        if event_id.startswith(prefix):
            recorded[event_id[len(prefix):]] = row["minutes_delta"]
        else:
            purchases.append((row["created_at"], row["minutes_delta"]))
    open_months = [m for m in months if m["key"] not in recorded]
    if not open_months:
        return []
    base = allocated - sum(row["minutes_delta"] for row in rows)
    used = await _monthly_usage_minutes(conn, tenant_id, open_months[0]["start"], months[-1]["end"])
    result: list[tuple[str, int]] = []
    pending = 0
    for month in months:
        key = month["key"]
        if key in recorded:
            continue
        available = (
            sum(delta for created, delta in purchases if created < month["end"])
            + sum(delta for closed, delta in recorded.items() if closed < key)
            - pending
        )
        consumed = max(0, min(available, used.get(key, 0) - base))
        result.append((key, consumed))
        pending += consumed
    return result


async def pending_topup_usage(conn: Any, tenant_id: str, allocated: int) -> int:
    """Minutes still inside ``minutes_allocated`` that closed months used up."""
    return sum(consumed for _, consumed in await unrecorded_topup_usage(conn, tenant_id, allocated))


async def record_topup_usage(conn: Any, tenant_id: str) -> int:
    """Record closed months. The caller owns the transaction."""
    tenant = await conn.fetchrow(
        """SELECT t.minutes_allocated, p.minutes AS plan_minutes
             FROM tenants t LEFT JOIN plans p ON p.id=t.plan_id
            WHERE t.id=$1::uuid FOR UPDATE OF t""",
        str(tenant_id),
    )
    if tenant is None or type(tenant["minutes_allocated"]) is not int:
        return 0
    if tenant["minutes_allocated"] == 0 and tenant["plan_minutes"] == 0:
        return 0  # unlimited: top-ups are never credited
    total = 0
    for month, consumed in await unrecorded_topup_usage(conn, tenant_id, tenant["minutes_allocated"]):
        inserted = await conn.fetchval(
            """INSERT INTO billing_ledger (tenant_id, kind, minutes_delta, provider_event_id, note)
               VALUES ($1::uuid, 'adjustment', $2, $3, $4)
               ON CONFLICT (provider_event_id) DO NOTHING RETURNING id""",
            str(tenant_id), -consumed, usage_event_id(tenant_id, month),
            f"top-up minutes used in {month}",
        )
        if inserted and consumed:
            await conn.execute(
                "UPDATE tenants SET minutes_allocated=GREATEST(0, minutes_allocated-$2) WHERE id=$1::uuid",
                str(tenant_id), consumed,
            )
            total += consumed
    return total


async def record_all_topup_usage(pool: Any) -> int:
    """Reminder-worker sweep over tenants that have ever bought minutes."""
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(pool, None) as conn:
        tenants = [row["tenant_id"] for row in await conn.fetch(
            "SELECT DISTINCT tenant_id FROM billing_ledger"
        )]
    total = 0
    for tenant_id in tenants:
        try:
            async with acquire_with_tenant(pool, str(tenant_id)) as conn:
                total += await record_topup_usage(conn, str(tenant_id))
        except Exception as exc:
            logger.warning("topup_usage_record_failed tenant=%s error_type=%s", str(tenant_id)[:8], type(exc).__name__)
    return total


async def topup_minutes_left(conn: Any, tenant_id: str, used_minutes: int) -> int:
    """Bought minutes not yet used, after this month's usage beyond the plan."""
    row = await conn.fetchrow(
        """SELECT t.minutes_allocated,
                  COALESCE((SELECT SUM(b.minutes_delta) FROM billing_ledger b WHERE b.tenant_id=t.id), 0) AS ledger
             FROM tenants t WHERE t.id=$1::uuid""",
        str(tenant_id),
    )
    if row is None or not row["ledger"] or type(row["minutes_allocated"]) is not int:
        return 0
    allocated, ledger = row["minutes_allocated"], int(row["ledger"])
    start = ledger - await pending_topup_usage(conn, tenant_id, allocated)
    base = allocated - ledger
    return max(0, start - max(0, int(used_minutes or 0) - base))
