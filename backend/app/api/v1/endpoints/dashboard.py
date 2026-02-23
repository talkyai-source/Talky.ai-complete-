"""
Dashboard Endpoints
Provides aggregated metrics for the dashboard overview
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter

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

        # 2. Answered calls + duration (only matching rows, minimal columns)
        answered_q = db_client.table("calls").select("duration_seconds")
        answered_q = apply_tenant_filter(answered_q, current_user.tenant_id)
        answered_q = answered_q.in_("status", ["answered", "completed", "in_progress"])
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
        
        return DashboardSummary(
            total_calls=total_calls,
            answered_calls=answered_calls,
            failed_calls=failed_calls,
            minutes_used=minutes_used,
            minutes_remaining=current_user.minutes_remaining,
            active_campaigns=active_campaigns
        )
    
    except Exception as e:
        logger.error(f"Failed to fetch dashboard summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch dashboard summary"
        )

