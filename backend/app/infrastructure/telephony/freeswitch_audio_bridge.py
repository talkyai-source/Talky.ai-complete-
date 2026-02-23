"""
FreeSWITCH Audio Bridge
WebSocket server that bridges FreeSWITCH audio (via mod_audio_fork) to the AI voice pipeline.

This enables real-time bidirectional audio streaming:
- FreeSWITCH → WebSocket → STT → AI → TTS → WebSocket → FreeSWITCH

Usage:
    The FastAPI app registers this endpoint at /ws/freeswitch-audio
    FreeSWITCH connects via dialplan: <action application="audio_fork" data="ws://..."/>
"""
import asyncio
import logging
import struct
from typing import Optional, Callable, Dict
from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


@dataclass
class AudioSession:
    """Active audio session from FreeSWITCH."""
    call_uuid: str
    websocket: WebSocket
    sample_rate: int = 8000
    channels: int = 1
    format: str = "L16"  # Linear 16-bit PCM
    active: bool = True


class FreeSwitchAudioBridge:
    """
    Bridges FreeSWITCH audio streams to AI voice pipeline.
    
    mod_audio_fork sends audio as raw PCM via WebSocket.
    We receive caller audio, process through STT/AI/TTS, and send response audio back.
    """
    
    def __init__(self):
        self._sessions: Dict[str, AudioSession] = {}
        self._background_tasks: set = set()  # Track tasks to prevent GC and log errors
        
        # Callbacks for voice pipeline integration
        self._on_audio_received: Optional[Callable] = None
        self._on_session_start: Optional[Callable] = None
        self._on_session_end: Optional[Callable] = None
    
    def _track_task(self, coro) -> None:
        """Create a tracked background task that logs exceptions instead of swallowing them."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        def _done(t):
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error(f"Background task failed: {t.exception()}")
        task.add_done_callback(_done)
    
    def set_audio_callback(self, callback: Callable) -> None:
        """Set callback for received audio: async def callback(call_uuid, audio_bytes)"""
        self._on_audio_received = callback
    
    def set_session_start_callback(self, callback: Callable) -> None:
        """Set callback for session start: async def callback(call_uuid)"""
        self._on_session_start = callback
    
    def set_session_end_callback(self, callback: Callable) -> None:
        """Set callback for session end: async def callback(call_uuid)"""
        self._on_session_end = callback
    
    async def handle_websocket(self, websocket: WebSocket, call_uuid: str) -> None:
        """
        Handle a WebSocket connection from FreeSWITCH mod_audio_fork.
        
        Args:
            websocket: FastAPI WebSocket connection
            call_uuid: FreeSWITCH call UUID
        """
        await websocket.accept()
        
        logger.info(f"🎤 FreeSWITCH audio WebSocket connected: {call_uuid[:8]}")
        
        session = AudioSession(
            call_uuid=call_uuid,
            websocket=websocket
        )
        self._sessions[call_uuid] = session
        
        # Notify session start
        if self._on_session_start:
            self._track_task(self._on_session_start(call_uuid))
        
        try:
            # Receive audio stream from FreeSWITCH
            while session.active:
                try:
                    # mod_audio_fork sends binary audio data
                    data = await websocket.receive_bytes()
                    
                    if data and self._on_audio_received:
                        # Forward to voice pipeline (tracked async task)
                        self._track_task(
                            self._on_audio_received(call_uuid, data)
                        )
                        
                except WebSocketDisconnect:
                    logger.info(f"🎤 FreeSWITCH audio WebSocket disconnected: {call_uuid[:8]}")
                    break
                except Exception as e:
                    logger.error(f"Audio receive error: {e}")
                    break
                    
        finally:
            session.active = False
            self._sessions.pop(call_uuid, None)
            
            # Notify session end
            if self._on_session_end:
                self._track_task(self._on_session_end(call_uuid))
            
            logger.info(f"🎤 Audio session ended: {call_uuid[:8]}")
    
    async def send_audio(self, call_uuid: str, audio_data: bytes) -> bool:
        """
        Send audio back to FreeSWITCH for playback.
        
        Args:
            call_uuid: Call UUID
            audio_data: PCM audio (8kHz, 16-bit, mono)
            
        Returns:
            True if sent successfully
        """
        session = self._sessions.get(call_uuid)
        if not session or not session.active:
            return False
        
        try:
            await session.websocket.send_bytes(audio_data)
            return True
        except Exception as e:
            logger.error(f"Audio send error for {call_uuid[:8]}: {e}")
            return False
    
    async def send_audio_chunked(
        self, 
        call_uuid: str, 
        audio_data: bytes,
        chunk_ms: int = 20
    ) -> bool:
        """
        Send audio in properly-timed chunks for real-time playback.
        
        Args:
            call_uuid: Call UUID
            audio_data: PCM audio (8kHz, 16-bit, mono)
            chunk_ms: Chunk size in milliseconds
        """
        session = self._sessions.get(call_uuid)
        if not session or not session.active:
            return False
        
        # Calculate chunk size: 8000 Hz * 2 bytes * chunk_ms/1000
        bytes_per_ms = 16  # 8000 * 2 / 1000
        chunk_size = bytes_per_ms * chunk_ms
        
        try:
            for i in range(0, len(audio_data), chunk_size):
                if not session.active:
                    break
                    
                chunk = audio_data[i:i+chunk_size]
                await session.websocket.send_bytes(chunk)
                await asyncio.sleep(chunk_ms / 1000.0)
            
            return True
            
        except Exception as e:
            logger.error(f"Chunked audio send error: {e}")
            return False
    
    def get_websocket(self, call_uuid: str) -> Optional[WebSocket]:
        """Get the WebSocket for an active audio session."""
        session = self._sessions.get(call_uuid)
        return session.websocket if session and session.active else None

    def is_session_active(self, call_uuid: str) -> bool:
        """Check if a session is active."""
        session = self._sessions.get(call_uuid)
        return session is not None and session.active
    
    def get_active_sessions(self) -> list:
        """Get list of active session UUIDs."""
        return list(self._sessions.keys())
    
    async def close_session(self, call_uuid: str) -> None:
        """Close an audio session."""
        session = self._sessions.get(call_uuid)
        if session:
            session.active = False
            try:
                await session.websocket.close()
            except Exception:
                pass


# Global audio bridge instance
_audio_bridge: Optional[FreeSwitchAudioBridge] = None


def get_audio_bridge() -> FreeSwitchAudioBridge:
    """Get the global audio bridge instance."""
    global _audio_bridge
    if _audio_bridge is None:
        _audio_bridge = FreeSwitchAudioBridge()
    return _audio_bridge
