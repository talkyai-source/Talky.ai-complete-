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
    # MULTI-TENANT: we have to uncomment it when we ll enable the mutli-tenant features
    # tenant_id: str  # Tenant identifier for multi-tenant isolation
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
