"""
Meetings API Endpoints
FastAPI endpoints for meeting booking and management.

Day 25: Meeting Booking Feature
"""
from app.core.error_handlers import safe_exception_message
from app.api.v1.validation import ResourceUUID, bounded_text
import logging
from typing import Optional, List
from datetime import datetime, time as dt_time, timezone as dt_timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.core.security.rbac import Permission, require_permission
from app.services.meeting_service import (
    get_meeting_service,
    CalendarNotConnectedError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["Meetings"])

# Writes change the tenant's real calendar and invite attendees, so they need
# the same CALENDAR_MANAGE grant the assistant tool path requires.
_require_calendar_manage = require_permission(Permission.CALENDAR_MANAGE)


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateMeetingRequest(BaseModel):
    """Request to create a meeting"""
    # QA F2-13: title fits VARCHAR(255), timezone VARCHAR(50).
    title: bounded_text(255, min_length=1) = Field(..., description="Meeting title")
    start_time: bounded_text(64) = Field(..., description="Start time in ISO format")
    duration_minutes: int = Field(30, ge=15, le=480, description="Duration in minutes")
    attendees: List[bounded_text(254)] = Field(default_factory=list, max_length=50, description="Attendee emails")
    lead_id: Optional[str] = Field(None, description="Lead ID if booking with a lead")
    description: Optional[bounded_text(10_000)] = Field(None, description="Meeting description")
    add_video_conference: bool = Field(True, description="Add Google Meet/Teams link")
    timezone: bounded_text(50) = Field("UTC", description="Meeting timezone")


class UpdateMeetingRequest(BaseModel):
    """Request to update a meeting"""
    title: Optional[bounded_text(255, min_length=1)] = None
    start_time: Optional[bounded_text(64)] = None
    description: Optional[bounded_text(10_000)] = None
    attendees: Optional[List[bounded_text(254)]] = Field(None, max_length=50)


class CancelMeetingRequest(BaseModel):
    """Request to cancel a meeting"""
    reason: Optional[bounded_text(1_000)] = None


class AvailabilitySlot(BaseModel):
    """Available time slot"""
    start: str
    end: str
    duration_minutes: int


class _NativeRowModel(BaseModel):
    """Response model built straight from database rows and service receipts.

    asyncpg returns uuid.UUID for uuid columns and datetime for timestamptz
    columns. The JSON contract carries both as strings and pydantic v2 does not
    coerce them, so every real row failed validation (QA F2-08). Same fix as
    admin/_serialization.AdminResponseModel; a no-op for values already text.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _native_to_text(cls, value):
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class MeetingResponse(_NativeRowModel):
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
    # Attendee emails as plain strings: the shape the web client reads (models.ts MeetingSchema).
    participants: List[str] = Field(default_factory=list)
    lead_id: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class MeetingListResponse(BaseModel):
    """Meetings in the ``{"items": [...]}`` envelope the web client parses (models.ts ListResponseSchema)."""
    items: List[MeetingResponse]


class CreateMeetingResponse(_NativeRowModel):
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


def _meeting_response(meeting: dict) -> MeetingResponse:
    """Build the API shape from a ``meetings`` row as the database driver returns it."""
    attendees = meeting.get("attendees") or []
    return MeetingResponse(
        id=meeting["id"],
        title=meeting["title"],
        description=meeting.get("description"),
        start_time=meeting["start_time"],
        end_time=meeting["end_time"],
        timezone=meeting.get("timezone") or "UTC",
        join_link=meeting.get("join_link"),
        status=meeting["status"],
        attendees=attendees,
        # MeetingService stores attendees as {"email", "status"} objects.
        participants=[a["email"] for a in attendees if isinstance(a, dict) and a.get("email")],
        lead_id=meeting.get("lead_id"),
        created_at=meeting["created_at"],
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/availability", response_model=List[AvailabilitySlot])
async def get_availability(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    duration_minutes: int = Query(30, ge=15, le=240, description="Slot duration"),
    tz: str = Query("UTC", alias="timezone", description="IANA timezone the 9 AM-6 PM window is in"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
) -> List[AvailabilitySlot]:
    """
    Get available meeting slots for a specific date.
    
    Requires a connected calendar (Google Calendar or Microsoft Outlook).
    Returns time slots between 9 AM and 6 PM (in `timezone`, default UTC) that are
    free for the specified duration; slots are returned in that timezone.
    """
    try:
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise HTTPException(status_code=400, detail=f"Unknown timezone: {tz}")

        # Parse date
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start_time = datetime.combine(target_date, dt_time(hour=9), tzinfo=zone).astimezone(dt_timezone.utc)
        end_time = datetime.combine(target_date, dt_time(hour=18), tzinfo=zone).astimezone(dt_timezone.utc)
        
        service = get_meeting_service(db_client)
        
        slots = await service.get_availability(
            tenant_id=current_user.tenant_id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes
        )
        
        def local(value: str) -> str:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone).isoformat()
            except (ValueError, AttributeError):
                return value

        return [
            AvailabilitySlot(
                start=local(slot["start"]),
                end=local(slot["end"]),
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
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'The request could not be completed.'))


@router.post("/", response_model=CreateMeetingResponse, dependencies=[Depends(_require_calendar_manage)])
async def create_meeting(
    request: CreateMeetingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
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
        
        service = get_meeting_service(db_client)
        
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
        raise HTTPException(status_code=400, detail=safe_exception_message(e, 'The request could not be completed.'))


@router.get("/", response_model=MeetingListResponse)
async def list_meetings(
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, description="From date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="To date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
) -> MeetingListResponse:
    """
    List meetings for the tenant.
    
    Supports filtering by status and date range.
    """
    service = get_meeting_service(db_client)
    
    # Parse dates if provided; a malformed one is the caller's error, not a 500.
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d") if from_date else None
        to_dt = datetime.strptime(to_date, "%Y-%m-%d") if to_date else None
    except ValueError:
        raise HTTPException(status_code=400, detail="from_date and to_date must be dates in YYYY-MM-DD form")
    
    meetings = await service.list_meetings(
        tenant_id=current_user.tenant_id,
        status=status,
        from_date=from_dt,
        to_date=to_dt,
        limit=limit
    )
    
    return MeetingListResponse(items=[_meeting_response(m) for m in meetings])


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: ResourceUUID,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
) -> MeetingResponse:
    """
    Get meeting details by ID.
    """
    service = get_meeting_service(db_client)
    
    meeting = await service.get_meeting(
        tenant_id=current_user.tenant_id,
        meeting_id=meeting_id
    )
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    return _meeting_response(meeting)


@router.put("/{meeting_id}", dependencies=[Depends(_require_calendar_manage)])
async def update_meeting(
    meeting_id: ResourceUUID,
    request: UpdateMeetingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Update/reschedule a meeting.
    
    Updates both the calendar event and database record.
    """
    try:
        service = get_meeting_service(db_client)
        
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


@router.delete("/{meeting_id}", dependencies=[Depends(_require_calendar_manage)])
async def cancel_meeting(
    meeting_id: ResourceUUID,
    request: Optional[CancelMeetingRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Cancel a meeting.
    
    Deletes the calendar event and updates the database status to 'cancelled'.
    """
    service = get_meeting_service(db_client)
    
    reason = request.reason if request else None
    
    result = await service.cancel_meeting(
        tenant_id=current_user.tenant_id,
        meeting_id=meeting_id,
        reason=reason
    )
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to cancel meeting"))
    
    return result
