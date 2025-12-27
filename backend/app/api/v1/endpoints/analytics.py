"""
Analytics Endpoints
Provides call analytics with date range and grouping
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter

router = APIRouter(prefix="/analytics", tags=["analytics"])


class CallSeriesItem(BaseModel):
    """Single data point in call analytics series"""
    date: str
    total_calls: int
    answered: int
    failed: int


class CallAnalyticsResponse(BaseModel):
    """Call analytics response"""
    series: List[CallSeriesItem]


@router.get("/calls", response_model=CallAnalyticsResponse)
async def get_call_analytics(
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    group_by: str = Query("day", description="Grouping: day, week, month"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get call analytics with date range and grouping.
    
    Used by: /dashboard/analytics page.
    
    Query params:
        - from: Start date (YYYY-MM-DD), defaults to 30 days ago
        - to: End date (YYYY-MM-DD), defaults to today
        - group_by: day, week, or month
    """
    try:
        # Parse dates with defaults
        if to_date:
            end_dt = datetime.strptime(to_date, "%Y-%m-%d")
        else:
            end_dt = datetime.utcnow()
        
        if from_date:
            start_dt = datetime.strptime(from_date, "%Y-%m-%d")
        else:
            start_dt = end_dt - timedelta(days=30)
        
        # Query calls within date range with tenant filtering
        query = supabase.table("calls").select(
            "created_at, status"
        ).gte(
            "created_at", start_dt.isoformat()
        ).lte(
            "created_at", end_dt.isoformat()
        )
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.order("created_at").execute()
        
        if not response.data:
            return CallAnalyticsResponse(series=[])
        
        # Group calls by date
        date_groups = {}
        
        for call in response.data:
            created_at = call.get("created_at", "")
            if not created_at:
                continue
            
            # Parse timestamp and get date key based on grouping
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                
                if group_by == "week":
                    # Week starts on Monday
                    week_start = dt - timedelta(days=dt.weekday())
                    date_key = week_start.strftime("%Y-%m-%d")
                elif group_by == "month":
                    date_key = dt.strftime("%Y-%m-01")
                else:  # day
                    date_key = dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
            
            if date_key not in date_groups:
                date_groups[date_key] = {"total": 0, "answered": 0, "failed": 0}
            
            date_groups[date_key]["total"] += 1
            
            status = call.get("status", "").lower()
            if status in ["answered", "completed", "in_progress"]:
                date_groups[date_key]["answered"] += 1
            elif status in ["failed", "no_answer", "busy"]:
                date_groups[date_key]["failed"] += 1
        
        # Convert to series
        series = []
        for date_key in sorted(date_groups.keys()):
            data = date_groups[date_key]
            series.append(CallSeriesItem(
                date=date_key,
                total_calls=data["total"],
                answered=data["answered"],
                failed=data["failed"]
            ))
        
        return CallAnalyticsResponse(series=series)
    
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch analytics: {str(e)}"
        )
