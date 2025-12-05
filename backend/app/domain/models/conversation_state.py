"""
Conversation State Models
Defines conversation states and transitions
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
    """Context information for conversation state"""
    objection_count: int = Field(default=0, ge=0, description="Number of objections handled")
    follow_up_count: int = Field(default=0, ge=0, description="Number of follow-up questions asked")
    user_confirmed: bool = Field(default=False, description="User has confirmed action")
    transfer_requested: bool = Field(default=False, description="User requested transfer")
    
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
