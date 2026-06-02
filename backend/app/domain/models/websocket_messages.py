"""
WebSocket Message Schemas
Defines all message types for voice streaming protocol

Based on latest Pydantic v2 and FastAPI WebSocket documentation (2024)
"""
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from typing import Literal, Optional, Dict, Any, Union
from datetime import datetime
from enum import Enum


class MessageDirection(str, Enum):
    """Direction of audio/data flow"""
    INBOUND = "inbound"   # User → AI (from Vonage)
    OUTBOUND = "outbound" # AI → User (to Vonage)


class MessageType(str, Enum):
    """All supported WebSocket message types"""
    # Audio messages
    AUDIO_CHUNK = "audio_chunk"
    
    # Control messages
    TRANSCRIPT_CHUNK = "transcript_chunk"
    TURN_END = "turn_end"
    LLM_START = "llm_start"
    LLM_END = "llm_end"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    
    # Session control
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    
    # Error handling
    ERROR = "error"
    
    # Heartbeat
    PING = "ping"
    PONG = "pong"


# ============================================================================
# AUDIO MESSAGES (Binary Frames)
# ============================================================================

class AudioChunkMessage(BaseModel):
    """
    Audio data message (sent as binary WebSocket frame)
    
    Vonage sends audio as PCM 16-bit linear, 16kHz (audio/l16;rate=16000)
    In production, this will be sent as raw binary PCM data with a small header.
    For development/debugging, we use JSON with base64-encoded audio.
    """
    type: Literal[MessageType.AUDIO_CHUNK] = MessageType.AUDIO_CHUNK
    call_id: str = Field(..., description="Unique call identifier")
    direction: MessageDirection = Field(..., description="Inbound or outbound")
    data: bytes = Field(..., description="Raw PCM audio data (linear16)")
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz (16000 for Vonage)")
    channels: int = Field(default=1, description="Number of audio channels (mono)")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Message timestamp")
    sequence: int = Field(..., ge=0, description="Sequence number for ordering")
    
    @field_serializer('data')
    def serialize_bytes(self, v: bytes) -> str:
        """Serialize bytes to hex for JSON serialization in logs"""
        return v.hex()


# ============================================================================
# CONTROL MESSAGES (Text Frames)
# ============================================================================

class TranscriptChunkMessage(BaseModel):
    """
    Partial or final transcript from STT
    """
    type: Literal[MessageType.TRANSCRIPT_CHUNK] = MessageType.TRANSCRIPT_CHUNK
    call_id: str
    text: str = Field(..., description="Transcribed text")
    is_final: bool = Field(default=False, description="Is this a final transcript?")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TurnEndMessage(BaseModel):
    """
    Signal that user has finished speaking (end-of-turn detected)
    """
    type: Literal[MessageType.TURN_END] = MessageType.TURN_END
    call_id: str
    turn_id: int = Field(..., ge=0, description="Turn number in conversation")
    full_transcript: str = Field(..., description="Complete user utterance")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class LLMStartMessage(BaseModel):
    """
    Signal that LLM processing has started
    """
    type: Literal[MessageType.LLM_START] = MessageType.LLM_START
    call_id: str
    turn_id: int = Field(..., ge=0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class LLMEndMessage(BaseModel):
    """
    Signal that LLM has finished generating response
    """
    type: Literal[MessageType.LLM_END] = MessageType.LLM_END
    call_id: str
    turn_id: int = Field(..., ge=0)
    full_response: str = Field(..., description="Complete AI response")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TTSStartMessage(BaseModel):
    """
    Signal that TTS synthesis has started
    """
    type: Literal[MessageType.TTS_START] = MessageType.TTS_START
    call_id: str
    turn_id: int = Field(..., ge=0)
    text: str = Field(..., description="Text being synthesized")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TTSEndMessage(BaseModel):
    """
    Signal that TTS synthesis has completed
    """
    type: Literal[MessageType.TTS_END] = MessageType.TTS_END
    call_id: str
    turn_id: int = Field(..., ge=0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SessionStartMessage(BaseModel):
    """
    Initialize a new call session
    """
    type: Literal[MessageType.SESSION_START] = MessageType.SESSION_START
    call_id: str
    campaign_id: str
    lead_id: str
    system_prompt: str = Field(..., description="AI system prompt")
    voice_id: str = Field(..., description="TTS voice identifier")
    language: str = Field(default="en", description="Language code")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SessionEndMessage(BaseModel):
    """
    End a call session
    """
    type: Literal[MessageType.SESSION_END] = MessageType.SESSION_END
    call_id: str
    reason: str = Field(..., description="Reason for ending (hangup, error, timeout)")
    duration_seconds: float = Field(..., ge=0.0, description="Total call duration")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ErrorMessage(BaseModel):
    """
    Error notification
    """
    type: Literal[MessageType.ERROR] = MessageType.ERROR
    call_id: str
    error_code: str = Field(..., description="Error code (e.g., STT_FAILED, LLM_TIMEOUT)")
    error_message: str = Field(..., description="Human-readable error message")
    component: str = Field(..., description="Component that failed (stt, llm, tts)")
    recoverable: bool = Field(default=True, description="Can the call continue?")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PingMessage(BaseModel):
    """
    Heartbeat ping to keep connection alive
    """
    type: Literal[MessageType.PING] = MessageType.PING
    call_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PongMessage(BaseModel):
    """
    Heartbeat pong response
    """
    type: Literal[MessageType.PONG] = MessageType.PONG
    call_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# MESSAGE UTILITIES
# ============================================================================

# Union type for all possible messages
WebSocketMessage = Union[
    AudioChunkMessage,
    TranscriptChunkMessage,
    TurnEndMessage,
    LLMStartMessage,
    LLMEndMessage,
    TTSStartMessage,
    TTSEndMessage,
    SessionStartMessage,
    SessionEndMessage,
    ErrorMessage,
    PingMessage,
    PongMessage
]


def parse_message(message_type: str, data: Dict[str, Any]) -> WebSocketMessage:
    """
    Parse incoming WebSocket message based on type field
    
    Args:
        message_type: The 'type' field from the message
        data: Full message data dictionary
        
    Returns:
        Parsed message object
        
    Raises:
        ValueError: If message type is unknown
    """
    message_map = {
        MessageType.AUDIO_CHUNK: AudioChunkMessage,
        MessageType.TRANSCRIPT_CHUNK: TranscriptChunkMessage,
        MessageType.TURN_END: TurnEndMessage,
        MessageType.LLM_START: LLMStartMessage,
        MessageType.LLM_END: LLMEndMessage,
        MessageType.TTS_START: TTSStartMessage,
        MessageType.TTS_END: TTSEndMessage,
        MessageType.SESSION_START: SessionStartMessage,
        MessageType.SESSION_END: SessionEndMessage,
        MessageType.ERROR: ErrorMessage,
        MessageType.PING: PingMessage,
        MessageType.PONG: PongMessage,
    }
    
    message_class = message_map.get(message_type)
    if not message_class:
        raise ValueError(f"Unknown message type: {message_type}")
    
    return message_class(**data)
