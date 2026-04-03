"""
Browser Media Gateway Implementation
Implements MediaGateway interface for browser-based voice interactions.

When ``telephony_mode`` is enabled (e.g. FreeSWITCH calls), outgoing
TTS audio (Float32) is converted back to Int16 PCM before being sent
over the WebSocket, and the output-buffer threshold is tuned for the
lower sample-rate / smaller frame size typical of telephony.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from fastapi import WebSocket

from app.domain.interfaces.media_gateway import MediaGateway
from app.utils.audio_utils import validate_pcm_format

logger = logging.getLogger(__name__)

# Input audio buffering: accumulate micro-chunks from browser AudioWorklet
# (128 samples = 8ms at 16kHz) into larger frames before validation/queuing.
# Deepgram Flux recommends ~80ms chunks for optimal performance.
INPUT_BUFFER_TARGET_MS = 80  # Target buffer duration before sending to STT
INPUT_BUFFER_MIN_MS = 20  # Minimum buffer duration to pass validation


@dataclass
class BrowserSession:
    """Session state for browser-based audio testing."""

    call_id: str
    websocket: WebSocket
    created_at: datetime = field(default_factory=datetime.utcnow)
    input_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=100)
    )
    output_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=100)
    )
    recording_buffer: list = field(default_factory=list)
    output_buffer: bytearray = field(
        default_factory=bytearray
    )  # Buffer for smooth TTS playback
    input_audio_buffer: bytearray = field(
        default_factory=bytearray
    )  # Buffer for accumulating small input chunks
    pending_byte: bytes = (
        b""  # Keep odd trailing byte until next chunk (Int16 frame alignment)
    )
    playback_complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    playback_tracking_active: bool = False
    playback_bytes_sent: int = 0
    is_active: bool = True

    # Audio metrics
    chunks_received: int = 0
    chunks_sent: int = 0
    total_bytes_received: int = 0
    total_bytes_sent: int = 0
    dropped_input_chunks: int = 0
    input_validation_errors: int = 0
    max_input_queue_depth: int = 0
    output_buffer_peak_bytes: int = 0
    dropped_output_bytes: int = 0
    ws_send_timeouts: int = 0
    ws_send_errors: int = 0
    last_send_latency_ms: float = 0.0


class BrowserMediaGateway(MediaGateway):
    """
    Media gateway for browser-based voice interactions.

    Audio Format:
    - Sample Rate: 16000 Hz
    - Bit Depth: 16-bit linear PCM
    - Channels: 1 (mono)
    - Audio comes from browser microphone via WebSocket
    - Audio goes to browser speakers via WebSocket
    """

    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}
        self._sample_rate: int = 16000
        self._input_sample_rate: int = 16000
        self._channels: int = 1
        self._bit_depth: int = 16
        self._frame_bytes: int = 2
        self._max_queue_size: int = 100
        self._target_buffer_ms: int = 60
        self._max_buffer_ms: int = 400
        self._ws_send_timeout_ms: int = 300
        self._telephony_mode: bool = False
        # TTS source format hint:
        # - "s16le": raw int16 PCM (Deepgram linear16)
        # - "f32le": float32 PCM (Google streaming)
        # - "auto": heuristic fallback (explicit opt-in only)
        self._tts_source_format: str = "s16le"

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the browser media gateway.

        Config keys:
            sample_rate (int): Outbound/playback audio sample rate (default 16000).
            input_sample_rate (int): Inbound/microphone audio sample rate.
                Defaults to sample_rate when omitted.
            channels (int): Mono / stereo (default 1).
            bit_depth (int): Bits per sample (default 16).
            max_queue_size (int): Per-session queue bound (default 100).
            target_buffer_ms (int): Outbound audio coalescing target (default 100ms).
            max_buffer_ms (int): Hard cap for outbound buffer (default 400ms).
            ws_send_timeout_ms (int): Timeout for websocket send (default 300ms).
            telephony_mode (bool): When True, convert Float32 TTS audio
                to Int16 PCM before sending over the WebSocket and use a
                smaller output-buffer threshold suited for telephony.
        """
        self._sample_rate = config.get("sample_rate", 16000)
        self._input_sample_rate = config.get("input_sample_rate", self._sample_rate)
        self._channels = config.get("channels", 1)
        self._bit_depth = config.get("bit_depth", 16)
        self._max_queue_size = int(config.get("max_queue_size", 100))
        self._target_buffer_ms = int(config.get("target_buffer_ms", 100))
        self._max_buffer_ms = int(config.get("max_buffer_ms", 400))
        self._ws_send_timeout_ms = int(config.get("ws_send_timeout_ms", 300))
        self._telephony_mode = config.get("telephony_mode", False)
        self._tts_source_format = self._normalize_tts_source_format(
            str(config.get("tts_source_format", "s16le")).lower()
        )

        if self._sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        if self._channels <= 0:
            raise ValueError("channels must be > 0")
        if self._bit_depth not in (8, 16, 24, 32):
            raise ValueError("bit_depth must be one of 8, 16, 24, 32")
        if self._max_queue_size <= 0:
            raise ValueError("max_queue_size must be > 0")
        if self._target_buffer_ms <= 0:
            raise ValueError("target_buffer_ms must be > 0")
        if self._max_buffer_ms < self._target_buffer_ms:
            raise ValueError("max_buffer_ms must be >= target_buffer_ms")
        if self._ws_send_timeout_ms <= 0:
            raise ValueError("ws_send_timeout_ms must be > 0")

        self._frame_bytes = max(1, self._channels * (self._bit_depth // 8))

        logger.info(
            f"BrowserMediaGateway initialized: output={self._sample_rate}Hz, "
            f"input={self._input_sample_rate}Hz, "
            f"{self._bit_depth}-bit, telephony_mode={self._telephony_mode}, "
            f"tts_source_format={self._tts_source_format}, "
            f"max_queue_size={self._max_queue_size}, target_buffer_ms={self._target_buffer_ms}, "
            f"max_buffer_ms={self._max_buffer_ms}, ws_send_timeout_ms={self._ws_send_timeout_ms}"
        )

    async def on_call_started(self, call_id: str, metadata: Dict[str, Any]) -> None:
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
            websocket=websocket,
            input_queue=asyncio.Queue(maxsize=self._max_queue_size),
            output_queue=asyncio.Queue(maxsize=self._max_queue_size),
        )

        self._sessions[call_id] = session

        logger.info(f"Browser session started: {call_id}", extra={"call_id": call_id})

    async def on_audio_received(self, call_id: str, audio_chunk: bytes) -> None:
        """
        Handle audio chunk from browser microphone.

        Accumulates small chunks from the browser AudioWorklet (which sends
        128-sample / 8ms frames) into larger buffers before validation and
        queuing for STT processing. This matches Deepgram Flux's recommendation
        of ~80ms chunks for optimal performance.

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

        # Accumulate incoming audio into the input buffer
        session.input_audio_buffer.extend(audio_chunk)

        # Calculate how much audio we have buffered
        bytes_per_second = self._input_sample_rate * self._frame_bytes
        buffered_ms = (len(session.input_audio_buffer) / bytes_per_second) * 1000

        # Only validate and queue when we have enough buffered audio
        if buffered_ms < INPUT_BUFFER_MIN_MS:
            return

        # Extract the buffered audio (aligned to frame boundaries)
        total_frames = len(session.input_audio_buffer) // self._frame_bytes
        target_frames = int((INPUT_BUFFER_TARGET_MS / 1000.0) * self._input_sample_rate)
        frames_to_extract = min(total_frames, max(target_frames, total_frames))
        bytes_to_extract = frames_to_extract * self._frame_bytes

        chunk_to_process = bytes(session.input_audio_buffer[:bytes_to_extract])
        session.input_audio_buffer = session.input_audio_buffer[bytes_to_extract:]

        is_valid, error = validate_pcm_format(
            chunk_to_process,
            expected_rate=self._input_sample_rate,
            expected_channels=self._channels,
            expected_bit_depth=self._bit_depth,
        )
        if not is_valid:
            session.input_validation_errors += 1
            if session.input_validation_errors <= 5:
                logger.debug(
                    f"Rejected invalid browser audio chunk for {call_id}: {error}"
                )
            return

        # Update metrics
        session.chunks_received += 1
        session.total_bytes_received += len(chunk_to_process)

        # Add to recording buffer
        session.recording_buffer.append(chunk_to_process)

        # Buffer for STT processing
        try:
            session.input_queue.put_nowait(chunk_to_process)
            session.max_input_queue_depth = max(
                session.max_input_queue_depth, session.input_queue.qsize()
            )
        except asyncio.QueueFull:
            # Drop oldest to maintain real-time
            try:
                session.input_queue.get_nowait()
                session.input_queue.put_nowait(chunk_to_process)
                session.dropped_input_chunks += 1
                session.max_input_queue_depth = max(
                    session.max_input_queue_depth, session.input_queue.qsize()
                )
            except asyncio.QueueEmpty:
                pass

    async def send_audio(self, call_id: str, audio_chunk: bytes) -> None:
        """
        Send TTS audio for playback.

        Converts Float32 audio (from TTS providers like Google) to Int16 PCM
        for browser compatibility. Also handles Int16 input (from Deepgram)
        by detecting format and skipping conversion when appropriate.

        Buffers small chunks to ~100 ms to prevent micro-jitter. The buffer
        threshold is automatically adjusted for telephony (smaller frames).
        """
        session = self._sessions.get(call_id)
        if not session:
            logger.warning(f"Unknown session for send: {call_id}")
            return

        if not session.is_active:
            return

        # Maintain 16-bit frame alignment across chunk boundaries.
        if session.pending_byte:
            audio_chunk = session.pending_byte + audio_chunk
            session.pending_byte = b""
        remainder = len(audio_chunk) % self._frame_bytes
        if remainder != 0:
            session.pending_byte = audio_chunk[-remainder:]
            audio_chunk = audio_chunk[:-remainder]
        if not audio_chunk:
            return

        # ----- Float32 → Int16 conversion -----
        # Prefer explicit source format to avoid accidental mis-detection.
        import numpy as np

        try:
            if self._tts_source_format == "f32le":
                float32_arr = np.frombuffer(audio_chunk, dtype=np.float32)
                int16_arr = (np.clip(float32_arr, -1.0, 1.0) * 32767.0).astype(np.int16)
                audio_chunk = int16_arr.tobytes()
            elif self._tts_source_format == "auto":
                # Fallback heuristic only when source format is unknown.
                if len(audio_chunk) >= 4 and len(audio_chunk) % 4 == 0:
                    float32_arr = np.frombuffer(audio_chunk, dtype=np.float32)
                    max_val = (
                        float(np.nanmax(np.abs(float32_arr)))
                        if float32_arr.size
                        else float("inf")
                    )
                    if np.isfinite(max_val) and max_val <= 1.5:
                        int16_arr = (np.clip(float32_arr, -1.0, 1.0) * 32767.0).astype(
                            np.int16
                        )
                        audio_chunk = int16_arr.tobytes()
                        logger.debug(
                            f"Converted auto-detected Float32 audio to Int16 (max_val={max_val:.3f})"
                        )
        except Exception as conv_err:
            # Conversion failed, assume audio is already in correct format
            logger.debug(f"Float32→Int16 conversion skipped: {conv_err}")

        # Add to recording buffer
        session.recording_buffer.append(audio_chunk)

        bytes_per_second = self._sample_rate * self._frame_bytes
        buf_threshold = max(
            self._frame_bytes,
            (bytes_per_second * self._target_buffer_ms) // 1000,
        )
        max_buffer_bytes = max(
            buf_threshold,
            (bytes_per_second * self._max_buffer_ms) // 1000,
        )

        session.output_buffer.extend(audio_chunk)
        session.output_buffer_peak_bytes = max(
            session.output_buffer_peak_bytes, len(session.output_buffer)
        )

        if len(session.output_buffer) > max_buffer_bytes:
            logger.warning(
                "Browser output buffer exceeded %sms for %s (%s bytes buffered); "
                "sending losslessly instead of trimming audio",
                self._max_buffer_ms,
                call_id,
                len(session.output_buffer),
            )

        while len(session.output_buffer) >= buf_threshold:
            # Send fixed-size frames so longer TTS replies do not get
            # time-compressed by trimming buffered audio.
            try:
                payload = bytes(session.output_buffer[:buf_threshold])
                payload_remainder = len(payload) % self._frame_bytes
                if payload_remainder != 0:
                    payload = payload[:-payload_remainder]
                if not payload:
                    break
                await self._send_payload(session, payload)
                del session.output_buffer[: len(payload)]
            except Exception as e:
                logger.error(f"Failed to send audio: {e}")
                session.is_active = False
                break

    async def flush_audio_buffer(self, call_id: str) -> None:
        """Flush remaining audio buffer at end of TTS."""
        session = self._sessions.get(call_id)
        if session and session.is_active:
            try:
                payload = bytes(session.output_buffer)
                payload_remainder = len(payload) % self._frame_bytes
                if payload_remainder != 0:
                    # Rare case: drop trailing bytes to keep frame alignment.
                    logger.debug(
                        "Dropping %s trailing byte(s) to preserve frame alignment for %s",
                        payload_remainder,
                        call_id,
                    )
                    payload = payload[:-payload_remainder]
                if payload:
                    await self._send_payload(session, payload)
                session.output_buffer = bytearray()
                if session.pending_byte:
                    logger.debug(
                        "Dropping pending byte to preserve Int16 frame alignment for %s",
                        call_id,
                    )
                session.pending_byte = b""
            except Exception as e:
                logger.error(f"Failed to flush audio buffer: {e}")

    async def clear_output_buffer(self, call_id: str) -> None:
        """Discard buffered outbound audio immediately."""
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return

        session.output_buffer = bytearray()
        session.pending_byte = b""
        session.playback_tracking_active = False
        session.playback_bytes_sent = 0
        session.playback_complete_event.clear()

    def start_playback_tracking(self, call_id: str) -> None:
        """Begin tracking one browser-played utterance."""
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return
        session.playback_complete_event.clear()
        session.playback_tracking_active = True
        session.playback_bytes_sent = 0

    def mark_playback_complete(self, call_id: str) -> None:
        """Mark the current browser utterance as fully played."""
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return
        if session.playback_tracking_active:
            session.playback_complete_event.set()

    async def wait_for_playback_complete(
        self,
        call_id: str,
        *,
        extra_grace_ms: int = 1200,
        minimum_timeout_ms: int = 1000,
        maximum_timeout_ms: int = 15000,
    ) -> bool:
        """
        Wait for the browser to confirm queued audio finished playing.

        Timeout is derived from the actual number of bytes sent for the
        current utterance plus a small grace window.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active or not session.playback_tracking_active:
            return True

        bytes_per_second = max(1, self._sample_rate * self._frame_bytes)
        expected_playback_ms = int(
            (session.playback_bytes_sent / bytes_per_second) * 1000
        )
        timeout_ms = max(
            minimum_timeout_ms,
            min(maximum_timeout_ms, expected_playback_ms + extra_grace_ms),
        )

        try:
            await asyncio.wait_for(
                session.playback_complete_event.wait(),
                timeout=timeout_ms / 1000.0,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out waiting for browser playback completion for %s after %sms "
                "(expected_playback_ms=%s, bytes_sent=%s)",
                call_id,
                timeout_ms,
                expected_playback_ms,
                session.playback_bytes_sent,
            )
            return False
        finally:
            session.playback_tracking_active = False
            session.playback_bytes_sent = 0
            session.playback_complete_event.clear()

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

    async def on_call_ended(self, call_id: str, reason: str) -> None:
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

        # Flush any remaining buffered input audio
        self._flush_input_buffer(session)

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
                "bytes_sent": session.total_bytes_sent,
            },
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
            "dropped_input_chunks": session.dropped_input_chunks,
            "input_validation_errors": session.input_validation_errors,
            "max_input_queue_depth": session.max_input_queue_depth,
            "output_buffer_peak_bytes": session.output_buffer_peak_bytes,
            "dropped_output_bytes": session.dropped_output_bytes,
            "ws_send_timeouts": session.ws_send_timeouts,
            "ws_send_errors": session.ws_send_errors,
            "last_send_latency_ms": session.last_send_latency_ms,
            "contract": {
                "sample_rate": self._sample_rate,
                "input_sample_rate": self._input_sample_rate,
                "channels": self._channels,
                "bit_depth": self._bit_depth,
                "frame_bytes": self._frame_bytes,
                "target_buffer_ms": self._target_buffer_ms,
                "max_buffer_ms": self._max_buffer_ms,
            },
            "is_active": session.is_active,
        }

    def _flush_input_buffer(self, session: BrowserSession) -> None:
        """Flush any remaining buffered input audio when session ends."""
        if not session.input_audio_buffer:
            return

        chunk = bytes(session.input_audio_buffer)
        session.input_audio_buffer.clear()

        # Align to frame boundary
        aligned_len = (len(chunk) // self._frame_bytes) * self._frame_bytes
        if aligned_len == 0:
            return

        chunk = chunk[:aligned_len]

        is_valid, error = validate_pcm_format(
            chunk,
            expected_rate=self._input_sample_rate,
            expected_channels=self._channels,
            expected_bit_depth=self._bit_depth,
        )
        if not is_valid:
            logger.debug(f"Dropping flushed audio on session end: {error}")
            return

        session.chunks_received += 1
        session.total_bytes_received += len(chunk)
        session.recording_buffer.append(chunk)

        try:
            session.input_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            session.dropped_input_chunks += 1
            logger.debug("Dropped flushed audio: input queue full")

    async def _send_payload(self, session: BrowserSession, payload: bytes) -> None:
        """
        Send audio payload with timeout so slow websocket clients don't stall
        the full pipeline.
        """
        started = datetime.utcnow()
        try:
            await asyncio.wait_for(
                session.websocket.send_bytes(payload),
                timeout=self._ws_send_timeout_ms / 1000.0,
            )
            elapsed_ms = (datetime.utcnow() - started).total_seconds() * 1000
            session.last_send_latency_ms = elapsed_ms
            session.chunks_sent += 1
            session.total_bytes_sent += len(payload)
            if session.playback_tracking_active:
                session.playback_bytes_sent += len(payload)
        except asyncio.TimeoutError:
            session.ws_send_timeouts += 1
            session.dropped_output_bytes += len(payload)
            logger.warning(
                "WebSocket send timeout for call %s after %sms; dropped %s bytes",
                session.call_id,
                self._ws_send_timeout_ms,
                len(payload),
            )
        except Exception:
            session.ws_send_errors += 1
            raise

    def _normalize_tts_source_format(self, raw_value: str) -> str:
        """
        Normalize source format aliases.
        Defaults to s16le because it is the safe baseline for telephony
        when format metadata is missing.
        """
        aliases = {
            "pcm_s16le": "s16le",
            "linear16": "s16le",
            "int16": "s16le",
            "pcm_f32le": "f32le",
            "float32": "f32le",
        }
        normalized = aliases.get(raw_value, raw_value)
        if normalized not in {"s16le", "f32le", "auto"}:
            logger.warning(
                "Unknown tts_source_format '%s'; defaulting to s16le",
                raw_value,
            )
            return "s16le"
        return normalized
