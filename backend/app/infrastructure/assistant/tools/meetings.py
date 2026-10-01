"""
Meeting management tools for the assistant agent.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class CheckAvailabilityInput(BaseModel):
    """Input for check_availability tool"""
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    duration_minutes: int = Field(30, description="Meeting duration in minutes")
    timezone_name: str = Field(..., description="Caller-provided IANA timezone, e.g. Asia/Karachi")


class BookMeetingInput(BaseModel):
    """Input for book_meeting tool"""
    title: str = Field(..., description="Meeting title")
    start_time: str = Field(..., description="Start time in ISO format with explicit UTC offset")
    duration_minutes: int = Field(30, description="Duration in minutes")
    attendees: List[str] = Field(default_factory=list, description="Attendee email addresses")
    lead_id: Optional[str] = Field(None, description="Lead ID if meeting is with a lead")
    description: Optional[str] = Field(None, description="Meeting description")
    add_video_conference: bool = Field(True, description="Add Google Meet or Teams link")
    confirm: bool = Field(False, description="Leave false for preview; the user's Apply button confirms.")


class UpdateMeetingInput(BaseModel):
    """Input for update_meeting tool"""
    meeting_id: str = Field(..., description="Meeting ID to update")
    new_time: Optional[str] = Field(None, description="New start time in ISO format")
    new_title: Optional[str] = Field(None, description="New meeting title")
    confirm: bool = Field(False, description="Preview first; Apply confirms.")


class CancelMeetingInput(BaseModel):
    """Input for cancel_meeting tool"""
    meeting_id: str = Field(..., description="Meeting ID to cancel")
    reason: Optional[str] = Field(None, description="Cancellation reason")
    confirm: bool = Field(False, description="Preview first; Apply confirms.")


def _future_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Specify the date, time and UTC offset; do not assume a timezone.")
    if result <= datetime.now(timezone.utc):
        raise ValueError("The meeting time must be in the future.")
    return result


def _meeting(tenant_id, db_client, meeting_id):
    response = db_client.table("meetings").select("id,title,start_time,status").eq(
        "tenant_id", tenant_id
    ).eq("id", meeting_id).single().execute()
    if not response.data:
        raise ValueError("Meeting not found in this account.")
    return response.data


async def check_availability(
    tenant_id: str,
    db_client: Client,
    date_str: Optional[str] = None,
    duration_minutes: int = 30,
    timezone_name: Optional[str] = None,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check available meeting slots for a given date.

    Requires connected Google Calendar or Microsoft Outlook.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError

        date_str = date or date_str
        if not timezone_name:
            raise ValueError("Provide the timezone for availability; do not assume UTC.")
        zone = ZoneInfo(timezone_name)
        if not 1 <= duration_minutes <= 480:
            raise ValueError("Duration must be between 1 and 480 minutes.")
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_time = datetime.combine(target_date, datetime.min.time().replace(hour=9), tzinfo=zone)
        end_time = datetime.combine(target_date, datetime.min.time().replace(hour=18), tzinfo=zone)

        service = get_meeting_service(db_client)

        slots = await service.get_availability(
            tenant_id=tenant_id,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes
        )

        return {
            "success": True,
            "date": date_str,
            "duration_minutes": duration_minutes,
            "available_slots": slots,
            "slot_count": len(slots)
        }
    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error checking availability: {e}")
        return {"success": False, "error": str(e)}


async def book_meeting(
    tenant_id: str,
    db_client: Client,
    title: str,
    start_time: str,
    duration_minutes: int = 30,
    attendees: Optional[List[str]] = None,
    lead_id: Optional[str] = None,
    description: Optional[str] = None,
    add_video_conference: bool = True,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Book a meeting via connected calendar.

    Creates calendar event and saves meeting record to database.
    Returns join link for video conference if enabled.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError

        # Parse start time
        start_dt = _future_time(start_time)
        if not title.strip() or not 1 <= duration_minutes <= 480:
            raise ValueError("Provide a title and a duration between 1 and 480 minutes.")
        if not confirm:
            return {"preview": True, "changes": [
                {"field": "Title", "before": None, "after": title},
                {"field": "Start", "before": None, "after": start_dt.isoformat()},
                {"field": "Duration", "before": None, "after": f"{duration_minutes} minutes"},
                {"field": "Attendees", "before": None, "after": ", ".join(attendees or [])},
            ], "note": "No calendar event has been created."}

        service = get_meeting_service(db_client)

        result = await service.create_meeting(
            tenant_id=tenant_id,
            title=title,
            start_time=start_dt,
            duration_minutes=duration_minutes,
            attendees=attendees or [],
            lead_id=lead_id,
            description=description,
            add_video_conference=add_video_conference,
            triggered_by="assistant"
        )

        return result

    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error booking meeting: {e}")
        return {"success": False, "error": str(e)}


async def update_meeting_tool(
    tenant_id: str,
    db_client: Client,
    meeting_id: str,
    new_time: Optional[str] = None,
    new_title: Optional[str] = None,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Update/reschedule an existing meeting.
    """
    try:
        from app.services.meeting_service import get_meeting_service, CalendarNotConnectedError

        service = get_meeting_service(db_client)

        new_start_time = None
        if new_time:
            new_start_time = _future_time(new_time)
        if not new_time and not new_title:
            raise ValueError("Provide a new time or title.")
        current = _meeting(tenant_id, db_client, meeting_id)
        if not confirm:
            changes = []
            if new_time:
                changes.append({"field": "Start", "before": current.get("start_time"), "after": new_start_time.isoformat()})
            if new_title:
                changes.append({"field": "Title", "before": current.get("title"), "after": new_title})
            return {"preview": True, "changes": changes, "note": "Meeting not changed yet."}

        result = await service.update_meeting(
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            new_start_time=new_start_time,
            new_title=new_title
        )

        return result

    except CalendarNotConnectedError as e:
        return {"success": False, "error": e.message, "calendar_required": True}
    except Exception as e:
        logger.error(f"Error updating meeting: {e}")
        return {"success": False, "error": str(e)}


async def cancel_meeting_tool(
    tenant_id: str,
    db_client: Client,
    meeting_id: str,
    reason: Optional[str] = None,
    conversation_id: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Cancel a scheduled meeting.
    """
    try:
        from app.services.meeting_service import get_meeting_service

        current = _meeting(tenant_id, db_client, meeting_id)
        if not confirm:
            return {"preview": True, "changes": [{"field": "Meeting", "before": current.get("title"), "after": "Cancelled"}], "note": "Meeting not cancelled yet."}

        service = get_meeting_service(db_client)

        result = await service.cancel_meeting(
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            reason=reason
        )

        return result

    except Exception as e:
        logger.error(f"Error cancelling meeting: {e}")
        return {"success": False, "error": str(e)}
