"""
Dashboard Endpoints
Provides aggregated metrics for the dashboard overview
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter

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
    supabase: Client = Depends(get_supabase)
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
        # Build query with tenant filtering
        calls_query = supabase.table("calls").select("status, duration_seconds")
        calls_query = apply_tenant_filter(calls_query, current_user.tenant_id)
        calls_response = calls_query.execute()
        
        total_calls = 0
        answered_calls = 0
        failed_calls = 0
        total_duration_seconds = 0
        
        if calls_response.data:
            for call in calls_response.data:
                total_calls += 1
                status = call.get("status", "").lower()
                
                if status in ["answered", "completed", "in_progress"]:
                    answered_calls += 1
                    total_duration_seconds += call.get("duration_seconds", 0) or 0
                elif status in ["failed", "no_answer", "busy"]:
                    failed_calls += 1
        
        # Convert seconds to minutes
        minutes_used = total_duration_seconds // 60
        
        # Get active campaigns count with tenant filtering
        campaigns_query = supabase.table("campaigns").select("id").eq("status", "running")
        campaigns_query = apply_tenant_filter(campaigns_query, current_user.tenant_id)
        campaigns_response = campaigns_query.execute()
        active_campaigns = len(campaigns_response.data) if campaigns_response.data else 0
        
        return DashboardSummary(
            total_calls=total_calls,
            answered_calls=answered_calls,
            failed_calls=failed_calls,
            minutes_used=minutes_used,
            minutes_remaining=current_user.minutes_remaining,
            active_campaigns=active_campaigns
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch dashboard summary: {str(e)}"
        )
