"""
Action Plan Domain Models for multi-step workflow orchestration.

Day 28: AssistantAgentService - Multi-step action plans with safety guardrails.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class AllowedActionType(str, Enum):
    """
    Hard allowlist of permitted action types.
    
    Only these action types can be executed through action plans.
    This ensures no free-form or arbitrary code execution.
    """
    # Calendar
    BOOK_MEETING = "book_meeting"
    UPDATE_MEETING = "update_meeting"
    CANCEL_MEETING = "cancel_meeting"
    CHECK_AVAILABILITY = "check_availability"
    
    # Communication
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    
    # Reminders
    SCHEDULE_REMINDER = "schedule_reminder"
    
    # Calling
    INITIATE_CALL = "initiate_call"
    
    # Campaign
    START_CAMPAIGN = "start_campaign"


class ActionStepCondition(str, Enum):
    """Conditions for conditional action execution."""
    ALWAYS = "always"
    IF_PREVIOUS_SUCCESS = "if_previous_success"
    IF_PREVIOUS_FAILED = "if_previous_failed"


class ActionStep(BaseModel):
    """
    Single action step in an action plan.
    
    Attributes:
        type: Action type from the allowlist
        parameters: Action-specific parameters
        use_result_from: Index of previous action to chain results from
        condition: Condition for executing this step
    """
    type: AllowedActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    use_result_from: Optional[int] = Field(
        None, 
        description="Index of previous action to chain results from"
    )
    condition: ActionStepCondition = Field(
        ActionStepCondition.ALWAYS,
        description="Condition for executing this step"
    )
    
    class Config:
        use_enum_values = True


class ActionPlanStatus(str, Enum):
    """Status of an action plan execution."""
    PENDING = "pending"              # Created but not started
    RUNNING = "running"              # Currently executing
    COMPLETED = "completed"          # All steps succeeded
    PARTIALLY_COMPLETED = "partially_completed"  # Some steps succeeded
    FAILED = "failed"                # Critical failure, stopped execution
    CANCELLED = "cancelled"          # Cancelled by user or system


class ActionStepResult(BaseModel):
    """Result of a single action step execution."""
    step_index: int
    action_type: str
    success: bool
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    executed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class ActionPlan(BaseModel):
    """
    Multi-step action plan for orchestrated workflows.
    
    Example:
        {
            "intent": "Book meeting and send confirmation",
            "context": {"lead_id": "abc123"},
            "actions": [
                {"type": "book_meeting", "parameters": {"title": "Demo", "time": "..."}},
                {"type": "send_email", "parameters": {"template": "confirmation"}, "use_result_from": 0}
            ]
        }
    """
    id: Optional[str] = None
    tenant_id: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    
    # Intent and context
    intent: str = Field(..., description="Natural language intent describing the workflow")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Context data like lead_id, campaign_id, etc."
    )
    
    # Actions
    actions: List[ActionStep] = Field(
        default_factory=list,
        description="Ordered list of action steps to execute"
    )
    
    # Execution state
    status: ActionPlanStatus = ActionPlanStatus.PENDING
    current_step: int = 0
    step_results: List[ActionStepResult] = Field(default_factory=list)
    error: Optional[str] = None
    
    # Timing
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    class Config:
        use_enum_values = True
    
    @field_validator("actions")
    @classmethod
    def validate_action_types(cls, actions: List[ActionStep]) -> List[ActionStep]:
        """Ensure all actions are in the allowlist."""
        allowed_values = {e.value for e in AllowedActionType}
        for i, action in enumerate(actions):
            action_type = action.type if isinstance(action.type, str) else action.type.value
            if action_type not in allowed_values:
                raise ValueError(
                    f"Action type '{action_type}' at index {i} is not allowed. "
                    f"Allowed types: {allowed_values}"
                )
        return actions
    
    @field_validator("actions")
    @classmethod
    def validate_result_references(cls, actions: List[ActionStep]) -> List[ActionStep]:
        """Ensure use_result_from references valid previous steps."""
        for i, action in enumerate(actions):
            if action.use_result_from is not None:
                if action.use_result_from < 0 or action.use_result_from >= i:
                    raise ValueError(
                        f"Action at index {i} references invalid step {action.use_result_from}. "
                        f"Must reference a previous step (0 to {i-1})."
                    )
        return actions
    
    @property
    def is_complete(self) -> bool:
        """Check if all steps have been executed."""
        return self.current_step >= len(self.actions)
    
    @property
    def successful_steps(self) -> int:
        """Count of successfully completed steps."""
        return sum(1 for r in self.step_results if r.success and not r.skipped)
    
    @property
    def failed_steps(self) -> int:
        """Count of failed steps."""
        return sum(1 for r in self.step_results if not r.success and not r.skipped)
    
    @property
    def skipped_steps(self) -> int:
        """Count of skipped steps."""
        return sum(1 for r in self.step_results if r.skipped)
    
    def get_step_result(self, index: int) -> Optional[ActionStepResult]:
        """Get result for a specific step by index."""
        for result in self.step_results:
            if result.step_index == index:
                return result
        return None


# Type aliases for convenience
ActionPlanDict = Dict[str, Any]
ActionStepDict = Dict[str, Any]
