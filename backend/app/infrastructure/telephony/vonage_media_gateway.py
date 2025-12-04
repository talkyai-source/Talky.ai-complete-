"""
Vonage Media Gateway Implementation
Handles Vonage-specific audio format and WebSocket integration
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from app.domain.interfaces.media_gateway import MediaGateway
from app.utils.audio_utils import validate_pcm_format, calculate_audio_duration_ms

logger = logging.getLogger(__name__)


class VonageMediaGateway(MediaGateway):
    """
    Media gateway implementation for Vonage Voice API.
    
    Handles:
    - Audio format validation (PCM 16-bit, 16kHz, mono)
    - Audio buffering with overflow protection
    - Session lifecycle management
    - Audio metrics tracking
    
    Vonage Audio Format:
    - Format: audio/l16;rate=16000 (PCM 16-bit linear)
    - Sample Rate: 16000 Hz
    - Channels: 1 (mono)
    - Encoding: Linear PCM (no compression)
    """
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._audio_queues: Dict[str, asyncio.Queue] = {}
        self._output_queues: Dict[str, asyncio.Queue] = {}
        self._session_metadata: Dict[str, Dict[str, Any]] = {}
        self._audio_metrics: Dict[str, Dict[str, Any]] = {}
        
        # Configuration
        self._sample_rate: int = 16000
        self._channels: int = 1
        self._bit_depth: int = 16
        self._max_queue_size: int = 100
        
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialize Vonage media gateway.
        
        Args:
            config: Configuration dictionary
                - sample_rate: Expected sample rate (default: 16000)
                - channels: Expected channels (default: 1)
                - max_queue_size: Max audio buffer size (default: 100)
        """
        self._config = config
        self._sample_rate = config.get("sample_rate", 16000)
        self._channels = config.get("channels", 1)
        self._max_queue_size = config.get("max_queue_size", 100)
        
        logger.info(
            f"Vonage Media Gateway initialized: "
            f"{self._sample_rate}Hz, {self._channels} channel(s)"
        )
    
    async def on_call_started(
        self,
        call_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Handle call start event.
        
        Creates audio queues and initializes session tracking.
        
        Args:
            call_id: Unique call identifier
            metadata: Call metadata (campaign_id, lead_id, etc.)
        """
        logger.info(f"Call started: {call_id}", extra={"call_id": call_id})
        
        # Create audio queues
        self._audio_queues[call_id] = asyncio.Queue(maxsize=self._max_queue_size)
        self._output_queues[call_id] = asyncio.Queue(maxsize=self._max_queue_size)
        
        # Store metadata
        self._session_metadata[call_id] = {
            **metadata,
            "started_at": datetime.utcnow(),
            "status": "active"
        }
        
        # Initialize metrics
        self._audio_metrics[call_id] = {
            "total_chunks": 0,
            "total_bytes": 0,
            "total_duration_ms": 0.0,
            "validation_errors": 0,
            "buffer_overflows": 0,
            "last_chunk_at": None
        }
        
        logger.debug(
            f"Audio queues created for call {call_id}",
            extra={
                "call_id": call_id,
                "max_queue_size": self._max_queue_size
            }
        )
    
    async def on_audio_received(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Handle incoming audio chunk from Vonage.
        
        Validates format, updates metrics, and buffers the chunk.
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw PCM audio data
        """
        if call_id not in self._audio_queues:
            logger.warning(
                f"Received audio for unknown call: {call_id}",
                extra={"call_id": call_id}
            )
            return
        
        # Validate audio format
        is_valid, error = validate_pcm_format(
            audio_chunk,
            self._sample_rate,
            self._channels,
            self._bit_depth
        )
        
        if not is_valid:
            logger.warning(
                f"Invalid audio format: {error}",
                extra={
                    "call_id": call_id,
                    "chunk_size": len(audio_chunk),
                    "error": error
                }
            )
            self._audio_metrics[call_id]["validation_errors"] += 1
            return
        
        # Calculate duration
        duration_ms = calculate_audio_duration_ms(
            audio_chunk,
            self._sample_rate,
            self._channels,
            self._bit_depth
        )
        
        # Update metrics
        metrics = self._audio_metrics[call_id]
        metrics["total_chunks"] += 1
        metrics["total_bytes"] += len(audio_chunk)
        metrics["total_duration_ms"] += duration_ms
        metrics["last_chunk_at"] = datetime.utcnow()
        
        # Buffer audio chunk
        queue = self._audio_queues[call_id]
        
        try:
            # Non-blocking put with overflow handling
            queue.put_nowait(audio_chunk)
            
            logger.debug(
                f"Audio buffered: {len(audio_chunk)} bytes ({duration_ms:.1f}ms)",
                extra={
                    "call_id": call_id,
                    "chunk_size": len(audio_chunk),
                    "duration_ms": duration_ms,
                    "queue_size": queue.qsize()
                }
            )
        
        except asyncio.QueueFull:
            # Buffer overflow - drop oldest chunk
            try:
                queue.get_nowait()  # Remove oldest
                queue.put_nowait(audio_chunk)  # Add new
                
                metrics["buffer_overflows"] += 1
                
                logger.warning(
                    f"Audio buffer overflow, dropped oldest chunk",
                    extra={
                        "call_id": call_id,
                        "total_overflows": metrics["buffer_overflows"]
                    }
                )
            except:
                logger.error(
                    f"Failed to handle buffer overflow",
                    extra={"call_id": call_id}
                )
    
    async def on_call_ended(
        self,
        call_id: str,
        reason: str
    ) -> None:
        """
        Handle call end event.
        
        Logs final metrics and cleans up resources.
        
        Args:
            call_id: Unique call identifier
            reason: Reason for call ending
        """
        logger.info(
            f"Call ended: {call_id} (reason: {reason})",
            extra={"call_id": call_id, "reason": reason}
        )
        
        # Update metadata
        if call_id in self._session_metadata:
            self._session_metadata[call_id]["status"] = "ended"
            self._session_metadata[call_id]["ended_at"] = datetime.utcnow()
            self._session_metadata[call_id]["end_reason"] = reason
        
        # Log final metrics
        if call_id in self._audio_metrics:
            metrics = self._audio_metrics[call_id]
            logger.info(
                f"Call metrics for {call_id}",
                extra={
                    "call_id": call_id,
                    "total_chunks": metrics["total_chunks"],
                    "total_bytes": metrics["total_bytes"],
                    "total_duration_ms": metrics["total_duration_ms"],
                    "validation_errors": metrics["validation_errors"],
                    "buffer_overflows": metrics["buffer_overflows"]
                }
            )
        
        # Clean up queues (don't remove yet, let pipeline finish)
        # Queues will be removed in cleanup() or after pipeline completes
    
    async def send_audio(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Send audio chunk to Vonage (outbound audio).
        
        Buffers TTS audio for transmission back to the user.
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw PCM audio data to send
        """
        if call_id not in self._output_queues:
            logger.warning(
                f"Cannot send audio for unknown call: {call_id}",
                extra={"call_id": call_id}
            )
            return
        
        queue = self._output_queues[call_id]
        
        try:
            await queue.put(audio_chunk)
            
            logger.debug(
                f"Outbound audio queued: {len(audio_chunk)} bytes",
                extra={
                    "call_id": call_id,
                    "chunk_size": len(audio_chunk),
                    "queue_size": queue.qsize()
                }
            )
        
        except Exception as e:
            logger.error(
                f"Failed to queue outbound audio: {e}",
                extra={"call_id": call_id, "error": str(e)}
            )
    
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get the audio input queue for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Audio queue or None if call not found
        """
        return self._audio_queues.get(call_id)
    
    def get_output_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get the audio output queue for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Output queue or None if call not found
        """
        return self._output_queues.get(call_id)
    
    def get_metrics(self, call_id: str) -> Optional[Dict[str, Any]]:
        """
        Get audio metrics for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Metrics dictionary or None if call not found
        """
        return self._audio_metrics.get(call_id)
    
    async def cleanup(self) -> None:
        """
        Release all resources and clean up.
        
        Closes all active calls and releases resources.
        """
        logger.info(f"Cleaning up Vonage Media Gateway")
        
        # Clear all queues
        for call_id in list(self._audio_queues.keys()):
            logger.debug(f"Cleaning up call: {call_id}")
            
            # Clear queues
            if call_id in self._audio_queues:
                while not self._audio_queues[call_id].empty():
                    try:
                        self._audio_queues[call_id].get_nowait()
                    except:
                        break
            
            if call_id in self._output_queues:
                while not self._output_queues[call_id].empty():
                    try:
                        self._output_queues[call_id].get_nowait()
                    except:
                        break
        
        # Clear all data structures
        self._audio_queues.clear()
        self._output_queues.clear()
        self._session_metadata.clear()
        self._audio_metrics.clear()
        
        logger.info("Vonage Media Gateway cleanup complete")
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "vonage"
    
    def __repr__(self) -> str:
        return (
            f"VonageMediaGateway("
            f"sample_rate={self._sample_rate}, "
            f"channels={self._channels}, "
            f"active_calls={len(self._audio_queues)})"
        )
