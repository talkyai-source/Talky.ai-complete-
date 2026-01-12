"""
Calendar Provider Base Class
Abstract interface for calendar integrations.
"""
from abc import abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability


class CalendarEvent:
    """Represents a calendar event."""
    
    def __init__(
        self,
        id: Optional[str] = None,
        title: str = "",
        description: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        timezone: str = "UTC",
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        video_link: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.id = id
        self.title = title
        self.description = description
        self.start_time = start_time
        self.end_time = end_time
        self.timezone = timezone
        self.location = location
        self.attendees = attendees or []
        self.video_link = video_link
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "timezone": self.timezone,
            "location": self.location,
            "attendees": self.attendees,
            "video_link": self.video_link,
            "metadata": self.metadata
        }


class CalendarProvider(BaseConnector):
    """
    Abstract base class for calendar providers.
    
    Extends BaseConnector with calendar-specific methods.
    """
    
    @property
    def connector_type(self) -> str:
        return "calendar"
    
    @property
    def capabilities(self) -> List[ConnectorCapability]:
        return [
            ConnectorCapability.CREATE_EVENT,
            ConnectorCapability.UPDATE_EVENT,
            ConnectorCapability.DELETE_EVENT,
            ConnectorCapability.LIST_EVENTS,
            ConnectorCapability.GET_AVAILABILITY
        ]
    
    @abstractmethod
    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None,
        add_video_conference: bool = False,
        timezone: str = "UTC"
    ) -> CalendarEvent:
        """
        Create a calendar event.
        
        Args:
            title: Event title
            start_time: Event start time
            end_time: Event end time
            description: Event description
            attendees: List of attendee emails
            location: Event location
            add_video_conference: Add Google Meet/Zoom link
            timezone: Timezone for the event
            
        Returns:
            Created CalendarEvent with provider's event ID
        """
        pass
    
    @abstractmethod
    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None
    ) -> CalendarEvent:
        """Update an existing calendar event."""
        pass
    
    @abstractmethod
    async def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        pass
    
    @abstractmethod
    async def list_events(
        self,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 50
    ) -> List[CalendarEvent]:
        """List events in a time range."""
        pass
    
    @abstractmethod
    async def get_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        duration_minutes: int = 30
    ) -> List[Dict[str, datetime]]:
        """
        Get available time slots.
        
        Returns:
            List of {start, end} dicts for available slots
        """
        pass
