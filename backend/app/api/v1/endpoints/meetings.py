"""
Meetings API Endpoints
FastAPI endpoints for meeting booking and management.

Day 25: Meeting Booking Feature
"""
import logging
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser
from app.services.meeting_service import (
    get_meeting_service,
    CalendarNotConnectedError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["Meetings"])


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateMeetingRequest(BaseModel):
    """Request to create a meeting"""
    title: str = Field(..., description="Meeting title")
    start_time: str = Field(..., description="Start time in ISO format")
    duration_minutes: int = Field(30, ge=15, le=480, description="Duration in minutes")
    attendees: List[str] = Field(default_factory=list, description="Attendee emails")
    lead_id: Optional[str] = Field(None, description="Lead ID if booking with a lead")
    description: Optional[str] = Field(None, description="Meeting description")
    add_video_conference: bool = Field(True, description="Add Google Meet/Teams link")
    timezone: str = Field("UTC", description="Meeting timezone")


class UpdateMeetingRequest(BaseModel):
    """Request to update a meeting"""
    title: Optional[str] = None
    start_time: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None


class CancelMeetingRequest(BaseModel):
    """Request to cancel a meeting"""
    reason: Optional[str] = None


class AvailabilitySlot(BaseModel):
    """Available time slot"""
    start: str
    end: str
    duration_minutes: int


class MeetingResponse(BaseModel):
    """Meeting response"""
    id: str
    title: str
    description: Optional[str]
    start_time: str
    end_time: str
    timezone: str
    join_link: Optional[str]
    status: str
    attendees: List[dict]
    lead_id: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class CreateMeetingResponse(BaseModel):
    """Response after creating a meeting"""
    success: bool
    meeting_id: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    join_link: Optional[str] = None
    calendar_link: Optional[str] = None
    provider: Optional[str] = None
    error: Optional[str] = None
    calendar_required: bool = False


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/availability", response_model=List[AvailabilitySlot])
async def get_availability(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    duration_minutes: int = Query(30, ge=15, le=240, description="Slot duration"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> List[AvailabilitySlot]:
    """
    Get available meeting slots for a specific date.
    
    Requires a connected calendar (Google Calendar or Microsoft Outlook).
    Returns time slots between 9 AM and 6 PM that are free for the specified duration.
    """
    try:
        # Parse date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start_time = datetime.combine(target_date, datetime.min.time().replace(hour=9))
        end_time = datetime.combine(target_date, datetime.min.time().replace(hour=18))
        
        service = get_meeting_service(supabase)
        
        slots = await service.get_availability(
            tenant_id=current_user.tenant_id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes
        )
        
        return [
            AvailabilitySlot(
                start=slot["start"],
                end=slot["end"],
                duration_minutes=duration_minutes
            )
            for slot in slots
        ]
        
    except CalendarNotConnectedError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": e.message,
                "calendar_required": True,
                "connect_url": "/integrations"
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/", response_model=CreateMeetingResponse)
async def create_meeting(
    request: CreateMeetingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> CreateMeetingResponse:
    """
    Book a new meeting.
    
    Creates a calendar event in the connected calendar (Google Calendar or Outlook)
    and saves the meeting record to the database.
    
    Returns the meeting details including video conference join link if enabled.
    """
    try:
        # Parse start time
        start_time = datetime.fromisoformat(request.start_time.replace("Z", "+00:00"))
        
        service = get_meeting_service(supabase)
        
        result = await service.create_meeting(
            tenant_id=current_user.tenant_id,
            title=request.title,
            start_time=start_time,
            duration_minutes=request.duration_minutes,
            attendees=request.attendees,
            lead_id=request.lead_id,
            description=request.description,
            add_video_conference=request.add_video_conference,
            timezone=request.timezone,
            triggered_by="api"
        )
        
        return CreateMeetingResponse(**result)
        
    except CalendarNotConnectedError as e:
        return CreateMeetingResponse(
            success=False,
            error=e.message,
            calendar_required=True
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=List[MeetingResponse])
async def list_meetings(
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, description="From date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="To date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> List[MeetingResponse]:
    """
    List meetings for the tenant.
    
    Supports filtering by status and date range.
    """
    service = get_meeting_service(supabase)
    
    # Parse dates if provided
    from_dt = datetime.strptime(from_date, "%Y-%m-%d") if from_date else None
    to_dt = datetime.strptime(to_date, "%Y-%m-%d") if to_date else None
    
    meetings = await service.list_meetings(
        tenant_id=current_user.tenant_id,
        status=status,
        from_date=from_dt,
        to_date=to_dt,
        limit=limit
    )
    
    return [
        MeetingResponse(
            id=m["id"],
            title=m["title"],
            description=m.get("description"),
            start_time=m["start_time"],
            end_time=m["end_time"],
            timezone=m.get("timezone", "UTC"),
            join_link=m.get("join_link"),
            status=m["status"],
            attendees=m.get("attendees", []),
            lead_id=m.get("lead_id"),
            created_at=m["created_at"]
        )
        for m in meetings
    ]


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> MeetingResponse:
    """
    Get meeting details by ID.
    """
    service = get_meeting_service(supabase)
    
    meeting = await service.get_meeting(
        tenant_id=current_user.tenant_id,
        meeting_id=meeting_id
    )
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    return MeetingResponse(
        id=meeting["id"],
        title=meeting["title"],
        description=meeting.get("description"),
        start_time=meeting["start_time"],
        end_time=meeting["end_time"],
        timezone=meeting.get("timezone", "UTC"),
        join_link=meeting.get("join_link"),
        status=meeting["status"],
        attendees=meeting.get("attendees", []),
        lead_id=meeting.get("lead_id"),
        created_at=meeting["created_at"]
    )


@router.put("/{meeting_id}")
async def update_meeting(
    meeting_id: str,
    request: UpdateMeetingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Update/reschedule a meeting.
    
    Updates both the calendar event and database record.
    """
    try:
        service = get_meeting_service(supabase)
        
        new_start_time = None
        if request.start_time:
            new_start_time = datetime.fromisoformat(request.start_time.replace("Z", "+00:00"))
        
        new_attendees = None
        if request.attendees:
            new_attendees = request.attendees
        
        result = await service.update_meeting(
            tenant_id=current_user.tenant_id,
            meeting_id=meeting_id,
            new_start_time=new_start_time,
            new_title=request.title,
            new_description=request.description,
            new_attendees=new_attendees
        )
        
        return result
        
    except CalendarNotConnectedError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": e.message, "calendar_required": True}
        )


@router.delete("/{meeting_id}")
async def cancel_meeting(
    meeting_id: str,
    request: Optional[CancelMeetingRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
):
    """
    Cancel a meeting.
    
    Deletes the calendar event and updates the database status to 'cancelled'.
    """
    service = get_meeting_service(supabase)
    
    reason = request.reason if request else None
    
    result = await service.cancel_meeting(
        tenant_id=current_user.tenant_id,
        meeting_id=meeting_id,
        reason=reason
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to cancel meeting"))
    
    return result
