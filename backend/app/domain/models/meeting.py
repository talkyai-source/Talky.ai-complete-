"""
Meeting and Reminder Domain Models
Calendar events and scheduled reminders
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MeetingStatus(str, Enum):
    """Status of a meeting"""
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class AttendeeStatus(str, Enum):
    """RSVP status of an attendee"""
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"


class Attendee(BaseModel):
    """Meeting attendee"""
    email: str
    name: Optional[str] = None
    status: AttendeeStatus = AttendeeStatus.PENDING
    
    class Config:
        use_enum_values = True


class Meeting(BaseModel):
    """Calendar meeting/event booked through the assistant"""
    id: str
    tenant_id: str
    lead_id: Optional[str] = None
    call_id: Optional[str] = None
    connector_id: Optional[str] = None
    action_id: Optional[str] = None
    
    # Calendar provider details
    external_event_id: Optional[str] = None
    
    # Meeting details
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    timezone: str = "UTC"
    location: Optional[str] = None
    join_link: Optional[str] = None
    
    status: MeetingStatus = MeetingStatus.SCHEDULED
    attendees: List[Attendee] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        use_enum_values = True
    
    @property
    def duration_minutes(self) -> int:
        """Calculate meeting duration in minutes"""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)
    
    def add_attendee(self, email: str, name: Optional[str] = None) -> Attendee:
        """Add an attendee to the meeting"""
        attendee = Attendee(email=email, name=name)
        self.attendees.append(attendee)
        return attendee


class ReminderType(str, Enum):
    """Type of reminder delivery"""
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class ReminderStatus(str, Enum):
    """Status of a reminder"""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Reminder(BaseModel):
    """Scheduled reminder for a meeting or lead"""
    id: str
    tenant_id: str
    meeting_id: Optional[str] = None
    lead_id: Optional[str] = None
    action_id: Optional[str] = None
    
    type: ReminderType
    scheduled_at: datetime
    sent_at: Optional[datetime] = None
    status: ReminderStatus = ReminderStatus.PENDING
    
    content: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    retry_count: int = 0
    
    # New fields for Day 27: Timed Communication
    idempotency_key: Optional[str] = None
    channel: Optional[str] = None  # "sms" or "email"
    max_retries: int = 3
    next_retry_at: Optional[datetime] = None
    last_error: Optional[str] = None
    external_message_id: Optional[str] = None
    
    created_at: datetime
    
    class Config:
        use_enum_values = True
    
    @property
    def is_due(self) -> bool:
        """Check if reminder is due to be sent"""
        return (
            self.status == ReminderStatus.PENDING and 
            datetime.utcnow() >= self.scheduled_at
        )
    
    @property
    def can_retry(self) -> bool:
        """Check if reminder can be retried"""
        return self.retry_count < self.max_retries
