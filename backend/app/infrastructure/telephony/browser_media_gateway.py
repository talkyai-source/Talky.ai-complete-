"""
Browser Media Gateway Implementation
Implements MediaGateway interface for browser-based voice testing.

This gateway uses the SAME interface as VonageMediaGateway, allowing
the VoicePipelineService to work identically with browser audio.
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from fastapi import WebSocket

from app.domain.interfaces.media_gateway import MediaGateway

logger = logging.getLogger(__name__)


@dataclass
class BrowserSession:
    """Session state for browser-based audio testing."""
    call_id: str
    websocket: WebSocket
    created_at: datetime = field(default_factory=datetime.utcnow)
    input_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    output_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    recording_buffer: list = field(default_factory=list)
    is_active: bool = True
    
    # Audio metrics
    chunks_received: int = 0
    chunks_sent: int = 0
    total_bytes_received: int = 0
    total_bytes_sent: int = 0


class BrowserMediaGateway(MediaGateway):
    """
    Media gateway for browser-based voice testing.
    
    Uses the SAME interface as VonageMediaGateway, enabling the
    VoicePipelineService to work with browser audio input/output.
    
    Audio Format:
    - Sample Rate: 16000 Hz (same as Vonage)
    - Bit Depth: 16-bit linear PCM
    - Channels: 1 (mono)
    
    The only difference from VonageMediaGateway:
    - Audio comes from browser microphone via WebSocket
    - Audio goes to browser speakers via WebSocket
    - No RTP/G.711 encoding (browser uses raw PCM)
    """
    
    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}
        self._sample_rate: int = 16000
        self._channels: int = 1
        self._bit_depth: int = 16
    
    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the browser media gateway."""
        self._sample_rate = config.get("sample_rate", 16000)
        self._channels = config.get("channels", 1)
        self._bit_depth = config.get("bit_depth", 16)
        logger.info(f"BrowserMediaGateway initialized: {self._sample_rate}Hz, {self._bit_depth}-bit")
    
    async def on_call_started(
        self,
        call_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Handle browser test session start.
        
        Args:
            call_id: Unique session identifier
            metadata: Must include 'websocket' key with WebSocket instance
        """
        websocket = metadata.get("websocket")
        if not websocket:
            raise ValueError("BrowserMediaGateway requires 'websocket' in metadata")
        
        # Create session with audio queues
        session = BrowserSession(
            call_id=call_id,
            websocket=websocket
        )
        
        self._sessions[call_id] = session
        
        logger.info(
            f"Browser session started: {call_id}",
            extra={"call_id": call_id}
        )
    
    async def on_audio_received(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Handle audio chunk from browser microphone.
        
        Validates format and buffers for STT processing.
        
        Args:
            call_id: Session identifier
            audio_chunk: PCM audio data from browser
        """
        session = self._sessions.get(call_id)
        if not session:
            logger.warning(f"Unknown session for audio: {call_id}")
            return
        
        if not session.is_active:
            return
        
        # Update metrics
        session.chunks_received += 1
        session.total_bytes_received += len(audio_chunk)
        
        # Add to recording buffer
        session.recording_buffer.append(audio_chunk)
        
        # Buffer for STT processing
        try:
            session.input_queue.put_nowait(audio_chunk)
        except asyncio.QueueFull:
            # Drop oldest to maintain real-time
            try:
                session.input_queue.get_nowait()
                session.input_queue.put_nowait(audio_chunk)
            except asyncio.QueueEmpty:
                pass
    
    async def send_audio(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Send TTS audio to browser for playback.
        
        Args:
            call_id: Session identifier
            audio_chunk: PCM audio data to play
        """
        session = self._sessions.get(call_id)
        if not session:
            logger.warning(f"Unknown session for send: {call_id}")
            return
        
        if not session.is_active:
            return
        
        # Update metrics
        session.chunks_sent += 1
        session.total_bytes_sent += len(audio_chunk)
        
        # Add to recording buffer
        session.recording_buffer.append(audio_chunk)
        
        # Send to browser via WebSocket
        try:
            await session.websocket.send_bytes(audio_chunk)
        except Exception as e:
            logger.error(f"Failed to send audio to browser: {e}")
            session.is_active = False
    
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get audio input queue for STT pipeline.
        
        Args:
            call_id: Session identifier
            
        Returns:
            Audio queue or None if session not found
        """
        session = self._sessions.get(call_id)
        return session.input_queue if session else None
    
    async def on_call_ended(
        self,
        call_id: str,
        reason: str
    ) -> None:
        """
        Handle browser test session end.
        
        Args:
            call_id: Session identifier
            reason: Reason for ending (user_hangup, error, timeout, etc.)
        """
        session = self._sessions.get(call_id)
        if not session:
            return
        
        session.is_active = False
        
        # Log metrics
        duration_seconds = (datetime.utcnow() - session.created_at).total_seconds()
        
        logger.info(
            f"Browser session ended: {call_id}",
            extra={
                "call_id": call_id,
                "reason": reason,
                "duration_seconds": duration_seconds,
                "chunks_received": session.chunks_received,
                "chunks_sent": session.chunks_sent,
                "bytes_received": session.total_bytes_received,
                "bytes_sent": session.total_bytes_sent
            }
        )
        
        # Cleanup
        del self._sessions[call_id]
    
    def get_recording_buffer(self, call_id: str):
        """Get recording buffer for session."""
        session = self._sessions.get(call_id)
        return session.recording_buffer if session else None
    
    def clear_recording_buffer(self, call_id: str) -> None:
        """Clear recording buffer to free memory."""
        session = self._sessions.get(call_id)
        if session:
            session.recording_buffer.clear()
    
    async def cleanup(self) -> None:
        """Clean up all sessions."""
        for call_id in list(self._sessions.keys()):
            await self.on_call_ended(call_id, "cleanup")
        self._sessions.clear()
        logger.info("BrowserMediaGateway cleaned up")
    
    @property
    def name(self) -> str:
        """Provider name."""
        return "browser"
    
    # =========================================================================
    # Browser-specific helper methods
    # =========================================================================
    
    def get_session(self, call_id: str) -> Optional[BrowserSession]:
        """Get session by call_id."""
        return self._sessions.get(call_id)
    
    def is_session_active(self, call_id: str) -> bool:
        """Check if session is active."""
        session = self._sessions.get(call_id)
        return session.is_active if session else False
    
    def get_session_metrics(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get session metrics for display."""
        session = self._sessions.get(call_id)
        if not session:
            return None
        
        duration_seconds = (datetime.utcnow() - session.created_at).total_seconds()
        
        return {
            "call_id": call_id,
            "duration_seconds": duration_seconds,
            "chunks_received": session.chunks_received,
            "chunks_sent": session.chunks_sent,
            "bytes_received": session.total_bytes_received,
            "bytes_sent": session.total_bytes_sent,
            "is_active": session.is_active
        }
