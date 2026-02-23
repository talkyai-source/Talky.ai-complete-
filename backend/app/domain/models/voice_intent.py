"""
Voice Intent Models for post-call analysis.

Day 29: Voice AI Intent Detection & Actions

These models support post-call transcript analysis to detect
actionable intents without adding latency to the call flow.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class VoiceActionableIntent(str, Enum):
    """
    Intents that may trigger assistant actions.
    
    These are distinct from conversation flow intents (UserIntent)
    and specifically represent actions the system can take.
    """
    BOOKING_REQUEST = "booking_request"       # User wants to schedule a meeting
    FOLLOW_UP_REQUEST = "follow_up_request"   # User requests email follow-up
    REMINDER_REQUEST = "reminder_request"     # User requests reminder
    CALLBACK_LATER = "callback_later"         # User wants callback at specific time
    NONE = "none"                             # No actionable intent detected


class ActionReadiness(str, Enum):
    """
    Whether an action can be executed or needs user input.
    
    This determines if we execute immediately or store as recommendation.
    """
    READY = "ready"                    # APIs available + permission granted
    MISSING_API = "missing_api"        # Required connector not connected
    NEEDS_PERMISSION = "needs_permission"  # User approval needed
    NOT_APPLICABLE = "not_applicable"  # No action needed


class DetectedIntent(BaseModel):
    """
    Result of intent detection on transcript.
    
    Stored in calls.detected_intents JSONB.
    """
    intent: VoiceActionableIntent
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence 0-1")
    extracted_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted entities like time_reference, attendee_email etc."
    )
    readiness: ActionReadiness = ActionReadiness.NEEDS_PERMISSION
    action_plan: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Proposed actions if ready to execute"
    )
    recommendation_message: Optional[str] = Field(
        default=None,
        description="Message to surface in next interaction if not ready"
    )
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


class CallRecommendation(BaseModel):
    """
    Recommendation to surface in next user interaction.
    
    When actions can't be executed (missing APIs or permission),
    we store recommendations to show the user what they could enable.
    """
    call_id: str
    tenant_id: str
    lead_id: Optional[str] = None
    
    intent: VoiceActionableIntent
    message: str = Field(..., description="User-facing recommendation message")
    
    # What's missing
    missing_connector: Optional[str] = None  # "calendar", "email", etc.
    needs_permission: bool = False
    
    # Proposed action if user enables
    proposed_action_plan: List[Dict[str, Any]] = Field(default_factory=list)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    dismissed: bool = False
    
    class Config:
        use_enum_values = True
