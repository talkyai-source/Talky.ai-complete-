"""
Call History Endpoints
Provides paginated call list and individual call details
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter, verify_tenant_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


class CallListItem(BaseModel):
    """Call list item (summary)"""
    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None


class CallDetail(BaseModel):
    """Full call details"""
    id: str
    talklee_call_id: Optional[str] = None
    timestamp: str
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    transcript: Optional[str] = None
    recording_id: Optional[str] = None
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    summary: Optional[str] = None


class CallListResponse(BaseModel):
    """Paginated call list response"""
    items: List[CallListItem]
    page: int
    page_size: int
    total: int


@router.get("/", response_model=CallListResponse)
async def list_calls(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get paginated list of calls.
    
    Used by: /dashboard/history page.
    
    Query params:
        - page: Page number (1-indexed)
        - page_size: Items per page (max 100)
        - status: Filter by call status
        - from: Start date filter
        - to: End date filter
    """
    try:
        # Build query with tenant filtering
        query = db_client.table("calls").select(
            "id, created_at, phone_number, status, duration_seconds, outcome",
            count="exact"
        )
        query = apply_tenant_filter(query, current_user.tenant_id)
        
        # Apply additional filters
        if status:
            query = query.eq("status", status)
        
        if from_date:
            query = query.gte("created_at", from_date)
        
        if to_date:
            query = query.lte("created_at", to_date + "T23:59:59Z")
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Execute with pagination
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        
        # Get total count
        total = response.count if response.count else 0
        
        # Map results
        items = []
        for call in response.data or []:
            created_at = call.get("created_at", "")
            items.append(CallListItem(
                id=str(call["id"]),
                talklee_call_id=call.get("talklee_call_id"),
                timestamp=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                to_number=call.get("phone_number", ""),
                status=call.get("status", "unknown"),
                duration_seconds=call.get("duration_seconds"),
                outcome=call.get("outcome")
            ))
        
        return CallListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total
        )
    
    except Exception as e:
        logger.error(f"Failed to fetch calls: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch calls"
        )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get individual call details.
    
    Used by: Call detail modal/page.
    
    Returns full call information including transcript and recording reference.
    """
    try:
        # Get call details with tenant filtering
        query = db_client.table("calls").select("*").eq("id", call_id)
        query = apply_tenant_filter(query, current_user.tenant_id)
        call_response = query.single().execute()
        
        if not call_response.data:
            raise HTTPException(
                status_code=404,
                detail="Call not found"
            )
        
        call = call_response.data
        
        # Get recording if exists
        recording_id = None
        recording_response = db_client.table("recordings").select("id").eq("call_id", call_id).execute()
        
        if recording_response.data and len(recording_response.data) > 0:
            recording_id = recording_response.data[0]["id"]
        
        created_at = call.get("created_at", "")
        return CallDetail(
            id=str(call["id"]),
            talklee_call_id=call.get("talklee_call_id"),
            timestamp=created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            to_number=call.get("phone_number", ""),
            status=call.get("status", "unknown"),
            duration_seconds=call.get("duration_seconds"),
            outcome=call.get("outcome"),
            transcript=call.get("transcript"),
            recording_id=str(recording_id) if recording_id is not None else None,
            campaign_id=str(call["campaign_id"]) if call.get("campaign_id") else None,
            lead_id=str(call["lead_id"]) if call.get("lead_id") else None,
            summary=call.get("summary")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch call {call_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch call"
        )


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    format: str = Query("json", description="Format: 'json' or 'text'"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call transcript in requested format (Day 10).
    
    Used by: Transcript viewer in call details.
    
    Query params:
        - format: 'json' for structured turns, 'text' for plain text
    
    Returns:
        JSON format: {"turns": [...], "metadata": {...}}
        Text format: Plain text transcript
    """
    try:
        # Verify call belongs to tenant before fetching transcript
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        # First try the transcripts table (Day 10)
        transcript_response = db_client.table("transcripts").select(
            "turns, full_text, word_count, turn_count, created_at"
        ).eq("call_id", call_id).execute()
        
        if transcript_response.data and len(transcript_response.data) > 0:
            transcript_data = transcript_response.data[0]
            
            if format == "text":
                return {
                    "format": "text",
                    "transcript": transcript_data.get("full_text", ""),
                    "call_id": call_id
                }
            else:
                return {
                    "format": "json",
                    "turns": transcript_data.get("turns", []),
                    "metadata": {
                        "word_count": transcript_data.get("word_count", 0),
                        "turn_count": transcript_data.get("turn_count", 0),
                        "created_at": transcript_data.get("created_at")
                    },
                    "call_id": call_id
                }
        
        # Fallback to calls table transcript fields
        call_response = db_client.table("calls").select(
            "transcript, transcript_json"
        ).eq("id", call_id).single().execute()
        
        if not call_response.data:
            raise HTTPException(
                status_code=404,
                detail="Call not found"
            )
        
        call_data = call_response.data
        
        if format == "text":
            return {
                "format": "text",
                "transcript": call_data.get("transcript", ""),
                "call_id": call_id
            }
        else:
            return {
                "format": "json",
                "turns": call_data.get("transcript_json", []),
                "metadata": {},
                "call_id": call_id
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch transcript for call {call_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch transcript"
        )


# =============================================================================
# Day 1: Call Events & Legs Endpoints
# =============================================================================

@router.get("/{call_id}/events")
async def get_call_events(
    call_id: str,
    limit: int = Query(100, ge=1, le=500),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call events (timeline) for a specific call.
    
    Returns chronological list of events: state changes, leg starts,
    transcripts, LLM calls, TTS, webhooks, etc.
    
    Query params:
        - limit: Max events to return (default 100, max 500)
        - event_type: Filter by type (state_change, transcript, etc.)
    """
    try:
        # Verify call belongs to tenant
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        # Build query
        query = db_client.table("call_events").select("*").eq("call_id", call_id)
        
        if event_type:
            query = query.eq("event_type", event_type)
        
        response = query.order("created_at", desc=False).limit(limit).execute()
        
        return {
            "call_id": call_id,
            "events": response.data or [],
            "count": len(response.data) if response.data else 0
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch events for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call events")


@router.get("/{call_id}/legs")
async def get_call_legs(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Get call legs for a specific call.
    
    Returns all legs (PSTN, WebSocket, SIP, etc.) with their status
    and timing information.
    """
    try:
        # Verify call belongs to tenant
        if not verify_tenant_access(db_client, "calls", call_id, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Call not found")
        
        response = db_client.table("call_legs").select("*").eq("call_id", call_id).order("created_at", desc=False).execute()
        
        return {
            "call_id": call_id,
            "legs": response.data or [],
            "count": len(response.data) if response.data else 0
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch legs for call {call_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch call legs")

