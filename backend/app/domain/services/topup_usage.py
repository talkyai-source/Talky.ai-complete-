"""Top-up minutes are used once: after the plan's monthly minutes, kept until used.

``tenants.minutes_allocated`` is the plan's monthly minutes (the base). Bought
minutes live only in ``billing_ledger``: credits, refunds and disputes, plus one
negative ``adjustment`` row per closed calendar month keyed
``topup-usage:<tenant>:<YYYY-MM>`` recording ``min(balance, usage beyond the
base)``. A zero row still marks the month closed. The meter's monthly cap is
``base + ledger balance - unrecorded usage``.

Until the reminder worker records a closed month, :func:`pending_topup_usage`
computes the same figure from the same rows, so the meter never grants a used
minute twice. A top-up counts only for months that ended after it was bought,
and a reversal only from the moment it was recorded. A refund of minutes
already used leaves a negative balance, taken from the next allowance.
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


# Windows come from PostgreSQL (session time zone), the same allowance months
# the meter uses (minutes_quota.allowance_window_start_sql): monthly from the
# plan's purchase, or calendar months without one. Keys are the window start
# date. Python arithmetic on the UTC instants asyncpg returns would shift them.


async def _closed_windows(conn: Any, tenant_id: str, first: datetime, close_current: bool) -> list:
    """Allowance windows from the one holding ``first`` up to now, oldest first.

    Closed windows only, unless ``close_current`` (a plan change re-anchors the
    allowance, so the running window ends now).
    """
    from app.domain.services.minutes_quota import allowance_window_index_sql

    anchor = await conn.fetchval("SELECT allowance_anchor_at FROM tenants WHERE id=$1::uuid", str(tenant_id))
    if anchor is None:
        windows = await conn.fetch(
            """SELECT to_char(m, 'YYYY-MM-DD') AS key, m AS start, m + interval '1 month' AS end
                 FROM generate_series(date_trunc('month', $1::timestamptz),
                                      date_trunc('month', NOW()) - interval '1 month',
                                      interval '1 month') AS m
                ORDER BY m""",
            first,
        )
        current = await conn.fetchrow(
            "SELECT to_char(date_trunc('month', NOW()), 'YYYY-MM-DD') AS key, date_trunc('month', NOW()) AS start, NOW() AS end"
        ) if close_current else None
    else:
        # Windows before the anchor were closed when the plan (re)started.
        index_first = allowance_window_index_sql("$1::timestamptz", "GREATEST($2::timestamptz, $1::timestamptz)")
        index_now = allowance_window_index_sql("$1::timestamptz", "NOW()")
        windows = await conn.fetch(
            f"""SELECT to_char(w.start, 'YYYY-MM-DD') AS key, w.start,
                       $1::timestamptz + make_interval(months => w.k + 1) AS end
                  FROM (SELECT k, $1::timestamptz + make_interval(months => k) AS start
                          FROM generate_series({index_first}, {index_now} - 1) AS k) w
                 ORDER BY w.k""",
            anchor, first,
        )
        current = await conn.fetchrow(
            f"""SELECT to_char(s, 'YYYY-MM-DD') AS key, s AS start, NOW() AS end
                  FROM (SELECT $1::timestamptz + make_interval(months => {index_now}) AS s) w""",
            anchor,
        ) if close_current and anchor <= await conn.fetchval("SELECT NOW()") else None
    return list(windows) + ([current] if current else [])


async def _window_usage_minutes(conn: Any, tenant_id: str, windows: list) -> dict[str, int]:
    """Whole used minutes per window, by the meter's own definition."""
    rows = await conn.fetch(
        """
        SELECT w.key,
               COALESCE((SELECT SUM(c.duration_seconds) FROM calls c
                          WHERE c.tenant_id=$1::uuid AND c.created_at >= w.start AND c.created_at < w.finish
                            AND NOT c.is_test
                            AND (c.direction IS DISTINCT FROM 'inbound' OR c.billing_status='finalized')), 0)
             + COALESCE((SELECT SUM(COALESCE(leg.duration_seconds, 0))
                           FROM call_legs leg JOIN calls parent ON parent.id=leg.call_id
                          WHERE parent.tenant_id=$1::uuid AND parent.created_at >= w.start
                            AND parent.created_at < w.finish AND NOT parent.is_test
                            AND leg.leg_type='transfer' AND leg.billing_status='finalized'), 0) AS seconds
          FROM unnest($2::text[], $3::timestamptz[], $4::timestamptz[]) AS w(key, start, finish)
        """,
        str(tenant_id), [w["key"] for w in windows], [w["start"] for w in windows], [w["end"] for w in windows],
    )
    return {row["key"]: int(row["seconds"] or 0) // 60 for row in rows}


async def _ledger_rows(conn: Any, tenant_id: str) -> list:
    return await conn.fetch(
        "SELECT minutes_delta, created_at, provider_event_id FROM billing_ledger WHERE tenant_id=$1::uuid",
        str(tenant_id),
    )


async def unrecorded_topup_usage(conn: Any, tenant_id: str, base: int, rows: list | None = None,
                                 close_current: bool = False) -> list[tuple[str, int]]:
    """Closed windows with no usage row yet, oldest first, with minutes used up.

    ``base`` is the plan's monthly minutes (``tenants.minutes_allocated``).
    """
    if rows is None:
        rows = await _ledger_rows(conn, tenant_id)
    if not rows:
        return []
    months = await _closed_windows(conn, tenant_id, min(row["created_at"] for row in rows), close_current)
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
    # Usage only matters where bought minutes were available; skip the calls
    # scan when none ever were (every window then records zero).
    ever_available = any(
        sum(delta for created, delta in purchases if created < month["end"])
        + sum(delta for closed, delta in recorded.items() if closed < month["key"]) > 0
        for month in open_months
    )
    used = await _window_usage_minutes(conn, tenant_id, open_months) if ever_available else {}
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


async def pending_topup_usage(conn: Any, tenant_id: str, base: int) -> int:
    """Bought minutes that closed months used up, not yet recorded."""
    return sum(consumed for _, consumed in await unrecorded_topup_usage(conn, tenant_id, base))


async def topup_balance(conn: Any, tenant_id: str, base: int) -> int:
    """Ledger balance less unrecorded usage, from ONE read of the ledger.

    Negative when refunded minutes were already used. The meter adds this to
    the plan's minutes; one read keeps the sum and the usage consistent.
    """
    rows = await _ledger_rows(conn, tenant_id)
    if not rows:
        return 0
    pending = await unrecorded_topup_usage(conn, tenant_id, base, rows)
    return sum(row["minutes_delta"] for row in rows) - sum(consumed for _, consumed in pending)


async def record_topup_usage(conn: Any, tenant_id: str, close_current: bool = False) -> int:
    """Record closed months as ledger rows. The caller owns the transaction.

    The tenant row lock serializes two recorders and plan allocation (which
    records first in its own transaction). Credits and reversals are stamped
    NOW(), after every closed month ended, so they never change a closed
    month's figure; the unique event id makes a repeat a no-op.
    """
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
    windows = await unrecorded_topup_usage(conn, tenant_id, tenant["minutes_allocated"], close_current=close_current)
    for position, (month, consumed) in enumerate(windows):
        if close_current and position == len(windows) - 1 and not consumed:
            continue  # the running window ends at re-anchoring; a zero row marks nothing
        inserted = await conn.fetchval(
            """INSERT INTO billing_ledger (tenant_id, kind, minutes_delta, provider_event_id, note)
               VALUES ($1::uuid, 'adjustment', $2, $3, $4)
               ON CONFLICT (provider_event_id) DO NOTHING RETURNING id""",
            str(tenant_id), -consumed, usage_event_id(tenant_id, month),
            f"top-up minutes used in {month}",
        )
        if inserted:
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
    row = await conn.fetchrow("SELECT minutes_allocated FROM tenants WHERE id=$1::uuid", str(tenant_id))
    if row is None or type(row["minutes_allocated"]) is not int:
        return 0
    base = row["minutes_allocated"]
    start = await topup_balance(conn, tenant_id, base)
    return max(0, start - max(0, int(used_minutes or 0) - base))
