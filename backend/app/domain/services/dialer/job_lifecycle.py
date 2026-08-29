"""Campaign / list ↔ job lifecycle.

A dialer job exists only to dial a lead that is an active member of a running
campaign. The moment that stops being true — the campaign is stopped/paused, or
the number is removed from it — the job must leave the pipeline. Otherwise it
lingers as a zombie "dialing" row and (without the dedup index) can even
re-dial a number you've removed.

Campaign cancellation touches only jobs that provably have not originated;
in-flight jobs retain ownership until PBX proof and normal settlement. Lead
removal retains its older all-active behavior pending a confirmation-aware
removal workflow. ``cancelled`` is a terminal status distinct from ``skipped``
(a transient per-call gate), so call history stays honest about WHY a job
stopped: a deliberate removal, not a momentary skip.

Uses the Supabase-style ``db_client`` adapter to match the campaign service
that calls it (``.table(...).update(...).in_(...).execute()``).
"""
from __future__ import annotations

import logging

from app.domain.services.dialer.job_states import ACTIVE_STATUSES

logger = logging.getLogger(__name__)

# Reasons (kept as constants so the UI / Call Issues advice can map them).
REASON_CAMPAIGN_STOPPED = "campaign_stopped"
REASON_CAMPAIGN_PAUSED = "campaign_paused"
REASON_LEAD_REMOVED = "removed_from_campaign"

# These states prove the worker has not claimed/originated the attempt. A
# campaign stop may cancel them immediately. ``processing`` and ``calling``
# deliberately remain active until PBX absence is proven and normal
# CallService settlement commits; cancelling them here would release the lead
# while a carrier leg may still be live.
NOT_ORIGINATED_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "queued",
    "retry_scheduled",
)


def _rowcount(result) -> int:
    return len(getattr(result, "data", None) or [])


def cancel_active_jobs_for_campaign(db_client, campaign_id: str, *, reason: str) -> int:
    """Cancel only not-yet-originated jobs for a campaign.

    In-flight ``processing``/``calling`` jobs are intentionally excluded: the
    campaign termination sweep owns their PBX teardown, and CallService owns
    the terminal job transition after provider absence is confirmed.
    Idempotent: only pre-origination states are touched, so re-running is a
    no-op once the queued pipeline is clear.
    """
    result = (
        db_client.table("dialer_jobs")
        .update({
            "status": "cancelled",
            "failure_reason": reason,
            "last_error": reason,
        })
        .eq("campaign_id", str(campaign_id))
        .in_("status", list(NOT_ORIGINATED_JOB_STATUSES))
        .execute()
    )
    n = _rowcount(result)
    if n:
        logger.info(
            "job_lifecycle: cancelled %d not-originated job(s) for campaign %s (%s)",
            n,
            campaign_id,
            reason,
        )
    return n


def cancel_active_jobs_for_lead(db_client, lead_id: str, *, reason: str = REASON_LEAD_REMOVED) -> int:
    """Cancel every active job for a single lead. Returns the count cancelled."""
    result = (
        db_client.table("dialer_jobs")
        .update({
            "status": "cancelled",
            "failure_reason": reason,
            "last_error": reason,
        })
        .eq("lead_id", str(lead_id))
        .in_("status", list(ACTIVE_STATUSES))
        .execute()
    )
    n = _rowcount(result)
    if n:
        logger.info("job_lifecycle: cancelled %d active job(s) for lead %s (%s)", n, lead_id, reason)
    return n
