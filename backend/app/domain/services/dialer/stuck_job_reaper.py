"""Diagnose stale dialer jobs; queue uncertain calls for proven termination.

Job age never proves that an original Redis payload cannot resume. Diagnostics
retain active ownership; authoritative finalizers/cancellation own release.
"""
from __future__ import annotations

import logging
import os

from app.domain.services.dialer.job_states import IN_FLIGHT_STATUSES

logger = logging.getLogger(__name__)

# How long an in-flight job may live before it's considered stuck. A real
# originate + pre-warm + connect completes in well under this; anything longer
# is hung. Env-overridable for tuning without a redeploy.
DEFAULT_STUCK_TIMEOUT_S = int(os.getenv("DIALER_STUCK_TIMEOUT_S", "120"))
STUCK_REASON = "stuck_reconciliation_required"

# Pre-answer rows have no billable conversation and should settle within the
# carrier ring window. Connected rows are different: inbound transfer policy
# supports calls up to four hours, and ``calls.updated_at`` is not a heartbeat.
# Give the two states separate clocks so a phantom pre-ARI claim still heals
# quickly without terminating a legitimate long conversation.
CALL_STUCK_TIMEOUT_S = int(os.getenv("DIALER_CALL_STUCK_TIMEOUT_S", "600"))
_PREANSWER_CALL_STATUSES = (
    "dialing",
    "ringing",
    "initiated",
)
_CONNECTED_CALL_STATUSES = ("answered", "in_call")
_INFLIGHT_CALL_STATUSES = _PREANSWER_CALL_STATUSES + _CONNECTED_CALL_STATUSES
_MAX_SUPPORTED_CALL_DURATION_S = 14_400


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("invalid %s; using %s", name, default)
        return default


def connected_call_stuck_timeout_seconds(configured: int | None = None) -> int:
    """Return a connected-call timeout above every supported call ceiling."""
    runtime_max = max(1, _env_int("TELEPHONY_MAX_CALL_DURATION_S", 600))
    grace = max(60, _env_int("DIALER_CONNECTED_CALL_GRACE_S", 300))
    safe_floor = max(_MAX_SUPPORTED_CALL_DURATION_S, runtime_max) + grace
    requested = (
        _env_int("DIALER_CONNECTED_CALL_STUCK_TIMEOUT_S", safe_floor)
        if configured is None
        else int(configured)
    )
    return max(safe_floor, requested)

# Call statuses that prove the originate actually landed and a conversation is
# (or was, moments ago) genuinely under way. A job whose linked `calls` row is
# in one of these must NEVER be reaped on the job's own (short, 120s) timeout
# — an answered call routinely runs well past 120s. Deliberately excludes
# "initiated": that status means a call row was created but the provider never
# even confirmed the channel, which is exactly the hung-origination case the
# reaper exists to catch. A call wedged in one of THESE live statuses is still
# bounded — `reap_stuck_calls` uses a short timeout for pre-answer states and
# a four-hour-safe connected timeout, then moves it to proof-aware teardown.
_LIVE_CALL_STATUSES = (
    "dialing",
    "ringing",
    "answered",
    "in_call",
    # Age only moved this call into the proof-aware teardown queue. Until the
    # telephony owner proves every PBX leg absent and commits CallService, its
    # linked job/lead must remain owned; reaping it here could redial while the
    # original PSTN channel is still billable.
    "termination_pending",
)

# ---------------------------------------------------------------------------
# Orphaned retry_scheduled jobs
# ---------------------------------------------------------------------------
# Retained compatibility grace after the actual due time. It is not a maximum
# supported schedule: weekend/business windows can legitimately exceed 48h.
SCHEDULED_STUCK_TIMEOUT_S = int(
    os.getenv("DIALER_SCHEDULED_STUCK_TIMEOUT_S", str(48 * 60 * 60))
)
ORPHANED_SCHEDULED_REASON = "retry_schedule_reconciliation_required"


async def reap_orphaned_scheduled_jobs(
    conn,
    *,
    timeout_seconds: int = SCHEDULED_STUCK_TIMEOUT_S,
) -> int:
    """Flag overdue retries once, retaining the active owner.

    A future due time is authoritative even when its window exceeds 48 hours.
    Age or missing Redis evidence cannot prove that a delayed payload will not
    resume, so this is an unresolved diagnostic, not terminal orphan recovery.
    Returns the number of newly flagged rows; normal finalizers own completion.
    """
    rows = await conn.fetch(
        """
        UPDATE dialer_jobs
           SET failure_category = COALESCE(failure_category, 'internal'),
               failure_reason   = $1,
               last_error       = $1
         WHERE status = 'retry_scheduled'
           AND GREATEST(updated_at, scheduled_at) < now() - make_interval(secs => $2::int)
           AND failure_reason IS DISTINCT FROM $1
        RETURNING id, lead_id
        """,
        ORPHANED_SCHEDULED_REASON,
        int(timeout_seconds),
    )
    reaped = len(rows)
    if reaped:
        logger.warning(
            "reaper: %d overdue retry schedule(s) require reconciliation "
            "(past due + %ss); active ownership retained. lead_ids=%s",
            reaped,
            timeout_seconds,
            [str(r["lead_id"]) for r in rows][:20],
        )
    return reaped


async def reap_stuck_jobs(
    conn,
    *,
    timeout_seconds: int = DEFAULT_STUCK_TIMEOUT_S,
) -> int:
    """Flag stale non-live jobs once without freeing an uncertain owner.

    No linked live call does not prove that an original inflight/scheduled
    payload is absent or unable to resume. Retain status/attempt/due time; only
    authoritative call/job finalization or explicit cancellation releases it.
    Returns newly flagged rows, not recovered or completed jobs.
    """
    rows = await conn.fetch(
        """
        UPDATE dialer_jobs
           SET failure_category = COALESCE(failure_category, 'internal'),
               failure_reason   = $2,
               last_error       = $2
         WHERE status = ANY($1::text[])
           AND GREATEST(updated_at, scheduled_at) < now() - make_interval(secs => $3::int)
           AND failure_reason IS DISTINCT FROM $2
           AND NOT EXISTS (
               SELECT 1 FROM calls c
                WHERE c.dialer_job_id = dialer_jobs.id
                  AND c.status = ANY($4::text[])
           )
        RETURNING id
        """,
        list(IN_FLIGHT_STATUSES),
        STUCK_REASON,
        int(timeout_seconds),
        list(_LIVE_CALL_STATUSES),
    )
    reaped = len(rows)
    if reaped:
        logger.warning(
            "reaper: %d stale dialer job(s) require reconciliation (in-flight > %ss); ownership retained",
            reaped,
            timeout_seconds,
        )
    return reaped


async def reap_stuck_calls(
    conn,
    *,
    timeout_seconds: int = CALL_STUCK_TIMEOUT_S,
    connected_timeout_seconds: int | None = None,
) -> int:
    """Mark stale calls for confirmation-aware owner recovery.

    Pre-answer claims use the short origination timeout. Answered/in-call rows
    use a separately clamped timeout above the platform's four-hour supported
    ceiling plus recovery grace. Age proves the row is stale, but it does *not*
    prove the provider/PBX channel is absent, so both paths move only to the
    nonterminal ``termination_pending`` state. The telephony owner then proves
    every leg absent before normal settlement.

    Idempotent and cheap (one indexed UPDATE); safe on every worker tick.
    """
    connected_timeout = connected_call_stuck_timeout_seconds(
        connected_timeout_seconds
    )
    rows = await conn.fetch(
        """
        UPDATE calls
           SET status     = 'termination_pending',
               updated_at = now()
         WHERE (
                   status = ANY($1::text[])
               AND created_at < now() - make_interval(secs => $2::int)
               )
            OR (
                   status = ANY($3::text[])
               AND COALESCE(answered_at, created_at)
                   < now() - make_interval(secs => $4::int)
               )
        RETURNING id
        """,
        list(_PREANSWER_CALL_STATUSES),
        int(timeout_seconds),
        list(_CONNECTED_CALL_STATUSES),
        connected_timeout,
    )
    reaped = len(rows)
    if reaped:
        logger.warning(
            "reaper: queued %d stuck call(s) for proof-aware termination "
            "(pre-answer > %ss or connected > %ss)",
            reaped,
            timeout_seconds,
            connected_timeout,
        )
    return reaped
