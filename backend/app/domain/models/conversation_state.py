"""
Conversation State Models
Defines conversation states, transitions, and call outcomes
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from enum import Enum


class ConversationState(str, Enum):
    """Conversation states for the agent"""
    GREETING = "greeting"
    QUALIFICATION = "qualification"
    OBJECTION_HANDLING = "objection_handling"
    CLOSING = "closing"
    TRANSFER = "transfer"
    GOODBYE = "goodbye"


class CallOutcomeType(str, Enum):
    """
    Explicit call outcomes for QA tracking and analytics.
    Each call ends with exactly one outcome type.
    """
    SUCCESS = "success"                   # Goal achieved (appointment confirmed, lead qualified)
    DECLINED = "declined"                 # User explicitly said no
    NOT_INTERESTED = "not_interested"     # User showed no interest after objection handling
    CALLBACK_REQUESTED = "callback_requested"  # User asked to be called back later
    TRANSFER_TO_HUMAN = "transfer_to_human"    # User requested human agent
    MAX_TURNS_REACHED = "max_turns_reached"    # Hit conversation turn limit
    ERROR = "error"                       # System error occurred (LLM failure, etc.)
    UNKNOWN = "unknown"                   # Unable to determine outcome


class UserIntent(str, Enum):
    """Detected user intents"""
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"
    OBJECTION = "objection"
    REQUEST_HUMAN = "request_human"
    REQUEST_INFO = "request_info"
    GREETING = "greeting"
    GOODBYE = "goodbye"
    CALLBACK = "callback"  # User requests callback
    UNKNOWN = "unknown"


class StateTransition(BaseModel):
    """Defines a state transition"""
    model_config = ConfigDict(use_enum_values=True)
    
    from_state: ConversationState = Field(..., description="Source state")
    to_state: ConversationState = Field(..., description="Destination state")
    trigger: UserIntent = Field(..., description="Intent that triggers transition")
    condition: Optional[str] = Field(None, description="Optional condition")
    priority: int = Field(default=0, description="Priority (higher = checked first)")


class ConversationContext(BaseModel):
    """Context information for conversation state and outcome tracking"""
    # Objection tracking
    objection_count: int = Field(default=0, ge=0, description="Number of objections handled")
    follow_up_count: int = Field(default=0, ge=0, description="Number of follow-up questions asked")
    user_confirmed: bool = Field(default=False, description="User has confirmed action")
    transfer_requested: bool = Field(default=False, description="User requested transfer")
    
    # Outcome tracking (Day 17)
    call_outcome: Optional[CallOutcomeType] = Field(default=None, description="Final call outcome")
    outcome_reason: Optional[str] = Field(default=None, description="Reason for outcome")
    goal_achieved: bool = Field(default=False, description="Whether the call goal was achieved")
    callback_requested: bool = Field(default=False, description="User requested callback")
    
    # Error tracking for graceful degradation
    llm_error_count: int = Field(default=0, ge=0, description="Number of LLM errors during call")
    
    def reset_objection_tracking(self):
        """Reset objection tracking counters"""
        self.objection_count = 0
        self.follow_up_count = 0
    
    def increment_objection(self) -> int:
        """Increment objection counter and return new count"""
        self.objection_count += 1
        return self.objection_count
    
    def increment_follow_up(self) -> int:
        """Increment follow-up counter and return new count"""
        self.follow_up_count += 1
        return self.follow_up_count
    
    def increment_llm_error(self) -> int:
        """Increment LLM error counter and return new count"""
        self.llm_error_count += 1
        return self.llm_error_count
    
    def set_outcome(self, outcome: CallOutcomeType, reason: str = None):
        """Set the call outcome with optional reason"""
        self.call_outcome = outcome
        self.outcome_reason = reason
        if outcome == CallOutcomeType.SUCCESS:
            self.goal_achieved = True
