"""
Call Domain Models
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum


class CallStatus(str, Enum):
    """Call status"""
    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"


class Call(BaseModel):
    """Call record"""
    id: str
    # MULTI-TENANT: Uncomment the line below to enable multi-tenancy
    # tenant_id: str  # Tenant identifier for multi-tenant isolation
    campaign_id: str
    lead_id: str
    phone_number: str
    status: CallStatus
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    cost: Optional[float] = None
