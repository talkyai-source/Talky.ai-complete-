"""Campaign API schemas."""
from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CampaignStartRequest(BaseModel):
    """Request body for starting a campaign."""

    priority_override: Optional[int] = None
    tenant_id: Optional[str] = None
    first_speaker: Literal["agent", "user"] = "agent"
    # Max calls in flight at once (dialing/ringing/answered). The campaign dials
    # in batches of this size — a new call starts only as an earlier one reaches
    # a terminal outcome. Client-selectable; None keeps the campaign's existing
    # setting (or the DIALER_BATCH_SIZE default). 0 = unbounded.
    batch_size: Optional[int] = Field(default=None, ge=0, le=100)
    # Minimum wait (seconds) between consecutive call originations — paces the
    # campaign so calls go out on a steady cadence instead of back-to-back.
    # Works together with batch_size. None keeps the existing setting; 0 = no gap.
    call_gap_seconds: Optional[int] = Field(default=None, ge=0, le=3600)


class CampaignCallingSchedule(BaseModel):
    """Per-campaign calling hours + timezone, set by the client.

    These overlay the tenant's default calling rules for this campaign:
    the dialer evaluates the calling window in ``timezone`` and only dials
    within ``time_window_start``–``time_window_end`` on ``allowed_days`` —
    UNLESS ``ignore_schedule`` is on, in which case the window is treated
    as advisory only (the UI still warns, but calls go out anyway). This
    is the "give the client the power, warn but don't block" behavior.
    All fields optional; anything omitted falls back to the tenant default.
    """
    timezone: Optional[str] = Field(default=None, description="IANA timezone, e.g. America/New_York")
    time_window_start: Optional[str] = Field(default=None, description="HH:MM")
    time_window_end: Optional[str] = Field(default=None, description="HH:MM")
    allowed_days: Optional[List[int]] = Field(default=None, description="0=Mon … 6=Sun")
    ignore_schedule: bool = Field(
        default=False,
        description="When true, dial regardless of the window (override). The UI still shows out-of-hours warnings.",
    )

    @field_validator("time_window_start", "time_window_end")
    @classmethod
    def _validate_hhmm(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        try:
            h, m = v.split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
        except Exception:
            raise ValueError("time must be HH:MM (24h)")
        return v

    @field_validator("allowed_days")
    @classmethod
    def _validate_days(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is None:
            return None
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("allowed_days entries must be 0–6 (Mon–Sun)")
        return sorted(set(v))


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
    agent_name_genders: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Optional map of agent name -> 'male'|'female'. Used to pick a "
            "name matching the selected voice's gender on each call. Names "
            "without an entry are treated as unknown gender."
        ),
    )
    company_name: str = Field(..., min_length=1)
    campaign_slots: dict = Field(default_factory=dict)
    knowledge_driven: bool = Field(
        default=False,
        description=(
            "Knowledge-first campaign (vectorless-RAG wizard): content comes "
            "from the uploaded knowledge base, so per-persona content slots are "
            "not required and the persona prompt is a lean identity+tone shell."
        ),
    )
    tts_provider: Optional[str] = Field(
        default=None,
        description=(
            "Per-campaign TTS provider (cartesia|google|deepgram|elevenlabs). "
            "NULL uses the tenant global. The campaign's voice_id is validated "
            "against this provider and the call runs on it."
        ),
    )
    calling_schedule: Optional[CampaignCallingSchedule] = Field(
        default=None,
        description="Per-campaign calling hours + timezone (overlays tenant defaults).",
    )

    @field_validator("agent_names")
    @classmethod
    def _validate_agent_names(cls, v: List[str]) -> List[str]:
        from app.services.scripts.prompts import validate_pool

        return validate_pool(v)

    @field_validator("agent_name_genders")
    @classmethod
    def _validate_agent_name_genders(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if not v:
            return v
        out: Dict[str, str] = {}
        for name, gender in v.items():
            g = str(gender).strip().lower()
            if g not in ("male", "female"):
                raise ValueError(f"agent_name_genders[{name!r}] must be 'male' or 'female'.")
            out[str(name).strip()] = g
        return out


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
    agent_name_genders: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Optional map of agent name -> 'male'|'female'. Used to pick a "
            "name matching the selected voice's gender on each call. Names "
            "without an entry are treated as unknown gender."
        ),
    )
    company_name: str = Field(..., min_length=1)
    campaign_slots: dict = Field(default_factory=dict)
    knowledge_driven: bool = Field(
        default=False,
        description=(
            "Knowledge-first campaign (vectorless-RAG wizard): content comes "
            "from the uploaded knowledge base, so per-persona content slots are "
            "not required and the persona prompt is a lean identity+tone shell."
        ),
    )
    tts_provider: Optional[str] = Field(
        default=None,
        description=(
            "Per-campaign TTS provider (cartesia|google|deepgram|elevenlabs). "
            "NULL uses the tenant global. The campaign's voice_id is validated "
            "against this provider and the call runs on it."
        ),
    )
    calling_schedule: Optional[CampaignCallingSchedule] = Field(
        default=None,
        description="Per-campaign calling hours + timezone (overlays tenant defaults).",
    )

    @field_validator("agent_names")
    @classmethod
    def _validate_agent_names(cls, v: List[str]) -> List[str]:
        from app.services.scripts.prompts import validate_pool

        return validate_pool(v)

    @field_validator("agent_name_genders")
    @classmethod
    def _validate_agent_name_genders(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if not v:
            return v
        out: Dict[str, str] = {}
        for name, gender in v.items():
            g = str(gender).strip().lower()
            if g not in ("male", "female"):
                raise ValueError(f"agent_name_genders[{name!r}] must be 'male' or 'female'.")
            out[str(name).strip()] = g
        return out


class CampaignPromptPreviewRequest(BaseModel):
    """Request body for ``POST /campaigns/preview-prompt`` (T4-B4).

    Mirrors the fields ``CampaignCreateRequest`` carries that affect the
    composed prompt, plus the per-call ``direction`` so operators can see
    both outbound and inbound shapes from the same form draft. Read-only —
    the endpoint never writes to the DB.
    """

    persona_type: Literal["lead_gen", "customer_support", "receptionist"]
    company_name: str = Field(..., min_length=1)
    agent_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Single agent name to render the preview with. Real campaigns "
            "rotate from a pool, but a preview just needs one concrete value."
        ),
    )
    campaign_slots: dict = Field(default_factory=dict)
    additional_instructions: Optional[str] = None
    direction: Literal["outbound", "inbound"] = "outbound"
    knowledge_driven: bool = Field(
        default=False,
        description="Preview the lean knowledge-first prompt (skips content slots).",
    )


class CampaignPromptPreviewResponse(BaseModel):
    """Response body for ``POST /campaigns/preview-prompt``."""

    system_prompt: str = Field(
        ...,
        description="The full assembled system prompt the LLM would receive.",
    )
    greeting: str = Field(
        ...,
        description=(
            "The pre-synthesized TTS opener for this persona × direction. "
            "Same string the live call would speak as the AI's first audio."
        ),
    )
    direction: Literal["outbound", "inbound"]
    has_inbound_directive: bool = Field(
        ...,
        description=(
            "True when the assembled prompt carries the canonical inbound "
            "directive sentinel — i.e. the AI is shaped to behave as the "
            "receiver, not the caller."
        ),
    )
    prompt_chars: int = Field(
        ..., description="Length of the assembled system_prompt in characters.",
    )


class ApplyTtsConfigRequest(BaseModel):
    """Apply a saved TTS config (provider + voice) to chosen campaigns.

    Backs the AI Options 'Save → apply to these campaigns' modal. Each chosen
    campaign's tts_provider + voice_id are set to these values; unselected
    campaigns are untouched (that's the whole point of per-campaign provider).
    """

    tts_provider: str = Field(..., min_length=1)
    tts_voice_id: str = Field(..., min_length=1)
    campaign_ids: List[str] = Field(..., min_length=1)


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
        # Length/format is enforced in the add-contact endpoint, where it can be
        # tenant-scoped (some accounts have phone validation relaxed for
        # testing). Only emptiness is rejected here.
        return v


class ContactUpdate(BaseModel):
    """Request body for editing an existing contact. All fields optional —
    only the provided fields are changed."""

    phone_number: Optional[str] = Field(None, description="New phone number (normalized)")
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        cleaned = re.sub(r"[\s\-\(\)\.]", "", v)
        if not cleaned:
            raise ValueError("Phone number cannot be empty")
        # Length/format enforced in the endpoint (tenant-scoped).
        return v


class ContactListResponse(BaseModel):
    """Response for listing contacts."""

    items: List[dict]
    page: int
    page_size: int
    total: int
