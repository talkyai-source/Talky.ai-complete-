"""Reaper for stuck dialer jobs.

A job that sits in an in-flight status (``processing`` / ``calling``) longer
than its timeout is a zombie: the originate hung — a slow provider, a dropped
worker, the DNS stall we hit — and nothing ever finalized it. Left alone these
accumulate forever (we found one three weeks old), keep the UI showing
"dialing", hold the lead hostage so it can never be retried, and pollute the
Call Issues panel.

The reaper marks them ``failed`` with reason ``stuck_timeout`` so they leave
the pipeline cleanly, free the lead, and surface honestly. It is idempotent and
cheap (one indexed UPDATE), safe to run on every worker tick.

Operates on a raw asyncpg connection — the dialer worker already holds a pool.
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
STUCK_REASON = "stuck_timeout"

# How long a CALL row may sit in a non-terminal status before it's a zombie.
# Must exceed the max plausible live-call lifetime (ring window + the hard call
# ceiling) so a real 8-minute conversation is never reaped mid-call. Default
# 600s. Env-overridable.
CALL_STUCK_TIMEOUT_S = int(os.getenv("DIALER_CALL_STUCK_TIMEOUT_S", "600"))
_INFLIGHT_CALL_STATUSES = (
    "dialing", "ringing", "answered", "in_call", "initiated",
)

# Call statuses that prove the originate actually landed and a conversation is
# (or was, moments ago) genuinely under way. A job whose linked `calls` row is
# in one of these must NEVER be reaped on the job's own (short, 120s) timeout
# — an answered call routinely runs well past 120s. Deliberately excludes
# "initiated": that status means a call row was created but the provider never
# even confirmed the channel, which is exactly the hung-origination case the
# reaper exists to catch. A call wedged in one of THESE live statuses is still
# bounded — `reap_stuck_calls` (CALL_STUCK_TIMEOUT_S, default 600s) closes it
# independently, which drops it out of this set and makes the job reapable on
# the next tick.
_LIVE_CALL_STATUSES = ("dialing", "ringing", "answered", "in_call")


async def reap_stuck_jobs(
    conn,
    *,
    timeout_seconds: int = DEFAULT_STUCK_TIMEOUT_S,
) -> int:
    """Mark in-flight jobs older than ``timeout_seconds`` as failed.

    A job is only reaped if it is BOTH past its timeout AND has no linked
    ``calls`` row currently in a live status (``_LIVE_CALL_STATUSES``). This
    is what keeps a genuinely long-running, answered conversation (which
    routinely exceeds the 120s job timeout) from being reaped mid-call — the
    reaper used to key purely off ``dialer_jobs.updated_at``, with no
    awareness of whether a live call existed for the job at all, so any call
    answered and talking past 120s got its job (and therefore its
    active-job/lead-dedup slot) killed out from under it, freeing the lead to
    be re-enqueued and double-dialed while the first call was still live.

    The linkage is ``calls.dialer_job_id``, populated at call-row INSERT
    (``dialer_worker._create_call_record``) — see that function's 2026-07-13
    fix note. A job that never reached origination (no ``calls`` row at all)
    or whose call never progressed past ``initiated`` has no row satisfying
    the ``EXISTS`` clause below, so it is still reaped normally: this only
    ever narrows which jobs get reaped, it never widens it.

    Args:
        conn: an asyncpg connection (or anything with ``.fetch``).
        timeout_seconds: max age of an in-flight job before it's reaped.

    Returns:
        Number of jobs reaped.
    """
    rows = await conn.fetch(
        """
        UPDATE dialer_jobs
           SET status           = 'failed',
               failure_category = COALESCE(failure_category, 'internal'),
               failure_reason   = $2,
               last_error       = $2,
               updated_at       = now()
         WHERE status = ANY($1::text[])
           AND updated_at < now() - make_interval(secs => $3::int)
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
            "reaper: marked %d stuck dialer job(s) failed (in-flight > %ss)",
            reaped,
            timeout_seconds,
        )
    return reaped


async def reap_stuck_calls(
    conn,
    *,
    timeout_seconds: int = CALL_STUCK_TIMEOUT_S,
) -> int:
    """Close ``calls`` rows stuck in a non-terminal status past the timeout.

    A call that has sat in ``dialing`` / ``ringing`` / ``answered`` / ``in_call``
    longer than the max plausible call lifetime is a zombie: the originate hung,
    or an ARI hangup event was lost, so ``_on_call_ended`` never fired to mark it
    ENDED. Left alone it lingers as "dialing" in the live-calls panel forever AND
    — now that batch dispatch counts in-flight calls — holds a batch slot,
    eventually wedging the campaign (it can never dial the next call). Marking it
    ENDED frees the slot and records an honest terminal state. Any real outcome
    already written is preserved; otherwise it's recorded as ``failed``.

    Idempotent and cheap (one indexed UPDATE); safe on every worker tick.
    """
    rows = await conn.fetch(
        """
        UPDATE calls
           SET status     = 'ended',
               ended_at   = COALESCE(ended_at, now()),
               outcome    = COALESCE(outcome, 'failed'),
               updated_at = now()
         WHERE status = ANY($1::text[])
           AND created_at < now() - make_interval(secs => $2::int)
        RETURNING id
        """,
        list(_INFLIGHT_CALL_STATUSES),
        int(timeout_seconds),
    )
    reaped = len(rows)
    if reaped:
        logger.warning(
            "reaper: closed %d stuck call(s) as ended (in-flight > %ss)",
            reaped,
            timeout_seconds,
        )
    return reaped
