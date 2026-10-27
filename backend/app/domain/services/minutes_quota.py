"""Single source of truth for a tenant's monthly call-minute quota.

The same computation gates three places, so it lives here once rather than
being re-derived (and drifting) in each:

  * the dialer worker — skips a queued job when the tenant is over quota
    (``dialer_worker._tenant_minutes_exhausted``),
  * the start-campaign endpoint — refuses to *start* a campaign at all
    when the tenant is already out of minutes,
  * the frontend — shows remaining minutes + disables the Start button
    (via ``GET /campaigns/minutes/status``).

Definition (mirrors the dashboard's live figure): this allowance month's parent-call
durations plus only ``finalized`` transfer-leg actual durations, divided by
60, versus ``tenants.minutes_allocated``. Live child reservations are enforced
by inbound admission but are not presented as already-used customer minutes.

A zero allocation is unlimited only when the bound plan explicitly has zero
included minutes; an account with no plan has zero minutes. Missing/invalid
allowance or failed usage reads are unavailable. Top-up minutes are used once
(see ``topup_usage``).
The stale tenants.minutes_used column is never an admission authority.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


def allowance_window_index_sql(anchor: str, ts: str) -> str:
    """SQL: whole months from ``anchor`` to ``ts`` (``ts >= anchor``).

    ``age()`` borrows days field by field, so the estimate can be one month off
    around month ends (Jan 31 + 1 month = Feb 28); both corrections keep
    ``anchor + idx months <= ts < anchor + (idx + 1) months``.
    """
    est = f"((EXTRACT(YEAR FROM age({ts}, {anchor})) * 12 + EXTRACT(MONTH FROM age({ts}, {anchor})))::int)"
    return (f"(CASE WHEN {anchor} + make_interval(months => {est} + 1) <= {ts} THEN {est} + 1 "
            f"WHEN {anchor} + make_interval(months => {est}) > {ts} THEN {est} - 1 ELSE {est} END)")


def allowance_window_start_sql(anchor: str, ts: str) -> str:
    """SQL: start of the allowance month containing ``ts``.

    A paid plan's minutes renew monthly from the purchase (``anchor``), as the
    pricing page says ("next monthly renewal"); a calendar month would give a
    customer who subscribes on the 25th a second full allowance on the 1st.
    Accounts without a paid plan (no anchor) keep calendar months.
    """
    return (f"(CASE WHEN {anchor} IS NULL OR {ts} < {anchor} THEN date_trunc('month', {ts}) "
            f"ELSE {anchor} + make_interval(months => {allowance_window_index_sql(anchor, ts)}) END)")


class MeteringUnavailable(RuntimeError):
    """No authoritative current allowance can be established; retry admission."""


@dataclass(frozen=True)
class MinutesStatus:
    allocated: int | None
    used_minutes: int | None
    remaining_minutes: int | None
    unlimited: bool
    exhausted: bool
    state: Literal["known", "unlimited", "unavailable"] = "known"
    reason: str | None = None

    def require_available(self) -> "MinutesStatus":
        if self.state == "unavailable":
            raise MeteringUnavailable(self.reason or "metering_unavailable")
        return self

    def allowance(self) -> dict[str, Any]:
        return {"minutes_state": self.state,
                "minutes_remaining": self.remaining_minutes if self.state == "known" else None}

    def as_dict(self) -> dict[str, Any]:
        return {"allocated": self.allocated, "used_minutes": self.used_minutes,
                "remaining_minutes": self.remaining_minutes, "unlimited": self.unlimited,
                "exhausted": self.exhausted, "minutes_state": self.state}


def unavailable_minutes(reason: str = "metering_unavailable") -> MinutesStatus:
    return MinutesStatus(None, None, None, False, False, "unavailable", reason)


def _status_from(allocated: Any, used_seconds: Any, *, unlimited: bool = False) -> MinutesStatus:
    if type(allocated) is not int or allocated < 0 or used_seconds is None or isinstance(used_seconds, bool):
        return unavailable_minutes("invalid_metering_data")
    try:
        seconds = Decimal(str(used_seconds))
        if not seconds.is_finite() or seconds < 0:
            return unavailable_minutes("invalid_metering_data")
        used_minutes = int(seconds) // 60
    except (InvalidOperation, ValueError, TypeError):
        return unavailable_minutes("invalid_metering_data")
    if unlimited and allocated == 0:
        return MinutesStatus(0, used_minutes, 0, True, False, "unlimited")
    return MinutesStatus(allocated, used_minutes, max(0, allocated-used_minutes),
                         False, used_minutes >= allocated)


async def _compute_minutes_status(conn: Any, tenant_id: str) -> MinutesStatus:
    # An absent/default tenant allocation cannot prove an unlimited product.
    # The existing bound plan must explicitly carry the zero-minute sentinel.
    row = await conn.fetchrow(
        f"""SELECT t.minutes_allocated,p.minutes AS plan_minutes,
                  t.plan_id IS NULL AS no_plan,
                  EXISTS(SELECT 1 FROM billing_ledger b WHERE b.tenant_id=t.id) AS has_ledger,
                  {allowance_window_start_sql('t.allowance_anchor_at', 'NOW()')} AS window_start
             FROM tenants t LEFT JOIN plans p ON p.id=t.plan_id
            WHERE t.id=$1
              AND (COALESCE(NULLIF(current_setting('app.bypass_rls',true),'')::boolean,false)
                   OR t.id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)""", tenant_id
    )
    if row is None or type(row["minutes_allocated"]) is not int or row["minutes_allocated"] < 0:
        return unavailable_minutes("allowance_unavailable")
    # minutes_allocated is the plan's monthly minutes; bought minutes live
    # only in billing_ledger (topup_service, topup_usage).
    base = allocated = row["minutes_allocated"]
    plan_minutes = row["plan_minutes"]
    unlimited = base == 0 and plan_minutes == 0
    # No plan bound (new accounts since paid-only billing): zero minutes, a
    # known and exhausted allowance rather than an unknown one.
    if base == 0 and not unlimited and not row.get("no_plan") and (type(plan_minutes) is not int or plan_minutes < 0):
        return unavailable_minutes("unlimited_entitlement_unverified")
    if not unlimited and row.get("has_ledger"):
        # Bought minutes still unused (negative after a refund of used ones),
        # less closed months the reminder worker has not recorded yet.
        from app.domain.services.topup_usage import topup_balance
        allocated = max(0, base + await topup_balance(conn, tenant_id, base))
    used_seconds = await conn.fetchval(
        """
        SELECT
            COALESCE((
                SELECT SUM(duration_seconds)
                FROM calls c
                WHERE c.tenant_id=$1
                  AND c.created_at >= COALESCE($2::timestamptz, date_trunc('month',NOW()))
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
                  AND parent.created_at >= COALESCE($2::timestamptz, date_trunc('month',NOW()))
                  AND NOT parent.is_test
                  AND leg.leg_type='transfer'
                  -- A live reservation protects admission capacity, but the
                  -- display/invoice total advances only after terminal proof.
                  AND leg.billing_status='finalized'
            ),0)
        """,
        tenant_id,
        row.get("window_start"),
    )
    # Unlimited is the stored sentinel, never a balance reduced to zero.
    return _status_from(allocated, used_seconds, unlimited=unlimited)


async def inflight_outbound_minutes(conn: Any, tenant_id: str, exclude_call_id: str | None = None) -> tuple[int, int]:
    """(count, minutes to reserve) for outbound calls placed but not settled.

    A live call keeps using minutes the meter cannot see yet, so each one
    reserves the whole minutes it has run since answer plus the minute in
    progress (1 while still ringing). A flat one minute per call let ten long
    calls overrun the allowance. ``exclude_call_id`` is the call being admitted
    when its own row already exists (the bridge re-check).
    """
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES

    row = await conn.fetchrow(
        """SELECT COUNT(*) AS calls,
                  COALESCE(SUM(CASE WHEN c.answered_at IS NULL THEN 1
                      ELSE GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (NOW() - c.answered_at)) / 60))::int + 1
                  END), 0)::int AS minutes
             FROM calls c
            WHERE c.tenant_id=$1 AND NOT c.is_test
              AND c.direction IS DISTINCT FROM 'inbound'
              AND c.duration_seconds IS NULL
              AND c.created_at > NOW() - INTERVAL '6 hours'
              AND COALESCE(c.status,'') <> ALL($2::text[])
              AND ($3::text IS NULL OR c.id::text <> $3::text)""",
        tenant_id,
        list(TERMINAL_CALL_STATUSES),
        exclude_call_id,
    )
    calls, minutes = (row["calls"], row["minutes"]) if row else (None, None)
    if type(calls) is not int or calls < 0 or type(minutes) is not int or minutes < 0:
        raise MeteringUnavailable("inflight_unavailable")
    return calls, minutes


async def count_inflight_outbound_calls(conn: Any, tenant_id: str) -> int:
    """Outbound calls placed but not yet settled (their duration is still NULL).

    The meter above counts settled seconds only, so N concurrent dials all see
    the same remaining balance. Admission reserves one estimate per in-flight
    call. The 6 h window keeps a row that never got a terminal status from
    reserving minutes forever.
    """
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES

    count = await conn.fetchval(
        """SELECT COUNT(*) AS inflight_calls FROM calls c
            WHERE c.tenant_id=$1 AND NOT c.is_test
              AND c.direction IS DISTINCT FROM 'inbound'
              AND c.duration_seconds IS NULL
              AND c.created_at > NOW() - INTERVAL '6 hours'
              AND COALESCE(c.status,'') <> ALL($2::text[])""",
        tenant_id,
        list(TERMINAL_CALL_STATUSES),
    )
    if type(count) is not int or count < 0:
        raise MeteringUnavailable("inflight_unavailable")
    return count


# Short-lived per-tenant reservations that close the guard -> calls-row gap.
# The dialer inserts the calls row AFTER the guard passes, so N simultaneous
# evaluations all read the same in-flight count. This script serialises them.
PENDING_RESERVATION_TTL_SECONDS = 30

_RESERVE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expiry = tonumber(ARGV[2])
local member = ARGV[3]
local base = tonumber(ARGV[4])
local est = tonumber(ARGV[5])
local allocated = tonumber(ARGV[6])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local pending = redis.call('ZCARD', key)
if redis.call('ZSCORE', key, member) then
  pending = pending - 1
end
if base + (pending + 1) * est <= allocated then
  redis.call('ZADD', key, expiry, member)
  redis.call('EXPIRE', key, math.ceil(expiry - now) + 5)
  return 1
end
return 0
"""


async def reserve_outbound_minutes(
    redis: Any,
    tenant_id: str,
    member: str,
    *,
    used: int,
    reserved_inflight: int,
    est_minutes: int,
    allocated: int,
    ttl_seconds: int = PENDING_RESERVATION_TTL_SECONDS,
) -> bool:
    """Atomically check-and-reserve one call's minutes for a tenant.

    Returns True when the call fits (and a pending reservation was recorded),
    False when it would exceed the allowance. Raises on Redis errors so the
    caller can fall back to the non-atomic check.
    """
    import time

    now = time.time()
    result = await redis.eval(
        _RESERVE_LUA, 1, f"minutes:pending:{tenant_id}",
        now, now + ttl_seconds, member,
        int(used) + int(reserved_inflight), int(est_minutes), int(allocated),
    )
    return int(result) == 1


async def compute_minutes_status(conn: Any, tenant_id: str) -> MinutesStatus:
    """One current calendar-month meter; invalid/missing reads remain unknown."""
    try:
        return await _compute_minutes_status(conn, tenant_id)
    except Exception as exc:
        logger.warning("minutes_status_unavailable error_type=%s", type(exc).__name__)
        return unavailable_minutes()


async def tenant_minutes_status(tenant_id: str) -> MinutesStatus:
    """Request-handler adapter; failure never grants an unlimited allowance."""
    try:
        from app.core.db import get_pool
        from app.core.db_utils import acquire_with_tenant
        async with acquire_with_tenant(get_pool(), tenant_id) as conn:
            return await compute_minutes_status(conn, tenant_id)
    except Exception as exc:
        logger.warning("minutes_status_unavailable error_type=%s", type(exc).__name__)
        return unavailable_minutes()
