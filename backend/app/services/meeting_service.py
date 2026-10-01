"""
Meeting Service
Orchestrates calendar connectors with database persistence for meeting booking.

Day 25: Meeting Booking Feature
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone as dt_timezone
import asyncio
from app.core.postgres_adapter import Client

from app.infrastructure.connectors.base import ConnectorFactory
from app.infrastructure.connectors.encryption import get_encryption_service
from app.domain.models.meeting import Meeting, MeetingStatus, Attendee

logger = logging.getLogger(__name__)


class CalendarNotConnectedError(Exception):
    """Raised when user attempts to book without a connected calendar."""
    def __init__(self, message: str = "No calendar connected. Please connect Google Calendar or Microsoft Outlook first."):
        self.message = message
        super().__init__(self.message)


class MeetingService:
    """
    Meeting creation service that bridges calendar connectors with database.
    
    Responsibilities:
    - Fetch availability from connected calendar
    - Create calendar events via connector
    - Persist meeting records to database
    - Generate meeting join links (Google Meet / Microsoft Teams)
    - Send calendar invites
    - Handle update/cancel operations
    
    Integration Points:
    - Triggerable from: Voice agent outcome, Assistant agent, Dashboard API
    """
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
        self.supabase = db_client
        self._encryption = get_encryption_service()
    
    async def _get_active_calendar_connector(
        self,
        tenant_id: str,
        connector_id: Optional[str] = None,
    ) -> tuple[Any, str, str]:
        """
        Get active calendar connector for tenant.
        
        Returns:
            Tuple of (connector_instance, connector_id, provider)
            
        Raises:
            CalendarNotConnectedError: If no active calendar connector
        """
        from app.services.connector_resolver import (
            ConnectorNotConnectedError, resolve_active_connector,
        )
        try:
            return await resolve_active_connector(
                self.db_client, tenant_id, "calendar",
                **({"connector_id": connector_id} if connector_id else {}),
            )
        except ConnectorNotConnectedError as exc:
            raise CalendarNotConnectedError(exc.message) from exc

    async def get_availability(
        self,
        tenant_id: str,
        start_time: datetime,
        end_time: datetime,
        duration_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get available time slots from connected calendar.
        
        Args:
            tenant_id: Tenant ID
            start_time: Start of availability window
            end_time: End of availability window
            duration_minutes: Required slot duration in minutes
            
        Returns:
            List of available slots: [{"start": datetime, "end": datetime}, ...]
            
        Raises:
            CalendarNotConnectedError: If no calendar is connected
        """
        connector, _, provider = await self._get_active_calendar_connector(tenant_id)
        
        logger.info(f"Getting availability for tenant {tenant_id[:8]}... via {provider}")
        
        # Get available slots from calendar
        available_slots = await connector.get_availability(
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes
        )
        
        # Format response
        return [
            {
                "start": slot["start"].isoformat() if hasattr(slot["start"], "isoformat") else slot["start"],
                "end": slot["end"].isoformat() if hasattr(slot["end"], "isoformat") else slot["end"],
                "duration_minutes": duration_minutes
            }
            for slot in available_slots
        ]
    
    @staticmethod
    def _required_row(response, operation):
        if getattr(response, "error", None) or not getattr(response, "data", None):
            raise RuntimeError(f"{operation} was not persisted")
        data = response.data
        row = data[0] if isinstance(data, list) else data
        if not isinstance(row, dict) or not row.get("id"):
            raise RuntimeError(f"{operation} has no receipt ID")
        return row

    @staticmethod
    def _validate_start(start_time):
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise ValueError("The meeting time needs an explicit UTC offset")
        if start_time <= datetime.now(dt_timezone.utc):
            raise ValueError("The meeting time must be in the future")

    def _start_action(self, tenant_id, action, connector_id, parameters, *,
                      triggered_by="assistant", lead_id=None, call_id=None):
        row = self._required_row(self.db_client.table("assistant_actions").insert({
            "tenant_id": tenant_id, "type": action, "status": "running",
            "connector_id": connector_id, "input_data": parameters,
            "triggered_by": triggered_by, "lead_id": lead_id, "call_id": call_id,
            "started_at": datetime.now(dt_timezone.utc).isoformat(),
        }).execute(), "Calendar action")
        return row["id"]

    def _finish_action(self, tenant_id, action_id, status, result):
        self._required_row(self.db_client.table("assistant_actions").update({
            "status": status, "output_data": result,
            "completed_at": datetime.now(dt_timezone.utc).isoformat(),
        }).eq("id", action_id).eq("tenant_id", tenant_id).execute(), "Calendar receipt")

    def _unconfirmed(self, tenant_id, action_id, receipt, exc):
        code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        rejected = code is not None and 400 <= code < 500 and code != 408
        state = "failed" if rejected else "unknown"
        result = {"success": False, "status": state, "confirmation_allowed": False,
                  "action_id": action_id, **receipt,
                  "error": "Calendar action was rejected." if rejected else
                           "Calendar outcome is unconfirmed; review the saved receipt before retrying."}
        try:
            self._finish_action(tenant_id, action_id, state, result)
        except Exception:
            logger.error("Calendar receipt could not be updated action=%s", action_id)
        return result

    async def create_meeting(
        self, tenant_id: str, title: str, start_time: datetime, duration_minutes: int,
        attendees: List[str], lead_id: Optional[str] = None, call_id: Optional[str] = None,
        description: Optional[str] = None, add_video_conference: bool = True,
        timezone: str = "UTC", triggered_by: str = "api",
    ) -> Dict[str, Any]:
        """Create the provider event only after a durable intent, then save its receipt."""
        self._validate_start(start_time)
        if not title.strip() or not 1 <= duration_minutes <= 480:
            raise ValueError("Provide a title and a duration between 1 and 480 minutes")
        for table, reference in (("leads", lead_id), ("calls", call_id)):
            if reference:
                found = self.db_client.table(table).select("id").eq("tenant_id", tenant_id).eq("id", reference).limit(1).execute()
                self._required_row(found, f"Linked {table} record")
        connector, connector_id, provider = await self._get_active_calendar_connector(tenant_id)
        end_time = start_time + timedelta(minutes=duration_minutes)
        action_id = self._start_action(tenant_id, "book_meeting", connector_id,
            {"title": title, "start_time": start_time.isoformat(), "duration_minutes": duration_minutes,
             "attendees": attendees}, triggered_by=triggered_by, lead_id=lead_id, call_id=call_id)
        receipt = {"connector_id": connector_id, "provider": provider}
        try:
            event = await connector.create_event(title=title, start_time=start_time, end_time=end_time,
                description=description, attendees=attendees, add_video_conference=add_video_conference,
                timezone=timezone)
            if not getattr(event, "id", None):
                raise RuntimeError("Calendar provider returned no event receipt")
            receipt["external_event_id"] = event.id
            metadata = getattr(event, "metadata", None) or {}
            join_link = getattr(event, "video_link", None)
            row = self._required_row(self.db_client.table("meetings").insert({
                "tenant_id": tenant_id, "lead_id": lead_id, "call_id": call_id,
                "connector_id": connector_id, "action_id": action_id, "external_event_id": event.id,
                "title": title, "description": description, "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(), "timezone": timezone, "join_link": join_link,
                "status": "scheduled", "attendees": [{"email": email, "status": "pending"} for email in attendees],
                "metadata": {"provider": provider, "calendar_link": metadata.get("htmlLink"),
                             "triggered_by": triggered_by},
            }).execute(), "Meeting")
            result = {"success": True, "status": "created", "confirmation_allowed": True,
                      **receipt, "meeting_id": row["id"], "action_id": action_id, "title": title,
                      "start_time": start_time.isoformat(), "end_time": end_time.isoformat(),
                      "duration_minutes": duration_minutes, "join_link": join_link,
                      "calendar_link": metadata.get("htmlLink"), "attendees": attendees,
                      "reminders_created": 0}
            # Messaging requires its own preview and permission. Booking itself
            # never silently queues three SMS messages.
            self._finish_action(tenant_id, action_id, "completed", result)
            return result
        except asyncio.CancelledError as exc:
            self._unconfirmed(tenant_id, action_id, receipt, exc)
            raise
        except Exception as exc:
            return self._unconfirmed(tenant_id, action_id, receipt, exc)

    async def update_meeting(
        self, tenant_id: str, meeting_id: str, new_start_time: Optional[datetime] = None,
        new_title: Optional[str] = None, new_description: Optional[str] = None,
        new_attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        meeting = await self.get_meeting(tenant_id, meeting_id)
        if not meeting:
            return {"success": False, "status": "failed", "error": "Meeting not found"}
        external_id, connector_id = meeting.get("external_event_id"), meeting.get("connector_id")
        if not external_id or not connector_id:
            return {"success": False, "status": "failed", "error": "Meeting has no linked calendar event"}
        updates = {}
        new_end = None
        if new_start_time is not None:
            self._validate_start(new_start_time)
            original_start = datetime.fromisoformat(str(meeting["start_time"]).replace("Z", "+00:00"))
            original_end = datetime.fromisoformat(str(meeting["end_time"]).replace("Z", "+00:00"))
            new_end = new_start_time + (original_end - original_start)
            updates.update(start_time=new_start_time.isoformat(), end_time=new_end.isoformat())
        if new_title is not None:
            if not new_title.strip():
                raise ValueError("The meeting title cannot be empty")
            updates["title"] = new_title
        if new_description is not None:
            updates["description"] = new_description
        if new_attendees is not None:
            updates["attendees"] = [{"email": email, "status": "pending"} for email in new_attendees]
        if not updates:
            return {"success": False, "status": "failed", "error": "No meeting changes provided"}
        connector, _, provider = await self._get_active_calendar_connector(tenant_id, connector_id=connector_id)
        action_id = self._start_action(tenant_id, "update_meeting", connector_id,
            {"meeting_id": meeting_id, **updates})
        receipt = {"meeting_id": meeting_id, "external_event_id": external_id,
                   "connector_id": connector_id, "provider": provider}
        try:
            event = await connector.update_event(event_id=external_id, title=new_title,
                start_time=new_start_time, end_time=new_end, description=new_description,
                attendees=new_attendees)
            if getattr(event, "id", None) != external_id:
                raise RuntimeError("Calendar provider did not confirm the event update")
            self._required_row(self.db_client.table("meetings").update(updates).eq("id", meeting_id).eq(
                "tenant_id", tenant_id).execute(), "Meeting update")
            result = {"success": True, "status": "updated", "confirmation_allowed": True,
                      "action_id": action_id, **receipt, "message": "Meeting updated."}
            self._finish_action(tenant_id, action_id, "completed", result)
            return result
        except asyncio.CancelledError as exc:
            self._unconfirmed(tenant_id, action_id, receipt, exc)
            raise
        except Exception as exc:
            return self._unconfirmed(tenant_id, action_id, receipt, exc)

    async def cancel_meeting(self, tenant_id: str, meeting_id: str,
                             reason: Optional[str] = None) -> Dict[str, Any]:
        meeting = await self.get_meeting(tenant_id, meeting_id)
        if not meeting:
            return {"success": False, "status": "failed", "error": "Meeting not found"}
        external_id, connector_id = meeting.get("external_event_id"), meeting.get("connector_id")
        if not external_id or not connector_id:
            return {"success": False, "status": "failed", "error": "Meeting has no linked calendar event"}
        connector, _, provider = await self._get_active_calendar_connector(tenant_id, connector_id=connector_id)
        action_id = self._start_action(tenant_id, "cancel_meeting", connector_id,
            {"meeting_id": meeting_id, "reason": reason})
        receipt = {"meeting_id": meeting_id, "external_event_id": external_id,
                   "connector_id": connector_id, "provider": provider}
        try:
            if await connector.delete_event(external_id) is not True:
                raise RuntimeError("Calendar provider did not confirm cancellation")
            self._required_row(self.db_client.table("meetings").update({
                "status": "cancelled", "metadata": {**(meeting.get("metadata") or {}),
                    "cancelled_at": datetime.now(dt_timezone.utc).isoformat(), "cancellation_reason": reason},
            }).eq("id", meeting_id).eq("tenant_id", tenant_id).execute(), "Meeting cancellation")
            # Existing unsent reminders belong to the cancelled event too.
            reminders = self.db_client.table("reminders").update({"status": "cancelled"}).eq(
                "tenant_id", tenant_id).eq("meeting_id", meeting_id).eq("status", "pending").execute()
            if getattr(reminders, "error", None):
                raise RuntimeError("Meeting reminder cancellation was not persisted")
            result = {"success": True, "status": "cancelled", "confirmation_allowed": True,
                      "action_id": action_id, **receipt, "message": "Meeting cancelled."}
            self._finish_action(tenant_id, action_id, "completed", result)
            return result
        except asyncio.CancelledError as exc:
            self._unconfirmed(tenant_id, action_id, receipt, exc)
            raise
        except Exception as exc:
            return self._unconfirmed(tenant_id, action_id, receipt, exc)

    async def get_meeting(
        self,
        tenant_id: str,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get meeting by ID."""
        response = self.db_client.table("meetings").select(
            "*"
        ).eq("id", meeting_id).eq("tenant_id", tenant_id).single().execute()
        
        return response.data
    
    async def list_meetings(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List meetings for tenant with optional filters."""
        query = self.db_client.table("meetings").select(
            "*"
        ).eq("tenant_id", tenant_id)
        
        if status:
            query = query.eq("status", status)
        if from_date:
            query = query.gte("start_time", from_date.isoformat())
        if to_date:
            query = query.lte("start_time", to_date.isoformat())
        
        response = query.order("start_time", desc=False).limit(limit).execute()
        
        return response.data or []
    
# Singleton instance helper
_meeting_service: Optional[MeetingService] = None

def get_meeting_service(db_client: Client) -> MeetingService:
    """Get or create MeetingService instance."""
    global _meeting_service
    if _meeting_service is None or _meeting_service.db_client is not db_client:
        _meeting_service = MeetingService(db_client)
    return _meeting_service
