"""
Conversation Domain Models
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """Message role in conversation"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    """Single message in conversation"""
    role: MessageRole
    content: str
    timestamp: datetime = datetime.now()


class AudioChunk(BaseModel):
    """Audio data chunk"""
    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    timestamp: Optional[datetime] = None


class TranscriptChunk(BaseModel):
    """Transcription chunk from STT"""
    text: str
    is_final: bool = False
    confidence: Optional[float] = None
    timestamp: Optional[datetime] = None


class Conversation(BaseModel):
    """Complete conversation session"""
    id: str
    # MULTI-TENANT: Uncomment the line below to enable multi-tenancy
    # tenant_id: str  # Tenant identifier for multi-tenant isolation
    call_id: str
    messages: List[Message] = []
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: str = "active"  # active, completed, failed
