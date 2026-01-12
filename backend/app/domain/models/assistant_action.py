"""
Assistant Action Domain Models
Defines action types, statuses, and the action model for audit logging
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class ActionType(str, Enum):
    """Types of actions the assistant can perform"""
    # Communication
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    
    # Calling
    INITIATE_CALL = "initiate_call"
    
    # Calendar
    BOOK_MEETING = "book_meeting"
    UPDATE_MEETING = "update_meeting"
    CANCEL_MEETING = "cancel_meeting"
    
    # Reminders
    SET_REMINDER = "set_reminder"
    
    # Campaign management
    START_CAMPAIGN = "start_campaign"
    PAUSE_CAMPAIGN = "pause_campaign"
    
    # Data queries (for audit purposes)
    QUERY_DATA = "query_data"


class ActionStatus(str, Enum):
    """Status of an action execution"""
    PENDING = "pending"          # Queued for execution
    RUNNING = "running"          # Currently executing
    COMPLETED = "completed"      # Successfully completed
    FAILED = "failed"            # Execution failed
    CANCELLED = "cancelled"      # Cancelled by user or system
    SCHEDULED = "scheduled"      # Scheduled for future execution


class ActionTrigger(str, Enum):
    """What triggered the action"""
    CHAT = "chat"                # User chat message
    CALL_OUTCOME = "call_outcome"  # Call completed with outcome
    SCHEDULE = "schedule"        # Scheduled trigger
    WEBHOOK = "webhook"          # External webhook
    SYSTEM = "system"            # System-initiated


class AssistantAction(BaseModel):
    """
    Represents an action executed by the assistant.
    Used for audit logging and tracking action status.
    """
    id: str
    tenant_id: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    connector_id: Optional[str] = None
    
    type: ActionType
    status: ActionStatus = ActionStatus.PENDING
    triggered_by: ActionTrigger = ActionTrigger.CHAT
    
    # Input/Output data
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Timing
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    
    class Config:
        use_enum_values = True
    
    def mark_running(self) -> None:
        """Mark action as running"""
        self.status = ActionStatus.RUNNING
        self.started_at = datetime.utcnow()
    
    def mark_completed(self, output: Dict[str, Any]) -> None:
        """Mark action as completed with output"""
        self.status = ActionStatus.COMPLETED
        self.output_data = output
        self.completed_at = datetime.utcnow()
        if self.started_at:
            self.duration_ms = int(
                (self.completed_at - self.started_at).total_seconds() * 1000
            )
    
    def mark_failed(self, error: str) -> None:
        """Mark action as failed with error"""
        self.status = ActionStatus.FAILED
        self.error = error
        self.completed_at = datetime.utcnow()
        if self.started_at:
            self.duration_ms = int(
                (self.completed_at - self.started_at).total_seconds() * 1000
            )


# Action input schemas for validation
class SendEmailInput(BaseModel):
    """Input for send_email action"""
    to: List[str]  # Email addresses
    subject: str
    body: str
    lead_ids: Optional[List[str]] = None  # If sending to leads
    template_id: Optional[str] = None


class SendSMSInput(BaseModel):
    """Input for send_sms action"""
    to: List[str]  # Phone numbers
    message: str
    lead_ids: Optional[List[str]] = None


class InitiateCallInput(BaseModel):
    """Input for initiate_call action"""
    phone_number: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None


class BookMeetingInput(BaseModel):
    """Input for book_meeting action"""
    lead_id: Optional[str] = None
    title: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)  # Emails


class SetReminderInput(BaseModel):
    """Input for set_reminder action"""
    meeting_id: Optional[str] = None
    lead_id: Optional[str] = None
    scheduled_at: datetime
    message: str
    type: str = "email"  # email, sms, push
