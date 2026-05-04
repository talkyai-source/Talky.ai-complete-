"""Campaign API schemas."""
from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CampaignStartRequest(BaseModel):
    """Request body for starting a campaign."""

    priority_override: Optional[int] = None
    tenant_id: Optional[str] = None
    first_speaker: Literal["agent", "user"] = "agent"


class CampaignCreateRequest(BaseModel):
    """Request body for creating a campaign.

    Persona fields are mandatory for new campaigns. This prevents new
    campaigns from bypassing the production prompt composer and falling
    into the legacy estimation prompt path.
    """

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: str = Field(default="")
    voice_id: str = Field(..., min_length=1, max_length=100)
    goal: Optional[str] = None
    persona_type: Literal["lead_gen", "customer_support", "receptionist"]
    agent_names: List[str] = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1)
    campaign_slots: dict = Field(default_factory=dict)

    @field_validator("agent_names")
    @classmethod
    def _validate_agent_names(cls, v: List[str]) -> List[str]:
        from app.services.scripts.prompts import validate_pool

        return validate_pool(v)


class CampaignUpdateRequest(BaseModel):
    """Request body for editing a campaign.

    Edits also require persona fields so an update cannot strip script_config
    and bypass the production prompt composer.
    """

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    system_prompt: str = Field(default="")
    voice_id: str = Field(..., min_length=1, max_length=100)
    goal: Optional[str] = None
    persona_type: Literal["lead_gen", "customer_support", "receptionist"]
    agent_names: List[str] = Field(..., min_length=1)
    company_name: str = Field(..., min_length=1)
    campaign_slots: dict = Field(default_factory=dict)

    @field_validator("agent_names")
    @classmethod
    def _validate_agent_names(cls, v: List[str]) -> List[str]:
        from app.services.scripts.prompts import validate_pool

        return validate_pool(v)


class ContactCreate(BaseModel):
    """Request body for adding a single contact to a campaign."""

    phone_number: str = Field(..., description="Phone number in any format (will be normalized)")
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    custom_fields: Optional[dict] = Field(default_factory=dict)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-\(\)\.]", "", v)
        if not cleaned:
            raise ValueError("Phone number cannot be empty")
        if len(cleaned) < 4:
            raise ValueError("Phone number too short (minimum 4 digits for SIP extensions)")
        return v


class ContactListResponse(BaseModel):
    """Response for listing contacts."""

    items: List[dict]
    page: int
    page_size: int
    total: int
