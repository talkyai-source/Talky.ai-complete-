"""
Agent Configuration Models
Defines agent goals, rules, and conversation flows
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict
from enum import Enum


class AgentGoal(str, Enum):
    """Agent conversation goals"""
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    LEAD_QUALIFICATION = "lead_qualification"
    CALLBACK_SCHEDULING = "callback_scheduling"
    INFORMATION_GATHERING = "information_gathering"
    SURVEY = "survey"
    REMINDER = "reminder"


class ConversationRule(BaseModel):
    """Rules and constraints for agent behavior"""
    allowed_phrases: List[str] = Field(
        default_factory=list,
        description="Phrases agent should use"
    )
    forbidden_phrases: List[str] = Field(
        default_factory=list,
        description="Phrases to avoid"
    )
    do_not_say_rules: List[str] = Field(
        default_factory=list,
        description="Explicit rules (e.g., 'no discounts', 'no medical advice')"
    )
    max_follow_up_questions: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum follow-up questions for uncertain responses"
    )
    require_confirmation: bool = Field(
        default=True,
        description="Require explicit confirmation before ending"
    )


class ConversationFlow(BaseModel):
    """Defines conversation flow based on user responses"""
    on_yes: str = Field(
        default="closing",
        description="Next state when user says yes"
    )
    on_no: str = Field(
        default="goodbye",
        description="Next state when user says no"
    )
    on_uncertain: str = Field(
        default="objection_handling",
        description="Next state when user is uncertain"
    )
    on_objection: str = Field(
        default="objection_handling",
        description="Next state when user objects"
    )
    on_request_human: str = Field(
        default="transfer",
        description="Next state when user requests human agent"
    )
    max_objection_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum attempts to handle objections"
    )


class AgentConfig(BaseModel):
    """Complete agent configuration for a campaign"""
    
    # Identity
    goal: AgentGoal = Field(..., description="Primary conversation goal")
    business_type: str = Field(..., description="Type of business (e.g., 'dental clinic')")
    agent_name: str = Field(..., description="Agent's name")
    company_name: str = Field(..., description="Company name")
    
    # Behavior
    rules: ConversationRule = Field(
        default_factory=ConversationRule,
        description="Conversation rules and constraints"
    )
    flow: ConversationFlow = Field(
        default_factory=ConversationFlow,
        description="Conversation flow configuration"
    )
    
    # Style
    tone: str = Field(
        default="polite, professional, conversational",
        description="Agent's tone and style"
    )
    personality_traits: List[str] = Field(
        default_factory=lambda: ["friendly", "helpful", "concise"],
        description="Personality traits"
    )
    
    # Constraints
    max_conversation_turns: int = Field(
        default=10,
        ge=3,
        le=20,
        description="Maximum conversation turns before ending"
    )
    response_max_sentences: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum sentences per response"
    )
    
    # Context (optional, campaign-specific)
    context: Dict[str, str] = Field(
        default_factory=dict,
        description="Additional context (appointment time, product name, etc.)"
    )
    
    def get_goal_description(self) -> str:
        """Get human-readable goal description"""
        goal_descriptions = {
            AgentGoal.APPOINTMENT_CONFIRMATION: f"confirm an appointment",
            AgentGoal.LEAD_QUALIFICATION: f"qualify a potential lead",
            AgentGoal.CALLBACK_SCHEDULING: f"schedule a callback",
            AgentGoal.INFORMATION_GATHERING: f"gather information",
            AgentGoal.SURVEY: f"conduct a survey",
            AgentGoal.REMINDER: f"provide a reminder"
        }
        return goal_descriptions.get(self.goal, "assist you")
    
    model_config = ConfigDict(use_enum_values=True)
    
    def validate_rules(self) -> bool:
        """Validate that rules are consistent"""
        # Check for conflicts between allowed and forbidden phrases
        allowed_set = set(phrase.lower() for phrase in self.rules.allowed_phrases)
        forbidden_set = set(phrase.lower() for phrase in self.rules.forbidden_phrases)
        
        conflicts = allowed_set.intersection(forbidden_set)
        if conflicts:
            raise ValueError(f"Conflicting phrases in allowed and forbidden: {conflicts}")
        
        return True
