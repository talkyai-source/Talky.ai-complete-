"""
Dialer Job Model
Represents a single call job in the dialer queue
"""
from pydantic import BaseModel, Field
from typing import Optional, ClassVar, Set
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    """Status of a dialer job"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    SKIPPED = "skipped"              # Time window, limit reached
    GOAL_ACHIEVED = "goal_achieved"
    NON_RETRYABLE = "non_retryable"  # Spam, invalid, etc.


class CallOutcome(str, Enum):
    """Outcome of a call attempt"""
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SPAM = "spam"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    DISCONNECTED = "disconnected"
    GOAL_ACHIEVED = "goal_achieved"
    GOAL_NOT_ACHIEVED = "goal_not_achieved"
    VOICEMAIL = "voicemail"
    REJECTED = "rejected"


# Module-level constants for retry logic
RETRY_DELAY_SECONDS = 7200  # 2 hours between retries
MAX_ATTEMPTS = 3

# Outcomes that should trigger retry (busy, no answer, timeout)
RETRYABLE_OUTCOMES: Set[str] = {"busy", "no_answer", "timeout", "failed"}

# Outcomes that should NOT retry (spam, invalid, unavailable, disconnected)
NON_RETRYABLE_OUTCOMES: Set[str] = {"spam", "invalid", "unavailable", "disconnected", "rejected"}

# Outcomes that indicate success (goal achieved)
GOAL_OUTCOMES: Set[str] = {"goal_achieved", "answered"}


class DialerJob(BaseModel):
    """
    Represents a single outbound call job.
    
    Jobs are queued in Redis and processed by the Dialer Worker.
    Supports priority ordering and smart retry logic.
    """
    
    # Identity
    job_id: str = Field(..., description="Unique job identifier (UUID)")
    campaign_id: str = Field(..., description="Campaign this job belongs to")
    lead_id: str = Field(..., description="Lead to call")
    tenant_id: str = Field(..., description="Tenant for rule lookups")
    
    # Call details
    phone_number: str = Field(..., description="Phone number to dial")
    
    # Priority and ordering
    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Priority 1-10 (higher = more urgent). Priority >= 8 goes to priority queue."
    )
    
    # Status tracking
    status: JobStatus = Field(default=JobStatus.PENDING)
    attempt_number: int = Field(default=1, ge=1, description="Current attempt (1-based)")
    
    # Timing
    scheduled_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Result tracking
    last_outcome: Optional[CallOutcome] = None
    last_error: Optional[str] = None
    call_id: Optional[str] = None  # Reference to calls table
    
    # Pydantic V2 configuration
    model_config = {"use_enum_values": True}
    
    # Access module-level constants via properties
    @property
    def RETRY_DELAY_SECONDS(self) -> int:
        return RETRY_DELAY_SECONDS
    
    @property
    def MAX_ATTEMPTS(self) -> int:
        return MAX_ATTEMPTS
    
    def should_retry(self, goal_achieved: bool = False) -> tuple[bool, str]:
        """
        Determine if this job should be retried.
        
        Returns:
            (should_retry, reason)
        """
        # Rule 1: Never retry if goal achieved
        if goal_achieved:
            return False, "goal_achieved"
        
        if self.last_outcome and self.last_outcome in GOAL_OUTCOMES:
            return False, f"goal_outcome_{self.last_outcome}"
        
        # Rule 2: Never retry spam/invalid/unavailable
        if self.last_outcome and self.last_outcome in NON_RETRYABLE_OUTCOMES:
            return False, f"non_retryable_{self.last_outcome}"
        
        # Rule 3: Max attempts reached
        if self.attempt_number >= MAX_ATTEMPTS:
            return False, "max_attempts_reached"
        
        # Rule 4: Retry only busy/no-pickup/timeout
        if self.last_outcome and self.last_outcome in RETRYABLE_OUTCOMES:
            return True, f"retrying_{self.last_outcome}"
        
        # Unknown outcome - don't retry
        return False, f"unknown_outcome_{self.last_outcome}"
    
    def get_retry_delay(self) -> int:
        """Get delay in seconds before next retry attempt."""
        return RETRY_DELAY_SECONDS
    
    def to_redis_dict(self) -> dict:
        """Serialize for Redis storage."""
        return {
            "job_id": self.job_id,
            "campaign_id": self.campaign_id,
            "lead_id": self.lead_id,
            "tenant_id": self.tenant_id,
            "phone_number": self.phone_number,
            "priority": self.priority,
            "status": self.status if isinstance(self.status, str) else self.status.value,
            "attempt_number": self.attempt_number,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_outcome": self.last_outcome if isinstance(self.last_outcome, str) else (self.last_outcome.value if self.last_outcome else None),
            "last_error": self.last_error,
            "call_id": self.call_id
        }
    
    @classmethod
    def from_redis_dict(cls, data: dict) -> "DialerJob":
        """Deserialize from Redis storage."""
        # Parse datetime fields
        for dt_field in ["scheduled_at", "created_at", "processed_at", "completed_at"]:
            if data.get(dt_field) and isinstance(data[dt_field], str):
                data[dt_field] = datetime.fromisoformat(data[dt_field])
        
        return cls(**data)
    
    def __repr__(self) -> str:
        return (
            f"DialerJob(id={self.job_id[:8]}..., "
            f"phone={self.phone_number}, "
            f"priority={self.priority}, "
            f"status={self.status}, "
            f"attempt={self.attempt_number})"
        )
