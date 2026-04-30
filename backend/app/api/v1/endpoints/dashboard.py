"""
Dashboard Endpoints
Provides aggregated metrics for the dashboard overview
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
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
    """Dashboard summary response"""
    total_calls: int
    answered_calls: int
    failed_calls: int
    minutes_used: int
    minutes_remaining: int
    active_campaigns: int


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

        return DashboardSummary(
            total_calls=total_calls,
            answered_calls=answered_calls,
            failed_calls=failed_calls,
            minutes_used=minutes_used,
            minutes_remaining=minutes_remaining,
            active_campaigns=active_campaigns
        )
    
    except Exception as e:
        logger.error(f"Failed to fetch dashboard summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch dashboard summary"
        )

