"""
Dashboard Endpoints
Provides aggregated metrics for the dashboard overview
"""
import logging
from datetime import datetime, timezone
from typing import Dict
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter


def _start_of_current_month_utc() -> str:
    """First instant of the current calendar month in UTC, ISO-8601.

    Used to scope minutes-used aggregations to the current billing window.
    Plans bill monthly (`plans.billing_period = 'monthly'`), so usage resets
    at 00:00 UTC on the 1st of each month.
    """
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardSummary(BaseModel):
    """Dashboard summary response.

    Fields are filled in two passes:
      1. Lifetime / current-month aggregates from the `calls` table.
      2. Live state — active_calls and queued_jobs — read from the rows
         the dialer / telephony layer is actively working on right now.

    Frontend KPIs read these directly. The previous dashboard page derived
    `active_calls` and `avg_call_duration_seconds` client-side from
    synthetic per-bucket values (formulas like `total * 0.18 + 6`). Those
    are gone; this response is the authoritative source.
    """
    total_calls: int
    answered_calls: int
    failed_calls: int
    minutes_used: int
    minutes_remaining: int
    active_campaigns: int

    # New live + aggregate fields
    active_calls: int = Field(
        default=0,
        description=(
            "Calls currently in flight for this tenant — status IN "
            "('initiated','ringing','in_progress'). Source of the "
            "Dashboard's 'Active Calls' KPI."
        ),
    )
    avg_call_duration_seconds: int = Field(
        default=0,
        description=(
            "Mean duration_seconds across this tenant's terminal calls "
            "in the current billing month. Source of the Dashboard's "
            "'Avg Duration' KPI."
        ),
    )
    queued_jobs: int = Field(
        default=0,
        description=(
            "Pending dialer_jobs for this tenant — status IN "
            "('pending','retry_scheduled'). Drives the call-stats hover "
            "card on the dashboard."
        ),
    )
    outcome_breakdown: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Counts of `calls.outcome` values for the current billing "
            "month. Source of the Dashboard's outcomes pie chart "
            "(replaces synthesised completed/voicemail/callback splits)."
        ),
    )


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get aggregated dashboard metrics.
    
    Used by: /dashboard main page overview widgets.
    
    Returns:
        - Total calls count
        - Answered/failed call breakdown
        - Minutes usage
        - Active campaigns count
    """
    try:
        # 1. Total calls count (uses PostgreSQL count, no rows transferred)
        total_q = db_client.table("calls").select("id", count="exact")
        total_q = apply_tenant_filter(total_q, current_user.tenant_id)
        total_resp = total_q.execute()
        total_calls = total_resp.count or 0

        # 2. Answered calls + duration for the CURRENT BILLING MONTH only.
        #    Plans bill monthly — minutes_used resets to 0 at the 1st UTC of
        #    each calendar month so a fresh allocation is available without
        #    any cron job or column update. Lifetime totals (`total_calls`,
        #    `failed_calls` above/below) intentionally stay unfiltered for
        #    historical context; only minutes-used is windowed.
        month_start_iso = _start_of_current_month_utc()
        answered_q = db_client.table("calls").select("duration_seconds")
        answered_q = apply_tenant_filter(answered_q, current_user.tenant_id)
        answered_q = answered_q.in_("status", ["answered", "completed", "in_progress"])
        answered_q = answered_q.gte("created_at", month_start_iso)
        answered_resp = answered_q.execute()
        answered_calls = len(answered_resp.data) if answered_resp.data else 0
        total_duration_seconds = sum(
            c.get("duration_seconds", 0) or 0 for c in (answered_resp.data or [])
        )

        # 3. Failed calls count (uses PostgreSQL count, no rows transferred)
        failed_q = db_client.table("calls").select("id", count="exact")
        failed_q = apply_tenant_filter(failed_q, current_user.tenant_id)
        failed_q = failed_q.in_("status", ["failed", "no_answer", "busy"])
        failed_resp = failed_q.execute()
        failed_calls = failed_resp.count or 0

        # Convert seconds to minutes
        minutes_used = total_duration_seconds // 60
        
        # Get active campaigns count with tenant filtering
        campaigns_query = db_client.table("campaigns").select("id", count="exact").eq("status", "running")
        campaigns_query = apply_tenant_filter(campaigns_query, current_user.tenant_id)
        campaigns_response = campaigns_query.execute()
        active_campaigns = campaigns_response.count or 0

        # Live minutes-remaining: allocation from the tenant's plan minus the
        # current month's actual usage from `calls`. The tenants.minutes_used
        # column is intentionally not consulted — it's never written by any
        # call-end hook and would always read 0, making minutes_remaining
        # always equal allocation regardless of usage.
        tenant_q = db_client.table("tenants").select("minutes_allocated").eq(
            "id", current_user.tenant_id
        )
        tenant_resp = tenant_q.execute()
        minutes_allocated = (
            (tenant_resp.data[0].get("minutes_allocated") or 0)
            if tenant_resp.data
            else 0
        )
        minutes_remaining = max(0, minutes_allocated - minutes_used)

        # 4. Active calls — anything currently being placed / on the line
        # for this tenant. Used as the Dashboard's "Active Calls" KPI.
        active_q = db_client.table("calls").select("id", count="exact")
        active_q = apply_tenant_filter(active_q, current_user.tenant_id)
        active_q = active_q.in_("status", ["initiated", "ringing", "in_progress"])
        active_resp = active_q.execute()
        active_calls = active_resp.count or 0

        # 5. Average call duration in the current billing month.
        # Reuses the same answered_resp.data we already loaded above so we
        # don't pay for a second SELECT. We compute the mean only over rows
        # that have a non-null duration_seconds — the row exists at
        # status='in_progress' before duration is written, and counting
        # those as 0 would drag the mean down for tenants with active calls.
        durations: list[int] = [
            int(c.get("duration_seconds", 0) or 0)
            for c in (answered_resp.data or [])
            if (c.get("duration_seconds") or 0) > 0
        ]
        avg_call_duration_seconds = (
            int(round(sum(durations) / len(durations))) if durations else 0
        )

        # 6. Queued dialer_jobs — pending work the dialer worker hasn't
        # started yet. Drives the dashboard's hover-card "Queue size".
        try:
            queue_q = db_client.table("dialer_jobs").select("id", count="exact")
            queue_q = apply_tenant_filter(queue_q, current_user.tenant_id)
            queue_q = queue_q.in_("status", ["pending", "retry_scheduled"])
            queued_jobs = queue_q.execute().count or 0
        except Exception:
            # dialer_jobs may be empty / not yet provisioned for new
            # tenants; treat as zero rather than 500.
            queued_jobs = 0

        # 7. Outcome breakdown for the current billing month.
        # Used by the dashboard's outcomes pie chart (which previously
        # invented completed/voicemail/callback ratios).
        outcome_q = db_client.table("calls").select("outcome")
        outcome_q = apply_tenant_filter(outcome_q, current_user.tenant_id)
        outcome_q = outcome_q.gte("created_at", month_start_iso)
        outcome_resp = outcome_q.execute()
        outcome_breakdown: Dict[str, int] = {}
        for row in outcome_resp.data or []:
            key = (row.get("outcome") or "unknown") or "unknown"
            outcome_breakdown[key] = outcome_breakdown.get(key, 0) + 1

        return DashboardSummary(
            total_calls=total_calls,
            answered_calls=answered_calls,
            failed_calls=failed_calls,
            minutes_used=minutes_used,
            minutes_remaining=minutes_remaining,
            active_campaigns=active_campaigns,
            active_calls=active_calls,
            avg_call_duration_seconds=avg_call_duration_seconds,
            queued_jobs=queued_jobs,
            outcome_breakdown=outcome_breakdown,
        )
    
    except Exception as e:
        logger.error(f"Failed to fetch dashboard summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch dashboard summary"
        )

