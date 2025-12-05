"""
Session Models
Defines CallSession, CallState, and LatencyMetric for runtime state management
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Any
from datetime import datetime
from fastapi import WebSocket
from enum import Enum
import asyncio

from app.domain.models.conversation import Message
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import AgentConfig


class CallState(str, Enum):
    """Call session state"""
    CONNECTING = "connecting"      # WebSocket connecting
    ACTIVE = "active"              # Call in progress
    LISTENING = "listening"        # Waiting for user speech
    PROCESSING = "processing"      # STT/LLM/TTS processing
    SPEAKING = "speaking"          # AI speaking
    ENDING = "ending"              # Graceful shutdown
    ENDED = "ended"                # Call completed
    ERROR = "error"                # Unrecoverable error


class LatencyMetric(BaseModel):
    """Single latency measurement"""
    component: str = Field(..., description="Component name (stt, llm, tts, total)")
    latency_ms: float = Field(..., ge=0.0, description="Latency in milliseconds")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Measurement timestamp")
    turn_id: int = Field(..., ge=0, description="Turn number")
    
    # Optional metadata
    success: bool = Field(default=True, description="Did operation succeed?")
    error_message: Optional[str] = Field(None, description="Error if failed")


class CallSession(BaseModel):
    """
    Runtime state for an active call
    Lives in memory + Redis during call, persisted to DB after
    
    Note: Some fields (websocket, queues) are not serializable to Redis
    """
    
    # ========== Identity ==========
    call_id: str = Field(..., description="Unique call identifier (UUID)")
    campaign_id: str = Field(..., description="Campaign this call belongs to")
    lead_id: str = Field(..., description="Lead/contact being called")
    tenant_id: Optional[str] = Field(None, description="Multi-tenant ID (future)")
    
    # ========== Connection State ==========
    # Note: websocket is NOT serialized to Redis (runtime only)
    vonage_call_uuid: str = Field(..., description="Vonage's call UUID")
    state: CallState = Field(default=CallState.CONNECTING, description="Current session state")
    
    # ========== Conversation State ==========
    conversation_history: List[Message] = Field(default_factory=list, description="Full message history")
    current_user_input: str = Field(default="", description="Accumulating transcript")
    current_ai_response: str = Field(default="", description="Accumulating LLM output")
    turn_id: int = Field(default=0, ge=0, description="Current turn number")
    
    # ========== Streaming State ==========
    stt_active: bool = Field(default=False, description="Is STT processing?")
    llm_active: bool = Field(default=False, description="Is LLM generating?")
    tts_active: bool = Field(default=False, description="Is TTS synthesizing?")
    user_speaking: bool = Field(default=False, description="Voice activity detected")
    ai_speaking: bool = Field(default=False, description="AI audio playing")
    
    # ========== Timing & Metrics ==========
    started_at: datetime = Field(default_factory=datetime.utcnow, description="Call start time")
    last_activity_at: datetime = Field(default_factory=datetime.utcnow, description="Last activity timestamp")
    total_user_speech_ms: int = Field(default=0, ge=0, description="Total user speech duration")
    total_ai_speech_ms: int = Field(default=0, ge=0, description="Total AI speech duration")
    latency_measurements: List[LatencyMetric] = Field(default_factory=list, description="Latency tracking")
    
    # ========== Configuration ==========
    system_prompt: str = Field(..., description="AI system prompt from campaign")
    voice_id: str = Field(..., description="TTS voice identifier")
    language: str = Field(default="en", description="Language code")
    
    # ========== Conversation State (Day 5) ==========
    conversation_state: ConversationState = Field(
        default=ConversationState.GREETING,
        description="Current conversation state"
    )
    conversation_context: ConversationContext = Field(
        default_factory=ConversationContext,
        description="Conversation context tracking"
    )
    agent_config: Optional[AgentConfig] = Field(
        None,
        description="Agent configuration for this call"
    )
    
    # ========== Runtime-Only Fields (Not Serialized) ==========
    # These are set after deserialization from Redis
    websocket: Optional[Any] = Field(None, exclude=True, description="Active WebSocket connection")
    audio_input_buffer: Optional[Any] = Field(None, exclude=True, description="Audio input queue")
    audio_output_buffer: Optional[Any] = Field(None, exclude=True, description="Audio output queue")
    transcript_buffer: Optional[Any] = Field(None, exclude=True, description="Transcript queue")
    
    # Pydantic v2 config
    model_config = ConfigDict(
        arbitrary_types_allowed=True,  # Allow WebSocket, asyncio.Queue
        use_enum_values=True  # Serialize enums as values
    )
    
    def model_dump_redis(self) -> dict:
        """
        Serialize to dict for Redis storage
        Excludes runtime-only fields (websocket, queues)
        """
        return self.model_dump(
            exclude={'websocket', 'audio_input_buffer', 'audio_output_buffer', 'transcript_buffer'},
            mode='json'
        )
    
    @classmethod
    def from_redis_dict(cls, data: dict, websocket: Optional[WebSocket] = None) -> "CallSession":
        """
        Deserialize from Redis dict
        Recreates runtime fields (queues)
        """
        # Convert datetime strings back to datetime objects
        if 'started_at' in data and isinstance(data['started_at'], str):
            data['started_at'] = datetime.fromisoformat(data['started_at'].replace('Z', '+00:00'))
        if 'last_activity_at' in data and isinstance(data['last_activity_at'], str):
            data['last_activity_at'] = datetime.fromisoformat(data['last_activity_at'].replace('Z', '+00:00'))
        
        # Convert latency measurements
        if 'latency_measurements' in data:
            data['latency_measurements'] = [
                LatencyMetric(**m) if isinstance(m, dict) else m
                for m in data['latency_measurements']
            ]
        
        # Convert conversation history
        if 'conversation_history' in data:
            data['conversation_history'] = [
                Message(**m) if isinstance(m, dict) else m
                for m in data['conversation_history']
            ]
        
        session = cls(**data)
        
        # Recreate runtime fields
        session.websocket = websocket
        session.audio_input_buffer = asyncio.Queue(maxsize=100)
        session.audio_output_buffer = asyncio.Queue(maxsize=100)
        session.transcript_buffer = asyncio.Queue(maxsize=50)
        
        return session
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity_at = datetime.utcnow()
    
    def add_latency_measurement(
        self,
        component: str,
        latency_ms: float,
        success: bool = True,
        error_message: Optional[str] = None
    ):
        """Add a latency measurement"""
        metric = LatencyMetric(
            component=component,
            latency_ms=latency_ms,
            turn_id=self.turn_id,
            success=success,
            error_message=error_message
        )
        self.latency_measurements.append(metric)
        self.update_activity()
    
    def is_stale(self, timeout_seconds: int = 300) -> bool:
        """Check if session has been inactive too long"""
        elapsed = (datetime.utcnow() - self.last_activity_at).total_seconds()
        return elapsed > timeout_seconds
    
    def get_duration_seconds(self) -> float:
        """Get total call duration in seconds"""
        return (datetime.utcnow() - self.started_at).total_seconds()
    
    def increment_turn(self):
        """Increment turn counter and reset current inputs"""
        self.turn_id += 1
        self.current_user_input = ""
        self.current_ai_response = ""
        self.update_activity()
    
    def get_average_latency(self, component: Optional[str] = None) -> float:
        """
        Get average latency for a component or overall
        
        Args:
            component: Component name (stt, llm, tts, total) or None for all
        
        Returns:
            Average latency in milliseconds, or 0.0 if no measurements
        """
        measurements = self.latency_measurements
        
        if component:
            measurements = [m for m in measurements if m.component == component]
        
        if not measurements:
            return 0.0
        
        successful = [m for m in measurements if m.success]
        if not successful:
            return 0.0
        
        return sum(m.latency_ms for m in successful) / len(successful)
