"""
Campaign Prompt Configuration
Per-campaign prompt templates, compliance text, and LLM settings.

Day 17: Enables different campaigns to have customized system prompts,
tools context, and compliance/legal text while sharing the base infrastructure.
"""
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class CampaignPromptConfig(BaseModel):
    """
    Campaign-specific prompt configuration.
    
    Stored in campaigns.prompt_config JSONB column.
    Allows each campaign to customize:
    - System prompt overrides
    - Compliance/legal text
    - LLM behavior (temperature, tokens)
    - Response style
    """
    
    # System prompt customization
    system_prompt_override: Optional[str] = Field(
        default=None,
        description="Complete system prompt override (Jinja2 template)"
    )
    
    greeting_override: Optional[str] = Field(
        default=None,
        description="Custom greeting template"
    )
    
    # Compliance and legal text
    compliance_text: Optional[str] = Field(
        default=None,
        description="Legal disclaimers and compliance requirements (TCPA, etc.)"
    )
    
    # Tools context (for future function calling)
    tools_context: Optional[str] = Field(
        default=None,
        description="Context for available tools/functions"
    )
    
    # LLM behavior overrides
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="LLM temperature override"
    )
    
    max_tokens: Optional[int] = Field(
        default=None,
        ge=50,
        le=500,
        description="Max tokens per response override"
    )
    
    # Response style configuration
    response_style: str = Field(
        default="conversational",
        description="Response style: 'formal', 'casual', 'professional', 'conversational'"
    )
    
    language: str = Field(
        default="en",
        description="Language code for responses"
    )
    
    # Sentence constraints for voice
    max_sentences: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum sentences per response for voice brevity"
    )
    
    # Custom context variables for templates
    context_variables: Dict[str, str] = Field(
        default_factory=dict,
        description="Custom variables for prompt templates"
    )
    
    @classmethod
    def from_db_record(cls, record: Dict[str, Any]) -> "CampaignPromptConfig":
        """Create config from database JSONB record"""
        if not record:
            return cls()
        return cls(**record)
    
    def to_db_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return self.model_dump(exclude_none=True)
    
    def get_effective_temperature(self, default: float = 0.6) -> float:
        """Get temperature with fallback to default"""
        return self.temperature if self.temperature is not None else default
    
    def get_effective_max_tokens(self, default: int = 100) -> int:
        """Get max_tokens with fallback to default"""
        return self.max_tokens if self.max_tokens is not None else default
