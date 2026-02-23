"""
Admin Calls Endpoints
Call monitoring: live calls, call history, call detail, terminate
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, require_admin, CurrentUser

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class TimelineEvent(BaseModel):
    """Timeline event for call detail"""
    event: str
    timestamp: str
    status: Optional[str] = None


class LiveCallItem(BaseModel):
    """Active call item for live calls table"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_name: Optional[str] = None
    status: str  # 'in_progress', 'ringing', 'queued'
    started_at: Optional[str] = None
    duration_seconds: int = 0


class CallHistoryItem(BaseModel):
    """Call history item with tenant info"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_name: Optional[str] = None
    status: str
    outcome: Optional[str] = None
    duration_seconds: Optional[int] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: str


class CallHistoryResponse(BaseModel):
    """Paginated call history response"""
    items: List[CallHistoryItem]
    page: int
    page_size: int
    total: int


class AdminCallDetail(BaseModel):
    """Full call detail for admin view"""
    id: str
    tenant_id: str
    tenant_name: str
    phone_number: str
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    lead_id: Optional[str] = None
    status: str
    outcome: Optional[str] = None
    goal_achieved: bool = False
    started_at: Optional[str] = None
    answered_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None
    transcript_json: Optional[list] = None
    summary: Optional[str] = None
    recording_url: Optional[str] = None
    cost: Optional[float] = None
    timeline: List[TimelineEvent]
    created_at: str
    updated_at: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/calls/live", response_model=List[LiveCallItem])
async def get_live_calls(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get list of currently active calls.
    
    Returns calls with status: in_progress, ringing, queued, initiated.
    Includes tenant name and campaign name for context.
    """
    try:
        # Query active calls
        active_statuses = ['in_progress', 'ringing', 'queued', 'initiated']
        calls_response = db_client.table("calls").select(
            "id, tenant_id, phone_number, status, started_at, campaign_id"
        ).in_("status", active_statuses).order("started_at", desc=True).execute()
        
        if not calls_response.data:
            return []
        
        # Collect tenant IDs and campaign IDs for batch lookup
        tenant_ids = list(set(c.get("tenant_id") for c in calls_response.data if c.get("tenant_id")))
        campaign_ids = list(set(c.get("campaign_id") for c in calls_response.data if c.get("campaign_id")))
        
        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = db_client.table("tenants").select("id, business_name").in_("id", tenant_ids).execute()
            for t in (tenants_response.data or []):
                tenant_map[t["id"]] = t["business_name"]
        
        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            for c in (campaigns_response.data or []):
                campaign_map[c["id"]] = c["name"]
        
        # Calculate duration for active calls
        now = datetime.utcnow()
        items = []
        for call in calls_response.data:
            duration = 0
            started_at = call.get("started_at")
            if started_at:
                try:
                    start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00").replace("+00:00", ""))
                    duration = int((now - start_dt).total_seconds())
                except (ValueError, TypeError):
                    duration = 0
            
            items.append(LiveCallItem(
                id=call["id"],
                tenant_id=call.get("tenant_id", ""),
                tenant_name=tenant_map.get(call.get("tenant_id"), "Unknown"),
                phone_number=call.get("phone_number", ""),
                campaign_name=campaign_map.get(call.get("campaign_id")),
                status=call.get("status", "unknown"),
                started_at=started_at,
                duration_seconds=max(0, duration)
            ))
        
        return items
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch live calls: {str(e)}"
        )


@router.get("/calls/history", response_model=CallHistoryResponse)
async def get_call_history(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    tenant_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
):
    """
    Get paginated call history with filters.
    
    Query params:
        - page: Page number (1-indexed)
        - page_size: Items per page (max 100)
        - search: Search by phone number
        - status: Filter by call status
        - tenant_id: Filter by tenant
        - from_date: Start date (YYYY-MM-DD)
        - to_date: End date (YYYY-MM-DD)
    """
    try:
        # Build query
        query = db_client.table("calls").select(
            "id, tenant_id, phone_number, campaign_id, status, outcome, duration_seconds, started_at, ended_at, created_at",
            count="exact"
        )
        
        # Apply filters
        if search:
            query = query.ilike("phone_number", f"%{search}%")
        
        if status:
            query = query.eq("status", status)
        
        if tenant_id:
            query = query.eq("tenant_id", tenant_id)
        
        if from_date:
            query = query.gte("created_at", from_date)
        
        if to_date:
            query = query.lte("created_at", to_date + "T23:59:59Z")
        
        # Calculate offset
        offset = (page - 1) * min(page_size, 100)
        
        # Execute with pagination
        response = query.order("created_at", desc=True).range(offset, offset + min(page_size, 100) - 1).execute()
        
        total = response.count if response.count else 0
        
        if not response.data:
            return CallHistoryResponse(items=[], page=page, page_size=page_size, total=total)
        
        # Collect tenant and campaign IDs
        tenant_ids = list(set(c.get("tenant_id") for c in response.data if c.get("tenant_id")))
        campaign_ids = list(set(c.get("campaign_id") for c in response.data if c.get("campaign_id")))
        
        # Fetch tenant names
        tenant_map = {}
        if tenant_ids:
            tenants_response = db_client.table("tenants").select("id, business_name").in_("id", tenant_ids).execute()
            for t in (tenants_response.data or []):
                tenant_map[t["id"]] = t["business_name"]
        
        # Fetch campaign names
        campaign_map = {}
        if campaign_ids:
            campaigns_response = db_client.table("campaigns").select("id, name").in_("id", campaign_ids).execute()
            for c in (campaigns_response.data or []):
                campaign_map[c["id"]] = c["name"]
        
        # Build items
        items = []
        for call in response.data:
            items.append(CallHistoryItem(
                id=call["id"],
                tenant_id=call.get("tenant_id", ""),
                tenant_name=tenant_map.get(call.get("tenant_id"), "Unknown"),
                phone_number=call.get("phone_number", ""),
                campaign_name=campaign_map.get(call.get("campaign_id")),
                status=call.get("status", "unknown"),
                outcome=call.get("outcome"),
                duration_seconds=call.get("duration_seconds"),
                started_at=call.get("started_at"),
                ended_at=call.get("ended_at"),
                created_at=call.get("created_at", "")
            ))
        
        return CallHistoryResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch call history: {str(e)}"
        )


@router.get("/calls/{call_id}", response_model=AdminCallDetail)
async def get_admin_call_detail(
    call_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get full call details for admin view.
    
    Returns complete call information including:
    - Transcript (text and JSON)
    - Timeline of events
    - Recording URL
    - Cost
    """
    try:
        # Fetch call
        call_response = db_client.table("calls").select("*").eq("id", call_id).single().execute()
        
        if not call_response.data:
            raise HTTPException(status_code=404, detail="Call not found")
        
        call = call_response.data
        
        # Fetch tenant name
        tenant_name = "Unknown"
        if call.get("tenant_id"):
            tenant_response = db_client.table("tenants").select("business_name").eq("id", call["tenant_id"]).single().execute()
            if tenant_response.data:
                tenant_name = tenant_response.data.get("business_name", "Unknown")
        
        # Fetch campaign name
        campaign_name = None
        if call.get("campaign_id"):
            campaign_response = db_client.table("campaigns").select("name").eq("id", call["campaign_id"]).single().execute()
            if campaign_response.data:
                campaign_name = campaign_response.data.get("name")
        
        # Build timeline
        timeline = []
        if call.get("created_at"):
            timeline.append(TimelineEvent(
                event="Call Initiated",
                timestamp=call["created_at"],
                status="initiated"
            ))
        if call.get("started_at"):
            timeline.append(TimelineEvent(
                event="Call Started",
                timestamp=call["started_at"],
                status="ringing"
            ))
        if call.get("answered_at"):
            timeline.append(TimelineEvent(
                event="Call Answered",
                timestamp=call["answered_at"],
                status="in_progress"
            ))
        if call.get("ended_at"):
            timeline.append(TimelineEvent(
                event="Call Ended",
                timestamp=call["ended_at"],
                status=call.get("status", "completed")
            ))
        
        # Parse transcript_json if string
        transcript_json = call.get("transcript_json")
        if isinstance(transcript_json, str):
            import json
            try:
                transcript_json = json.loads(transcript_json)
            except (json.JSONDecodeError, TypeError):
                transcript_json = None
        
        return AdminCallDetail(
            id=call["id"],
            tenant_id=call.get("tenant_id", ""),
            tenant_name=tenant_name,
            phone_number=call.get("phone_number", ""),
            campaign_id=call.get("campaign_id"),
            campaign_name=campaign_name,
            lead_id=call.get("lead_id"),
            status=call.get("status", "unknown"),
            outcome=call.get("outcome"),
            goal_achieved=call.get("goal_achieved", False),
            started_at=call.get("started_at"),
            answered_at=call.get("answered_at"),
            ended_at=call.get("ended_at"),
            duration_seconds=call.get("duration_seconds"),
            transcript=call.get("transcript"),
            transcript_json=transcript_json,
            summary=call.get("summary"),
            recording_url=call.get("recording_url"),
            cost=float(call["cost"]) if call.get("cost") else None,
            timeline=timeline,
            created_at=call.get("created_at", ""),
            updated_at=call.get("updated_at")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch call detail: {str(e)}"
        )


@router.post("/calls/{call_id}/terminate")
async def terminate_call(
    call_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Terminate an active call.
    
    This sets the call status to 'terminated' and records the end time.
    Note: This is a database-level change. Actual VoIP disconnection
    depends on telephony provider integration.
    """
    try:
        # Check if call exists and is active
        call_response = db_client.table("calls").select("id, status").eq("id", call_id).single().execute()
        
        if not call_response.data:
            raise HTTPException(status_code=404, detail="Call not found")
        
        current_status = call_response.data.get("status", "")
        active_statuses = ['in_progress', 'ringing', 'queued', 'initiated']
        
        if current_status not in active_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Call is not active. Current status: {current_status}"
            )
        
        # Update call status
        now = datetime.utcnow().isoformat() + "Z"
        update_response = db_client.table("calls").update({
            "status": "terminated",
            "ended_at": now,
            "outcome": "terminated_by_admin",
            "updated_at": now
        }).eq("id", call_id).execute()
        
        if not update_response.data:
            raise HTTPException(status_code=500, detail="Failed to terminate call")
        
        return {
            "detail": "Call terminated successfully",
            "call_id": call_id,
            "previous_status": current_status,
            "new_status": "terminated"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to terminate call: {str(e)}"
        )
