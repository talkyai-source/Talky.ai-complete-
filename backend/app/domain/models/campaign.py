"""
Campaign Domain Models
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum


class CampaignStatus(str, Enum):
    """Campaign status"""
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Campaign(BaseModel):
    """Campaign for outbound calls"""
    id: str
    # MULTI-TENANT: Optional (not required) is deliberate, not an oversight.
    # This Pydantic model is never constructed from a client request body
    # (grepped: no `Campaign(...)` call site in app/ takes API input — the
    # only constructors are in tests/unit/test_day9.py). Making the field
    # required would still be IDOR-safe *today*, but Optional[str] = None
    # is the defense-in-depth choice: if a future endpoint ever builds a
    # Campaign directly from a request payload (e.g. **body), an Optional
    # field can't be silently overridden by a client-supplied value the way
    # a required field invites ("just pass tenant_id in the JSON"). Every
    # construction site MUST set tenant_id itself from the authenticated
    # session / the already tenant-scoped DB row — never from client input.
    # The DB layer is the actual source of truth and already writes
    # tenant_id server-side on insert (see campaigns.py ~L293).
    tenant_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    status: CampaignStatus = CampaignStatus.DRAFT
    system_prompt: str  # AI agent instructions
    voice_id: str  # TTS voice identifier
    max_concurrent_calls: int = 10
    retry_failed: bool = True
    max_retries: int = 3
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_leads: int = 0
    calls_completed: int = 0
    calls_failed: int = 0
    # Day 9: New fields for campaign management
    goal: Optional[str] = None  # Campaign objective (e.g., "Book appointment", "Generate lead")
    script_config: Optional[dict] = None  # AI agent configuration (AgentConfig structure as JSONB)
    calling_config: Optional[dict] = None  # Calling rules including time_window (CallingRules structure)
