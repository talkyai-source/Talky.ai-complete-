"""In-call opt-out → instant Do-Not-Call purge.

When a caller asks never to be contacted again, honoring it must be
*immediate and total* — not "we'll stop after the current retry cycle."
This module performs the one-shot purge that the call-end teardown runs
when the live agent flagged an opt-out (``session._caller_opted_out``):

  1. **DNC** the number (permanent ``caller_opt_out`` entry) so CallGuard
     blocks every future origination to it, across the tenant.
  2. **Cancel** every active/scheduled dialer job for the lead, so a
     retry already sitting in the queue can never fire. (Belt-and-braces
     with #1 — CallGuard would block it anyway, but a cancelled job is
     honest in the history and frees the slot.)
  3. **Mark the lead DNC** so the UI and future campaign adds reflect it.

Each step is best-effort and independent: a failure in one is logged but
never blocks the others, because a half-applied opt-out (e.g. DNC added
but a job left queued) is exactly the compliance gap we're closing. The
whole thing is idempotent — re-running on an already-purged lead is a
no-op.

Two DB handles are needed because the codebase has two adapters:
``db_pool`` (asyncpg) for :class:`DNCService`, and the Supabase-style
``db_client`` for the dialer-job / lead writes (matching the rest of the
dialer lifecycle helpers).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.domain.services.dnc_service import DNCService
from app.domain.services.dialer.job_lifecycle import cancel_active_jobs_for_lead

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
    result = {"dnc_added": False, "jobs_cancelled": 0, "lead_marked": False}

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

    # 2. Cancel every active/scheduled job for the lead.
    if lead_id and db_client is not None:
        try:
            result["jobs_cancelled"] = cancel_active_jobs_for_lead(
                db_client, str(lead_id), reason=OPT_OUT_REASON,
            )
        except Exception as exc:
            logger.warning(
                "opt_out_purge: job cancel failed lead=%s err=%s", lead_id, exc,
            )

        # 3. Mark the lead do-not-call.
        try:
            db_client.table("leads").update({
                "status": "dnc",
                "last_call_result": OPT_OUT_REASON,
            }).eq("id", str(lead_id)).execute()
            result["lead_marked"] = True
        except Exception as exc:
            logger.warning(
                "opt_out_purge: lead DNC mark failed lead=%s err=%s", lead_id, exc,
            )

    logger.info(
        "opt_out_purge done lead=%s number=%s dnc=%s jobs_cancelled=%d lead_marked=%s",
        lead_id, phone_number, result["dnc_added"],
        result["jobs_cancelled"], result["lead_marked"],
    )
    return result
