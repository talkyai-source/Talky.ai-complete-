"""
Analytics Endpoints
Provides call analytics with date range and grouping
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime, time, timedelta, timezone
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
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
    db_client: Client = Depends(get_db_client)
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
        if group_by not in {"day", "week", "month"}:
            raise HTTPException(
                status_code=400,
                detail="Invalid group_by. Must be one of: day, week, month",
            )

        # Parse dates with defaults (inclusive range on provided dates).
        if to_date:
            end_date = date.fromisoformat(to_date)
        else:
            end_date = datetime.now(timezone.utc).date()

        if from_date:
            start_date = date.fromisoformat(from_date)
        else:
            start_date = end_date - timedelta(days=30)

        if start_date > end_date:
            raise HTTPException(
                status_code=400,
                detail="'from' date cannot be later than 'to' date",
            )

        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt_exclusive = datetime.combine(
            end_date + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        )

        # Query calls within date range with tenant filtering.
        query = db_client.table("calls").select(
            "created_at, status"
        ).gte(
            "created_at", start_dt
        ).lt(
            "created_at", end_dt_exclusive
        )
        query = apply_tenant_filter(query, current_user.tenant_id)
        response = query.order("created_at").execute()

        if response.error:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch analytics: {response.error}",
            )
        
        if not response.data:
            return CallAnalyticsResponse(series=[])
        
        # Group calls by date
        date_groups = {}
        
        for call in response.data:
            created_at = call.get("created_at")
            if not created_at:
                continue
            
            # Parse timestamp and get date key based on grouping
            try:
                if isinstance(created_at, datetime):
                    dt = created_at
                elif isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                else:
                    continue

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                
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
    
    except HTTPException:
        raise
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
