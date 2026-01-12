"""
Meeting Service
Orchestrates calendar connectors with database persistence for meeting booking.

Day 25: Meeting Booking Feature
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from supabase import Client

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
    
    def __init__(self, supabase: Client):
        self.supabase = supabase
        self._encryption = get_encryption_service()
    
    async def _get_active_calendar_connector(
        self,
        tenant_id: str
    ) -> tuple[Any, str, str]:
        """
        Get active calendar connector for tenant.
        
        Returns:
            Tuple of (connector_instance, connector_id, provider)
            
        Raises:
            CalendarNotConnectedError: If no active calendar connector
        """
        # Find active calendar connector for tenant
        response = self.supabase.table("connectors").select(
            "id, provider, status"
        ).eq("tenant_id", tenant_id).eq(
            "type", "calendar"
        ).eq("status", "active").execute()
        
        if not response.data:
            raise CalendarNotConnectedError(
                "No calendar connected. Please connect Google Calendar or Microsoft Outlook "
                "from Settings > Integrations to book meetings."
            )
        
        connector_data = response.data[0]
        connector_id = connector_data["id"]
        provider = connector_data["provider"]
        
        # Get decrypted access token
        account_response = self.supabase.table("connector_accounts").select(
            "access_token_encrypted, token_expires_at"
        ).eq("connector_id", connector_id).eq("status", "active").single().execute()
        
        if not account_response.data:
            raise CalendarNotConnectedError(
                "Calendar connection expired. Please reconnect your calendar from Settings > Integrations."
            )
        
        # Decrypt token
        encrypted_token = account_response.data.get("access_token_encrypted")
        if not encrypted_token:
            raise CalendarNotConnectedError("Calendar credentials are missing. Please reconnect.")
        
        access_token = self._encryption.decrypt(encrypted_token)
        
        # Create connector instance
        connector = ConnectorFactory.create(
            provider=provider,
            tenant_id=tenant_id,
            connector_id=connector_id
        )
        await connector.set_access_token(access_token)
        
        return connector, connector_id, provider
    
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
    
    async def create_meeting(
        self,
        tenant_id: str,
        title: str,
        start_time: datetime,
        duration_minutes: int,
        attendees: List[str],
        lead_id: Optional[str] = None,
        call_id: Optional[str] = None,
        description: Optional[str] = None,
        add_video_conference: bool = True,
        timezone: str = "UTC",
        triggered_by: str = "api"  # api, voice_agent, assistant, dashboard
    ) -> Dict[str, Any]:
        """
        Create a meeting end-to-end.
        
        Flow:
        1. Get active calendar connector
        2. Create event in calendar provider (Google Calendar / Outlook)
        3. Save meeting record to database
        4. Create action audit record
        5. Return meeting with join_link
        
        Args:
            tenant_id: Tenant ID
            title: Meeting title
            start_time: Meeting start time
            duration_minutes: Meeting duration
            attendees: List of attendee email addresses
            lead_id: Optional lead ID if meeting is with a lead
            call_id: Optional call ID if triggered from call outcome
            description: Optional meeting description
            add_video_conference: Create video conference link (Google Meet/Teams)
            timezone: Meeting timezone
            triggered_by: Trigger source for audit
            
        Returns:
            Meeting dict with id, join_link, calendar_link, etc.
            
        Raises:
            CalendarNotConnectedError: If no calendar is connected
        """
        connector, connector_id, provider = await self._get_active_calendar_connector(tenant_id)
        
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        logger.info(
            f"Creating meeting '{title}' for tenant {tenant_id[:8]}... "
            f"via {provider} at {start_time}"
        )
        
        # Step 1: Create calendar event
        calendar_event = await connector.create_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            attendees=attendees,
            add_video_conference=add_video_conference,
            timezone=timezone
        )
        
        # Step 2: Save meeting record to database
        meeting_data = {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "call_id": call_id,
            "connector_id": connector_id,
            "external_event_id": calendar_event.id,
            "title": title,
            "description": description,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "timezone": timezone,
            "join_link": calendar_event.video_link,
            "status": "scheduled",
            "attendees": [{"email": email, "status": "pending"} for email in attendees],
            "metadata": {
                "provider": provider,
                "calendar_link": calendar_event.metadata.get("htmlLink") if calendar_event.metadata else None,
                "triggered_by": triggered_by
            }
        }
        
        meeting_response = self.supabase.table("meetings").insert(meeting_data).execute()
        meeting_record = meeting_response.data[0] if meeting_response.data else {}
        meeting_id = meeting_record.get("id")
        
        # Step 3: Create action audit record
        action_data = {
            "tenant_id": tenant_id,
            "type": "book_meeting",
            "status": "completed",
            "triggered_by": triggered_by,
            "lead_id": lead_id,
            "call_id": call_id,
            "connector_id": connector_id,
            "input_data": {
                "title": title,
                "start_time": start_time.isoformat(),
                "duration_minutes": duration_minutes,
                "attendees": attendees
            },
            "output_data": {
                "meeting_id": meeting_id,
                "external_event_id": calendar_event.id,
                "join_link": calendar_event.video_link
            },
            "completed_at": datetime.utcnow().isoformat()
        }
        
        action_response = self.supabase.table("assistant_actions").insert(action_data).execute()
        action_id = action_response.data[0]["id"] if action_response.data else None
        
        # Update meeting with action_id
        if meeting_id and action_id:
            self.supabase.table("meetings").update(
                {"action_id": action_id}
            ).eq("id", meeting_id).execute()
        
        # Day 27: Create meeting reminders (T-24h, T-1h, T-10m)
        await self._create_meeting_reminders(
            meeting_id=meeting_id,
            tenant_id=tenant_id,
            lead_id=lead_id,
            start_time=start_time,
            title=title,
            join_link=calendar_event.video_link
        )
        
        logger.info(f"Meeting created: {meeting_id} with join link: {calendar_event.video_link}")
        
        return {
            "success": True,
            "meeting_id": meeting_id,
            "external_event_id": calendar_event.id,
            "title": title,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_minutes": duration_minutes,
            "join_link": calendar_event.video_link,
            "calendar_link": calendar_event.metadata.get("htmlLink") if calendar_event.metadata else None,
            "attendees": attendees,
            "provider": provider,
            "reminders_created": 3
        }
    
    async def update_meeting(
        self,
        tenant_id: str,
        meeting_id: str,
        new_start_time: Optional[datetime] = None,
        new_title: Optional[str] = None,
        new_description: Optional[str] = None,
        new_attendees: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Update/reschedule an existing meeting.
        
        Updates both the calendar event and database record.
        """
        # Get meeting from database
        meeting_response = self.supabase.table("meetings").select(
            "*, connectors(provider)"
        ).eq("id", meeting_id).eq("tenant_id", tenant_id).single().execute()
        
        if not meeting_response.data:
            return {"success": False, "error": "Meeting not found"}
        
        meeting = meeting_response.data
        external_event_id = meeting.get("external_event_id")
        connector_id = meeting.get("connector_id")
        
        if not external_event_id or not connector_id:
            return {"success": False, "error": "Meeting has no linked calendar event"}
        
        # Get connector
        connector, _, provider = await self._get_active_calendar_connector(tenant_id)
        
        # Calculate new end time if start time is changing
        new_end_time = None
        if new_start_time:
            original_duration = (
                datetime.fromisoformat(meeting["end_time"].replace("Z", "+00:00")) -
                datetime.fromisoformat(meeting["start_time"].replace("Z", "+00:00"))
            )
            new_end_time = new_start_time + original_duration
        
        # Update calendar event
        updated_event = await connector.update_event(
            event_id=external_event_id,
            title=new_title,
            start_time=new_start_time,
            end_time=new_end_time,
            description=new_description,
            attendees=new_attendees
        )
        
        # Update database record
        update_data = {}
        if new_start_time:
            update_data["start_time"] = new_start_time.isoformat()
            update_data["end_time"] = new_end_time.isoformat()
        if new_title:
            update_data["title"] = new_title
        if new_description:
            update_data["description"] = new_description
        if new_attendees:
            update_data["attendees"] = [{"email": email, "status": "pending"} for email in new_attendees]
        
        if update_data:
            self.supabase.table("meetings").update(update_data).eq("id", meeting_id).execute()
        
        logger.info(f"Meeting updated: {meeting_id}")
        
        return {
            "success": True,
            "meeting_id": meeting_id,
            "message": "Meeting updated successfully"
        }
    
    async def cancel_meeting(
        self,
        tenant_id: str,
        meeting_id: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel a meeting.
        
        Deletes the calendar event and updates database status.
        """
        # Get meeting from database
        meeting_response = self.supabase.table("meetings").select(
            "*"
        ).eq("id", meeting_id).eq("tenant_id", tenant_id).single().execute()
        
        if not meeting_response.data:
            return {"success": False, "error": "Meeting not found"}
        
        meeting = meeting_response.data
        external_event_id = meeting.get("external_event_id")
        
        if external_event_id:
            try:
                # Get connector and delete calendar event
                connector, _, _ = await self._get_active_calendar_connector(tenant_id)
                await connector.delete_event(external_event_id)
            except CalendarNotConnectedError:
                # Calendar disconnected but we can still cancel in DB
                logger.warning("Calendar disconnected, cancelling in database only")
            except Exception as e:
                logger.error(f"Error deleting calendar event: {e}")
        
        # Update database status
        self.supabase.table("meetings").update({
            "status": "cancelled",
            "metadata": {
                **meeting.get("metadata", {}),
                "cancelled_at": datetime.utcnow().isoformat(),
                "cancellation_reason": reason
            }
        }).eq("id", meeting_id).execute()
        
        logger.info(f"Meeting cancelled: {meeting_id}")
        
        return {
            "success": True,
            "meeting_id": meeting_id,
            "message": "Meeting cancelled successfully"
        }
    
    async def get_meeting(
        self,
        tenant_id: str,
        meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get meeting by ID."""
        response = self.supabase.table("meetings").select(
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
        query = self.supabase.table("meetings").select(
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
    
    async def _create_meeting_reminders(
        self,
        meeting_id: str,
        tenant_id: str,
        lead_id: Optional[str],
        start_time: datetime,
        title: str,
        join_link: Optional[str] = None
    ) -> List[str]:
        """
        Create reminders for a meeting at T-24h, T-1h, and T-10m.
        
        Day 27: Timed Communication System
        
        Args:
            meeting_id: Meeting ID to link reminders to
            tenant_id: Tenant ID
            lead_id: Lead ID (used for contact info lookup)
            start_time: Meeting start time
            title: Meeting title
            join_link: Video conference join link
            
        Returns:
            List of created reminder IDs
        """
        reminder_offsets = [
            ("24h", timedelta(hours=24)),
            ("1h", timedelta(hours=1)),
            ("10m", timedelta(minutes=10))
        ]
        
        created_ids = []
        
        for reminder_type, offset in reminder_offsets:
            scheduled_at = start_time - offset
            
            # Don't create reminders in the past
            if scheduled_at <= datetime.utcnow():
                logger.info(f"Skipping {reminder_type} reminder - already past")
                continue
            
            # Generate idempotency key
            idempotency_key = f"meeting-{meeting_id}-{reminder_type}"
            
            reminder_data = {
                "tenant_id": tenant_id,
                "meeting_id": meeting_id,
                "lead_id": lead_id,
                "type": "sms",  # Default to SMS, worker will fallback to email if needed
                "scheduled_at": scheduled_at.isoformat(),
                "status": "pending",
                "idempotency_key": idempotency_key,
                "max_retries": 3,
                "content": {
                    "reminder_type": reminder_type,
                    "title": title,
                    "join_link": join_link,
                    "template": f"meeting_reminder_{reminder_type}"
                }
            }
            
            try:
                response = self.supabase.table("reminders").insert(reminder_data).execute()
                
                if response.data:
                    reminder_id = response.data[0]["id"]
                    created_ids.append(reminder_id)
                    logger.info(f"Created {reminder_type} reminder: {reminder_id} for meeting {meeting_id}")
            except Exception as e:
                logger.error(f"Failed to create {reminder_type} reminder: {e}")
        
        logger.info(f"Created {len(created_ids)} reminders for meeting {meeting_id}")
        return created_ids



# Singleton instance helper
_meeting_service: Optional[MeetingService] = None

def get_meeting_service(supabase: Client) -> MeetingService:
    """Get or create MeetingService instance."""
    global _meeting_service
    if _meeting_service is None:
        _meeting_service = MeetingService(supabase)
    return _meeting_service
