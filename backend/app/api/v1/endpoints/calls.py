"""
Call History Endpoints
Provides paginated call list and individual call details
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

router = APIRouter(prefix="/calls", tags=["calls"])


class CallListItem(BaseModel):
    """Call list item (summary)"""
    id: str
    timestamp: str
    to_number: str
    status: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None


class CallDetail(BaseModel):
    """Full call details"""
    id: str
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
    supabase: Client = Depends(get_supabase)
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
        # Build query
        query = supabase.table("calls").select(
            "id, created_at, phone_number, status, duration_seconds, outcome",
            count="exact"
        )
        
        # Apply filters
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
            items.append(CallListItem(
                id=call["id"],
                timestamp=call.get("created_at", ""),
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
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch calls: {str(e)}"
        )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Get individual call details.
    
    Used by: Call detail modal/page.
    
    Returns full call information including transcript and recording reference.
    """
    try:
        # Get call details
        call_response = supabase.table("calls").select("*").eq("id", call_id).single().execute()
        
        if not call_response.data:
            raise HTTPException(
                status_code=404,
                detail="Call not found"
            )
        
        call = call_response.data
        
        # Get recording if exists
        recording_id = None
        recording_response = supabase.table("recordings").select("id").eq("call_id", call_id).execute()
        
        if recording_response.data and len(recording_response.data) > 0:
            recording_id = recording_response.data[0]["id"]
        
        return CallDetail(
            id=call["id"],
            timestamp=call.get("created_at", ""),
            to_number=call.get("phone_number", ""),
            status=call.get("status", "unknown"),
            duration_seconds=call.get("duration_seconds"),
            outcome=call.get("outcome"),
            transcript=call.get("transcript"),
            recording_id=recording_id,
            campaign_id=call.get("campaign_id"),
            lead_id=call.get("lead_id"),
            summary=call.get("summary")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch call: {str(e)}"
        )


@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    format: str = Query("json", description="Format: 'json' or 'text'"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
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
        # First try the transcripts table (Day 10)
        transcript_response = supabase.table("transcripts").select(
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
        call_response = supabase.table("calls").select(
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
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transcript: {str(e)}"
        )

