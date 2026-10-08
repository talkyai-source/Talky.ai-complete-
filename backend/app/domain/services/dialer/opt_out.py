"""In-call opt-out → instant Do-Not-Call purge.

When a caller asks never to be contacted again, honoring it must be
*immediate and total* — not "we'll stop after the current retry cycle."
This module performs the one-shot purge that the call-end teardown runs
when the live agent flagged an opt-out (``session._caller_opted_out``):

  1. **DNC** the number (permanent ``caller_opt_out`` entry) so CallGuard
     blocks every future origination to it, across the tenant.
  2. **Cancel** not-yet-originated dialer jobs for the lead, so a
     retry already sitting in the queue can never fire. (Belt-and-braces
     with #1 — CallGuard would block it anyway, but a cancelled job is
     honest in the history and frees the slot.)
  3. **Mark the lead DNC** so the UI and future campaign adds reflect it.

Each step is best-effort and independent: a failure in one is logged but
never blocks the others, because a half-applied opt-out (e.g. DNC added
but a job left queued) is exactly the compliance gap we're closing. The
whole thing is idempotent — re-running on an already-purged lead is a
no-op.

All writes use the tenant-scoped asyncpg pool so bounded in-call persistence
does not block audio on the synchronous compatibility adapter. ``db_client``
remains an accepted keyword for existing callers. Processing/calling jobs
retain ownership until normal, provider-proven final settlement.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.domain.services.dnc_service import DNCService
from app.core.db_utils import acquire_with_tenant
from app.domain.services.dialer.job_lifecycle import NOT_ORIGINATED_JOB_STATUSES

logger = logging.getLogger(__name__)

# Single reason string stamped on the DNC entry, the cancelled jobs, and
# the lead row so the whole purge is traceable to one cause.
OPT_OUT_REASON = "caller_opt_out"


async def purge_lead_on_opt_out(
    *,
    db_pool: Any,
    db_client: Any,
    tenant_id: Optional[str],
    lead_id: Optional[str],
    phone_number: Optional[str],
    call_id: Optional[str] = None,
) -> dict:
    """Honor an in-call opt-out across DNC, jobs, and the lead row.

    Returns a small result dict for logging/telemetry; never raises — a
    teardown path must not be torpedoed by a compliance side effect, and
    the individual failures are logged for follow-up.
    """
    result = {"dnc_added": False, "jobs_cancelled": 0, "lead_marked": False,
              "jobs_cleanup_complete": not bool(lead_id), "purge_complete": False}

    # 1. DNC the number (blocks all future origination via CallGuard).
    if phone_number and tenant_id and db_pool is not None:
        try:
            await DNCService(db_pool).add_caller_opt_out(
                tenant_id=str(tenant_id), e164=str(phone_number), call_id=call_id,
            )
            result["dnc_added"] = True
        except Exception as exc:
            logger.warning(
                "opt_out_purge: DNC add failed tenant=%s number=%s err=%s",
                tenant_id, phone_number, exc,
            )

    # 2. Cancel future work, retaining live attempt ownership. An empty UPDATE
    # is a successful queue cleanup; it is distinct from a failed DB request.
    if lead_id and tenant_id and db_pool is not None:
        try:
            async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
                status = await conn.execute(
                    """UPDATE dialer_jobs SET status='cancelled',
                           failure_reason=$3, last_error=$3
                       WHERE tenant_id=$1::uuid AND lead_id=$2::uuid
                         AND status = ANY($4::text[])""",
                    str(tenant_id), str(lead_id), OPT_OUT_REASON,
                    list(NOT_ORIGINATED_JOB_STATUSES),
                )
            if not isinstance(status, str) or not status.startswith("UPDATE "):
                raise RuntimeError("Job cleanup returned no update acknowledgement")
            result["jobs_cancelled"] = int(status.split()[-1])
            result["jobs_cleanup_complete"] = True
        except Exception as exc:
            logger.warning(
                "opt_out_purge: job cancel failed lead=%s err=%s", lead_id, exc,
            )

        # 3. Mark the lead do-not-call.
        try:
            async with acquire_with_tenant(db_pool, str(tenant_id)) as conn:
                row = await conn.fetchrow(
                    """UPDATE leads SET status='dnc', last_call_result=$3
                       WHERE tenant_id=$1::uuid AND id=$2::uuid RETURNING id""",
                    str(tenant_id), str(lead_id), OPT_OUT_REASON,
                )
            result["lead_marked"] = row is not None
        except Exception as exc:
            logger.warning(
                "opt_out_purge: lead DNC mark failed lead=%s err=%s", lead_id, exc,
            )

    result["purge_complete"] = bool(result["dnc_added"] and
        result["jobs_cleanup_complete"] and (not lead_id or result["lead_marked"]))
    logger.info(
        "opt_out_purge done lead=%s number=%s dnc=%s jobs_cancelled=%d lead_marked=%s",
        lead_id, phone_number, result["dnc_added"],
        result["jobs_cancelled"], result["lead_marked"],
    )
    return result


def record_purge_result(voice_session, result: dict) -> bool:
    """Remember acknowledged DNC separately from all applicable cleanup.

    A failed or cancelled attempt cannot create proof. Prior acknowledged DNC
    remains true while ancillary work is retried. This is process-local receipt
    state, not a durable recovery queue or a claim about caller hearing.
    """
    if result.get("dnc_added") is True:
        voice_session._opt_out_dnc_written = True
    if result.get("purge_complete") is True:
        voice_session._opt_out_purged = True
    return bool(getattr(voice_session, "_opt_out_dnc_written", False))


# Spoken when the caller asked to be removed but the DNC write did not
# succeed in time. It commits to nothing the system has not done; the
# teardown retries the purge, and the transcript shows what was actually said.
OPT_OUT_UNCONFIRMED_FAREWELL = (
    "Understood — I've noted that and won't keep you. Goodbye."
)


async def purge_opt_out_before_farewell(session, *, timeout_s: float = 2.5) -> bool:
    """Write the opt-out before the agent says it has; see the helper below.

    On success the live session is marked ``_opt_out_recorded`` so a spoken
    "you've been removed" is allowed (speech_guard.py); otherwise it is not.
    One in-call attempt per call: after a failure, later in-call callers get
    False at once instead of another 2.5 s of dead air each; the teardown
    purge (lifecycle.py) retries the write.
    """
    # No early return on success: a written DNC row with incomplete cleanup
    # (queued jobs, lead status) must be retried by the next caller.
    if getattr(session, "_opt_out_inline_failed", False) is True:
        return False
    recorded = await _purge_opt_out_before_farewell(session, timeout_s=timeout_s)
    try:
        if recorded:
            session._opt_out_recorded = True
        else:
            session._opt_out_inline_failed = True
    except Exception:  # noqa: BLE001 - foreign session doubles
        pass
    return recorded


async def _purge_opt_out_before_farewell(session, *, timeout_s: float = 2.5) -> bool:
    """Write the opt-out BEFORE the agent says it has.

    Called from the end-action shutdown path with the live ``CallSession``.
    Resolves the owning voice session (which carries the dialer's tenant /
    lead / phone), runs :func:`purge_lead_on_opt_out` under a short timeout
    so the caller is not left in silence. Only full acknowledged cleanup marks
    ``_opt_out_purged``; otherwise teardown retries the incomplete work.

    Returns True only when the DNC row was actually written (or already
    had been). False means the spoken farewell must not claim removal.
    Never raises.
    """
    call_id = str(getattr(session, "call_id", "") or "")
    try:
        from app.domain.services.telephony.lifecycle import _state

        voice_session = _state().get_voice_session(call_id) if call_id else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("opt_out_pre_farewell: no voice session call=%s err=%s", call_id[:12], exc)
        voice_session = None
    if voice_session is None:
        return False
    if getattr(voice_session, "_opt_out_purged", False):
        return True

    tenant_id = getattr(voice_session, "_dialer_tenant_id", None)
    phone = getattr(voice_session, "_dialer_phone", None)
    if not (tenant_id and phone):
        logger.warning(
            "opt_out_pre_farewell: missing tenant/phone call=%s tenant=%s phone=%s",
            call_id[:12], bool(tenant_id), bool(phone),
        )
        return False

    try:
        from app.core.container import get_container

        container = get_container()
        if not container.is_initialized:
            return False
        import asyncio

        result = await asyncio.wait_for(
            purge_lead_on_opt_out(
                db_pool=container.db_pool,
                db_client=container.db_client,
                tenant_id=str(tenant_id),
                lead_id=getattr(voice_session, "_dialer_lead_id", None),
                phone_number=str(phone),
                call_id=getattr(voice_session, "_dialer_call_id", None),
            ),
            timeout=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — includes TimeoutError
        logger.error(
            "opt_out_pre_farewell_failed call=%s err=%r — farewell will not claim removal; "
            "teardown retries",
            call_id[:12], exc,
        )
        return bool(getattr(voice_session, "_opt_out_dnc_written", False))

    return record_purge_result(voice_session, result)
