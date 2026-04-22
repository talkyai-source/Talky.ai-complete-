"""
Deepgram Flux STT Provider Implementation - aligned with official best practices.

Uses direct WebSocket connection with optimal configuration for voice agents.

Flux State Machine Events:
- Update: Transcript update (~every 0.25s)
- StartOfTurn: User started speaking (trigger barge-in immediately)
- EagerEndOfTurn: Early end-of-turn signal (moderate confidence - start LLM early)
- TurnResumed: User continued speaking (cancel speculative LLM call)
- EndOfTurn: User definitely finished speaking (proceed with response)

Default Configuration (Simple EndOfTurn-only Mode):
- eot_threshold=0.7: Confirm end of turn
- eot_timeout_ms=5000: Natural pause timeout
- eager_eot_threshold disabled unless explicitly configured

Audio Streaming:
- ~80ms chunks (2560 bytes at 16kHz linear16) for optimal Flux performance

Reference: https://developers.deepgram.com/docs/flux/configuration
"""
import os
import json
import asyncio
import random
import websockets
import logging
from typing import AsyncIterator, Optional, Callable
from dataclasses import dataclass

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import TranscriptChunk, AudioChunk, BargeInSignal
from app.utils.audio_utils import validate_pcm_format

logger = logging.getLogger(__name__)

# Deepgram recommended audio chunk size for optimal Flux performance.
# 40ms @ 16kHz, 16-bit mono = 1280 bytes.
# Reduced from 80ms (2560 bytes) — halves average STT input latency (~20ms
# median savings) with no degradation in Flux transcript quality.
FLUX_OPTIMAL_CHUNK_BYTES = 1280
FLUX_OPTIMAL_CHUNK_MS = 40
FLUX_HEARTBEAT_INTERVAL_SEC = 4.0
FLUX_HEARTBEAT_SILENCE_MS = 100

# WebSocket reconnection configuration
FLUX_MAX_RECONNECTS = 3          # Maximum mid-call reconnect attempts
FLUX_RECONNECT_BASE_DELAY = 0.5  # Initial backoff (seconds)
FLUX_RECONNECT_MAX_DELAY = 8.0   # Maximum backoff cap


@dataclass
class FluxEagerTurnState:
    """Tracks speculative LLM call state for EagerEndOfTurn pattern."""
    is_speculating: bool = False
    transcript: str = ""
    cancel_event: Optional[asyncio.Event] = None
    
    def reset(self):
        """Reset state when turn is finalized or resumed."""
        self.is_speculating = False
        self.transcript = ""
        if self.cancel_event:
            self.cancel_event.set()
        self.cancel_event = None


@dataclass
class FluxStreamStats:
    """Per-call streaming counters for Day 7 telemetry and evidence."""
    frames_in_total: int = 0
    frames_sent_total: int = 0
    frames_skipped_muted_total: int = 0
    frames_invalid_total: int = 0
    frames_dropped_total: int = 0
    transcript_events_total: int = 0
    stream_reconnect_total: int = 0
    stop_reason: str = "running"

    def to_dict(self) -> dict:
        return {
            "stt_frames_in_total": self.frames_in_total,
            "stt_frames_sent_total": self.frames_sent_total,
            "stt_frames_skipped_muted_total": self.frames_skipped_muted_total,
            "stt_frames_invalid_total": self.frames_invalid_total,
            "stt_frames_dropped_total": self.frames_dropped_total,
            "stt_transcript_events_total": self.transcript_events_total,
            "stt_stream_reconnect_total": self.stream_reconnect_total,
            "stt_stop_reason": self.stop_reason,
        }


class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT provider with optimized turn detection for voice agents.
    
    Implements Deepgram best practices:
    - EndOfTurn-only mode by default for simpler, more reliable agents
    - Optional eager mode when eager_eot_threshold is configured
    - ~80ms audio chunks for optimal streaming
    - EagerEndOfTurn + TurnResumed handling when eager mode is enabled
    - StartOfTurn immediate barge-in (with echo suppression)
    
    Uses direct WebSocket connection to Deepgram v2 API.
    """
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._config: dict = {}
        self._model: str = "flux-general-en"
        self._sample_rate: int = 16000
        self._encoding: str = "linear16"
        
        # EOT configuration (Simple mode defaults per Deepgram docs)
        self._eot_threshold: float = 0.7
        self._eager_eot_threshold: Optional[float] = None
        self._eot_timeout_ms: int = 5000
        
        # Echo suppression: mute microphone during TTS playback
        # This prevents the agent's voice from triggering StartOfTurn
        self._muted_calls: set[str] = set()
        self._mute_lock = asyncio.Lock()

        # Eager turn state tracking
        self._eager_states: dict[str, FluxEagerTurnState] = {}
        self._stream_stats: dict[str, FluxStreamStats] = {}

        # Pre-established WebSocket connections keyed by call_id.
        # pre_connect() stores a ws here; stream_transcribe() pops and reuses it,
        # eliminating the ~2s handshake from the hot path.
        self._pre_connections: dict = {}

    def _validate_turn_config(self) -> None:
        """Validate Flux turn-detection parameter ranges."""
        if not (0.5 <= self._eot_threshold <= 0.9):
            raise ValueError(
                f"eot_threshold must be between 0.5 and 0.9, got {self._eot_threshold}"
            )

        if not (500 <= self._eot_timeout_ms <= 10000):
            raise ValueError(
                f"eot_timeout_ms must be between 500 and 10000, got {self._eot_timeout_ms}"
            )

        if self._eager_eot_threshold is not None:
            if not (0.3 <= self._eager_eot_threshold <= 0.9):
                raise ValueError(
                    "eager_eot_threshold must be between 0.3 and 0.9, "
                    f"got {self._eager_eot_threshold}"
                )
            if self._eager_eot_threshold > self._eot_threshold:
                raise ValueError(
                    "eager_eot_threshold must be less than or equal to "
                    f"eot_threshold (got eager={self._eager_eot_threshold}, "
                    f"eot={self._eot_threshold})"
                )
    
    async def mute(self, call_id: str) -> None:
        """Mute microphone for a call (during TTS playback to prevent echo)."""
        async with self._mute_lock:
            self._muted_calls.add(call_id)
            logger.debug(f"Muted microphone for call {call_id}")
    
    async def unmute(self, call_id: str) -> None:
        """Unmute microphone for a call (after TTS playback)."""
        async with self._mute_lock:
            self._muted_calls.discard(call_id)
            logger.debug(f"Unmuted microphone for call {call_id}")
    
    def is_muted(self, call_id: str) -> bool:
        """Check if microphone is muted for a call."""
        return call_id in self._muted_calls
        
    async def initialize(self, config: dict) -> None:
        """Initialize Deepgram Flux with configuration"""
        self._config = config
        
        # Get API key
        self._api_key = config.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        if not self._api_key:
            raise ValueError("DEEPGRAM_API_KEY not set")
        
        self._model = config.get("model", "flux-general-en")
        self._sample_rate = config.get("sample_rate", 16000)
        self._encoding = config.get("encoding", "linear16")
        
        # EOT configuration from config (can override defaults)
        self._eot_threshold = float(config.get("eot_threshold", 0.7))
        eager = config.get("eager_eot_threshold")
        self._eager_eot_threshold = float(eager) if eager is not None else None
        self._eot_timeout_ms = int(config.get("eot_timeout_ms", 5000))
        self._validate_turn_config()
        
        logger.info(
            f"DeepgramFlux initialized: model={self._model}, sample_rate={self._sample_rate}, "
            f"eot_threshold={self._eot_threshold}, eager_eot_threshold={self._eager_eot_threshold}, "
            f"eot_timeout_ms={self._eot_timeout_ms}"
        )
    
    async def pre_connect(self, call_id: str) -> None:
        """
        Establish the Deepgram Flux WebSocket connection before audio starts.

        Call this immediately after session creation, before start_pipeline().
        stream_transcribe() will pop the stored connection and reuse it,
        skipping the ~2s WebSocket handshake from the hot path entirely.

        Non-fatal: if the pre-connect fails, stream_transcribe() falls back to
        its normal connect path automatically.
        """
        if not self._api_key:
            logger.warning("pre_connect called before initialize() — skipping")
            return

        params = [
            ("model", self._model),
            ("encoding", self._encoding),
            ("sample_rate", str(self._sample_rate)),
            ("eot_threshold", str(self._eot_threshold)),
            ("eot_timeout_ms", str(self._eot_timeout_ms)),
        ]
        if self._eager_eot_threshold is not None:
            params.append(("eager_eot_threshold", str(self._eager_eot_threshold)))
        query = "&".join(f"{k}={v}" for k, v in params)
        url = f"wss://api.deepgram.com/v2/listen?{query}"
        headers = {
            "Authorization": f"Token {self._api_key}",
            "User-Agent": "TalkyAI-VoiceAgent/1.0",
        }

        try:
            ws = await websockets.connect(url, extra_headers=headers)
            self._pre_connections[call_id] = ws
            logger.info(
                "Deepgram Flux pre-connected for call %s "
                "(eager=%s eot=%s timeout_ms=%s)",
                call_id, self._eager_eot_threshold,
                self._eot_threshold, self._eot_timeout_ms,
            )
        except Exception as exc:
            logger.warning(
                "Deepgram Flux pre_connect failed for %s — "
                "stream_transcribe() will connect normally: %s",
                call_id, exc,
            )

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to Deepgram Flux with optimized configuration.
        
        Uses Flux turn detection with EndOfTurn-only by default.
        Optional eager mode is enabled when eager_eot_threshold is configured.
        
        Args:
            audio_stream: Async iterator of audio chunks (PCM 16-bit)
            language: Language code
            context: Optional context
            call_id: Call ID for eager turn state tracking
            on_eager_end_of_turn: Callback for EagerEndOfTurn (start LLM early)
            
        Yields:
            TranscriptChunk: Partial or final transcripts
            BargeInSignal: When user starts speaking (StartOfTurn)
        """
        if not self._api_key:
            raise RuntimeError("Deepgram API key not set. Call initialize() first.")
        
        # Initialize eager turn state for this call
        if call_id:
            self._eager_states[call_id] = FluxEagerTurnState()
            self._stream_stats[call_id] = FluxStreamStats()
        eager_state = self._eager_states.get(call_id) if call_id else None
        stream_stats = self._stream_stats.get(call_id) if call_id else None
        stop_reason = "running"
        
        # Build WebSocket URL with Flux turn-detection parameters.
        # eager_eot_threshold is optional and only added when explicitly configured.
        params = [
            ("model", self._model),
            ("encoding", self._encoding),
            ("sample_rate", str(self._sample_rate)),
            ("eot_threshold", str(self._eot_threshold)),
            ("eot_timeout_ms", str(self._eot_timeout_ms)),
        ]
        if self._eager_eot_threshold is not None:
            params.append(("eager_eot_threshold", str(self._eager_eot_threshold)))
        query = "&".join(f"{k}={v}" for k, v in params)
        url = f"wss://api.deepgram.com/v2/listen?{query}"
        
        headers = {
            "Authorization": f"Token {self._api_key}",
            "User-Agent": "TalkyAI-VoiceAgent/1.0"
        }
        
        # Bounded queue — prevents unbounded memory growth on slow consumers
        transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        stop_event = asyncio.Event()
        last_audio_time = asyncio.get_event_loop().time()
        # One-shot flag: logs the first non-empty EndOfTurn for baseline
        # first-turn latency measurement (§6.1 of outbound_user_first_latency_plan.md).
        first_final_logged = [False]
        
        async def send_silence_heartbeat(ws):
            """
            Keep Flux stream active with short silent audio frames.

            Flux v2 control messages accept `CloseStream`/`Configure`; sending
            JSON `KeepAlive` causes UNPARSABLE_CLIENT_MESSAGE errors.
            """
            nonlocal last_audio_time
            silence_bytes = int(
                self._sample_rate * (FLUX_HEARTBEAT_SILENCE_MS / 1000.0) * 2
            )
            silent_frame = bytes(max(2, silence_bytes))
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(FLUX_HEARTBEAT_INTERVAL_SEC)
                    if stop_event.is_set():
                        break

                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_audio_time >= FLUX_HEARTBEAT_INTERVAL_SEC:
                        try:
                            await ws.send(silent_frame)
                            last_audio_time = current_time
                            logger.debug("Sent Flux silence heartbeat frame")
                        except websockets.exceptions.ConnectionClosed:
                            break
                        except Exception as e:
                            logger.warning(f"Flux silence heartbeat failed: {e}")
                            break
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Flux silence heartbeat error: {e}")
        
        async def send_audio(ws):
            """Send validated audio chunks to WebSocket with optimal chunking"""
            nonlocal last_audio_time, stop_reason
            chunks_sent = 0
            chunks_skipped = 0
            chunks_invalid = 0
            
            # Buffer for accumulating optimal chunk sizes
            audio_buffer = bytearray()
            
            logger.debug("send_audio started")
            _ws_open_time = asyncio.get_event_loop().time()
            _first_audio_sent = False
            try:
                async for audio_chunk in audio_stream:
                    if stop_event.is_set():
                        break
                    
                    # Skip audio when muted (during TTS to prevent echo)
                    if stream_stats:
                        stream_stats.frames_in_total += 1
                    if call_id and self.is_muted(call_id):
                        chunks_skipped += 1
                        if stream_stats:
                            stream_stats.frames_skipped_muted_total += 1
                        if chunks_skipped <= 3:
                            logger.debug(f"Skipping audio chunk - microphone muted for call {call_id}")
                        continue
                    
                    # Validate PCM format
                    is_valid, error = validate_pcm_format(
                        audio_chunk.data,
                        expected_rate=self._sample_rate,
                        expected_channels=1,
                        expected_bit_depth=16
                    )
                    
                    if not is_valid:
                        chunks_invalid += 1
                        if stream_stats:
                            stream_stats.frames_invalid_total += 1
                            stream_stats.frames_dropped_total += 1
                        if chunks_invalid <= 5:
                            logger.debug(f"Invalid PCM chunk: {error}")
                        continue
                    
                    # Accumulate in buffer for optimal chunk size
                    audio_buffer.extend(audio_chunk.data)
                    
                    # Send when we have optimal chunk size (~80ms)
                    while len(audio_buffer) >= FLUX_OPTIMAL_CHUNK_BYTES:
                        chunk_to_send = bytes(audio_buffer[:FLUX_OPTIMAL_CHUNK_BYTES])
                        if not _first_audio_sent:
                            _first_audio_sent = True
                            elapsed_ms = (asyncio.get_event_loop().time() - _ws_open_time) * 1000
                            if elapsed_ms > 8000:
                                logger.warning(
                                    "deepgram_flux_slow_start call_id=%s elapsed_ms=%.0f — "
                                    "Flux closes connection after 10s without audio; call setup is too slow",
                                    call_id, elapsed_ms,
                                    extra={"call_id": call_id, "flux_startup_ms": round(elapsed_ms)},
                                )
                        await ws.send(chunk_to_send)
                        audio_buffer = audio_buffer[FLUX_OPTIMAL_CHUNK_BYTES:]
                        chunks_sent += 1
                        if stream_stats:
                            stream_stats.frames_sent_total += 1
                        last_audio_time = asyncio.get_event_loop().time()
                    
                # Flush remaining audio
                if audio_buffer and not (call_id and self.is_muted(call_id)):
                    await ws.send(bytes(audio_buffer))
                    chunks_sent += 1
                    if stream_stats:
                        stream_stats.frames_sent_total += 1
                    
            except websockets.exceptions.ConnectionClosed:
                # Normal during shutdown/disconnect.
                logger.debug("Flux send_audio stopped: websocket closed")
            except Exception as e:
                stop_reason = "stt_internal_error"
                logger.error(f"Flux send_audio error: {e}")
            finally:
                logger.debug(f"send_audio ending. Sent {chunks_sent} chunks, {chunks_skipped} skipped, {chunks_invalid} invalid")
                if chunks_invalid > 0 or chunks_skipped > 0:
                    logger.info(f"Flux audio stats: {chunks_sent} sent, {chunks_skipped} skipped, {chunks_invalid} invalid")
                stop_event.set()
        
        async def receive_transcripts(ws):
            """Receive and process Flux TurnInfo events"""
            nonlocal stop_reason
            logger.debug("receive_transcripts started")
            msg_count = 0
            turn_info_count = 0
            
            try:
                async for message in ws:
                    msg_count += 1
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    if stream_stats:
                        stream_stats.transcript_events_total += 1
                    
                    if msg_count <= 10:
                        logger.debug(f"Msg #{msg_count}: type={msg_type}")
                    
                    if msg_type == "TurnInfo":
                        turn_info_count += 1
                        event = data.get("event", "")
                        transcript_text = data.get("transcript", "")
                        
                        if turn_info_count <= 10:
                            logger.debug(f"TurnInfo: event={event}, transcript={transcript_text[:30]!r}")
                        
                        if stop_event.is_set():
                            break
                        
                        # Handle StartOfTurn - immediate barge-in
                        if event == "StartOfTurn":
                            # Ignore StartOfTurn when muted (prevents echo from agent's voice)
                            if call_id and self.is_muted(call_id):
                                logger.debug(f"Ignoring StartOfTurn - microphone muted for call {call_id} (echo suppression)")
                                continue
                            
                            logger.info("Flux StartOfTurn - User started speaking, barge-in detected")
                            # Cancel any speculative processing
                            if eager_state:
                                eager_state.reset()
                            # Directly notify the pipeline's barge-in event so TTS
                            # synthesis stops immediately — even while the pipeline
                            # loop is blocked inside handle_turn_end and cannot
                            # consume the BargeInSignal from the transcript queue.
                            if on_barge_in:
                                try:
                                    on_barge_in()
                                except Exception:
                                    pass
                            # Also queue the signal for handle_barge_in bookkeeping
                            # (clears output buffer, updates session state).
                            # MUST NOT block: if the LLM is running and the queue
                            # is full, a blocking put would suspend here indefinitely,
                            # delaying the output-buffer clear until after the LLM
                            # finishes — causing stale audio to play post-barge-in.
                            # Solution: drop oldest non-critical Update chunks to
                            # make room, then put_nowait.
                            barge_in = BargeInSignal()
                            try:
                                transcript_queue.put_nowait(barge_in)
                            except asyncio.QueueFull:
                                drained = 0
                                while drained < 5:
                                    try:
                                        transcript_queue.get_nowait()
                                        drained += 1
                                    except asyncio.QueueEmpty:
                                        break
                                try:
                                    transcript_queue.put_nowait(barge_in)
                                except asyncio.QueueFull:
                                    logger.warning(
                                        "deepgram_flux: BargeIn dropped — queue full after drain"
                                    )
                        
                        # Handle EagerEndOfTurn - start LLM early (speculative)
                        elif event == "EagerEndOfTurn":
                            logger.debug(f"Flux EagerEndOfTurn: '{transcript_text}'")
                            if transcript_text and transcript_text.strip():
                                # Track speculative state
                                if eager_state:
                                    eager_state.is_speculating = True
                                    eager_state.transcript = transcript_text.strip()
                                    eager_state.cancel_event = asyncio.Event()
                                
                                # Yield partial transcript for display
                                chunk = TranscriptChunk(
                                    text=transcript_text.strip(),
                                    is_final=False,  # Not final yet
                                    confidence=data.get("end_of_turn_confidence", 0.5),
                                    metadata={"eager": True}
                                )
                                await transcript_queue.put(chunk)
                                
                                # Trigger early LLM processing via callback
                                if on_eager_end_of_turn:
                                    try:
                                        on_eager_end_of_turn(transcript_text.strip())
                                    except Exception as e:
                                        logger.warning(f"EagerEndOfTurn callback error: {e}")
                        
                        # Handle TurnResumed - cancel speculative LLM call
                        elif event == "TurnResumed":
                            logger.info("Flux TurnResumed - User continued speaking, cancelling speculative LLM")
                            if eager_state:
                                eager_state.reset()  # This signals cancellation
                            # Yield empty chunk to signal resumption
                            resume_chunk = TranscriptChunk(
                                text="",
                                is_final=False,
                                metadata={"resumed": True}
                            )
                            await transcript_queue.put(resume_chunk)
                        
                        # Handle EndOfTurn - finalize turn
                        elif event == "EndOfTurn":
                            logger.info(f"Flux EndOfTurn: '{transcript_text}'")

                            if transcript_text and transcript_text.strip():
                                if not first_final_logged[0]:
                                    first_final_logged[0] = True
                                    logger.info(
                                        "t_stt_first_final call_id=%s",
                                        call_id,
                                        extra={"call_id": call_id, "t_stt_first_final": 1},
                                    )
                                # Use the final transcript
                                chunk = TranscriptChunk(
                                    text=transcript_text.strip(),
                                    is_final=True,
                                    confidence=data.get("end_of_turn_confidence", 1.0)
                                )
                                await transcript_queue.put(chunk)
                            
                            # Reset eager state
                            if eager_state:
                                eager_state.reset()
                            
                            # Signal end of turn
                            end_chunk = TranscriptChunk(
                                text="",
                                is_final=True,
                                confidence=1.0
                            )
                            await transcript_queue.put(end_chunk)
                        
                        # Handle Update - partial transcript
                        elif event == "Update":
                            if transcript_text and transcript_text.strip():
                                chunk = TranscriptChunk(
                                    text=transcript_text.strip(),
                                    is_final=False,
                                    confidence=data.get("end_of_turn_confidence")
                                )
                                await transcript_queue.put(chunk)
                    
                    # Handle Results (fallback for non-Flux responses)
                    elif msg_type == "Results":
                        channel = data.get("channel", {})
                        alternatives = channel.get("alternatives", [])
                        if alternatives:
                            transcript = alternatives[0].get("transcript", "")
                            if transcript:
                                chunk = TranscriptChunk(
                                    text=transcript,
                                    is_final=False,
                                    confidence=alternatives[0].get("confidence")
                                )
                                await transcript_queue.put(chunk)
                    
                    elif msg_type == "Metadata":
                        logger.debug(f"Flux Metadata: {data}")
                    
                    elif msg_type == "Error":
                        stop_reason = "stt_provider_error"
                        logger.warning(f"Flux Error from Deepgram: {data}")
                        
            except websockets.exceptions.ConnectionClosed:
                if stop_reason == "running":
                    stop_reason = "stt_stream_closed"
                logger.info("Flux WebSocket closed")
            except Exception as e:
                if stop_reason == "running":
                    stop_reason = "stt_internal_error"
                logger.error(f"Flux receive error: {e}")
            finally:
                logger.debug(f"receive_transcripts ending. Total: {msg_count} msgs, {turn_info_count} TurnInfo")
                stop_event.set()
                await transcript_queue.put(None)
        
        # Main connection with automatic reconnection loop.
        # Auth errors (401/403) are fatal — do not reconnect.
        reconnect_count = 0
        try:
            while True:
                try:
                    # Re-use a pre-established connection (pre_connect() called
                    # before pipeline start) to skip the initial WebSocket handshake.
                    # Only available on the first attempt (reconnect_count == 0);
                    # subsequent reconnects always open a fresh connection.
                    _preconn = (
                        self._pre_connections.pop(call_id, None)
                        if (call_id and reconnect_count == 0)
                        else None
                    )
                    if _preconn is not None:
                        ws = _preconn
                        _ws_handshake_ms = 0.0
                        logger.info(
                            "Using pre-connected Deepgram Flux for %s "
                            "(eager=%s, eot=%s, timeout_ms=%s)",
                            call_id,
                            self._eager_eot_threshold,
                            self._eot_threshold,
                            self._eot_timeout_ms,
                        )
                    else:
                        _ws_handshake_start = asyncio.get_event_loop().time()
                        ws = await websockets.connect(url, extra_headers=headers)
                        _ws_handshake_ms = (
                            asyncio.get_event_loop().time() - _ws_handshake_start
                        ) * 1000.0
                        logger.info(
                            "stt_ws_open call_id=%s attempt=%d handshake_ms=%.0f "
                            "eager=%s eot=%s timeout_ms=%s",
                            call_id, reconnect_count + 1, _ws_handshake_ms,
                            self._eager_eot_threshold, self._eot_threshold,
                            self._eot_timeout_ms,
                            extra={
                                "call_id": call_id,
                                "stt_ws_handshake_ms": round(_ws_handshake_ms),
                                "stt_reconnect_attempt": reconnect_count + 1,
                            },
                        )

                    try:
                        # Send initial silent frame (per Deepgram docs)
                        silent_frame = bytes(3200)  # 100ms of silence
                        await ws.send(silent_frame)

                        # Reset stop_event so receive/send tasks run fresh
                        stop_event.clear()

                        # Start tasks
                        send_task = asyncio.create_task(send_audio(ws))
                        receive_task = asyncio.create_task(receive_transcripts(ws))
                        heartbeat_task = asyncio.create_task(send_silence_heartbeat(ws))

                        # Yield transcripts until stream ends
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    transcript_queue.get(),
                                    timeout=0.01
                                )
                                if chunk is None:
                                    break
                                yield chunk
                            except asyncio.TimeoutError:
                                if stop_event.is_set() and transcript_queue.empty():
                                    break
                                continue

                        # Graceful close
                        try:
                            await ws.send(json.dumps({"type": "CloseStream"}))
                        except Exception:
                            pass

                        # Cancel helper tasks
                        for task in [send_task, receive_task, heartbeat_task]:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(
                            send_task, receive_task, heartbeat_task, return_exceptions=True
                        )
                    finally:
                        try:
                            await ws.close()
                        except Exception:
                            pass

                    # If send_audio finished cleanly (audio_stream exhausted), stop.
                    if stop_reason not in ("running", "stt_stream_closed"):
                        break
                    # Normal completion — done.
                    break

                except websockets.exceptions.ConnectionClosed as e:
                    # Unexpected drop — decide whether to reconnect
                    if stop_event.is_set():
                        break  # Call ended intentionally
                    reconnect_count += 1
                    if stream_stats:
                        stream_stats.stream_reconnect_total += 1
                    if reconnect_count > FLUX_MAX_RECONNECTS:
                        stop_reason = "stt_provider_error"
                        logger.error(
                            f"Flux WS dropped — max reconnects ({FLUX_MAX_RECONNECTS}) reached"
                        )
                        raise
                    delay = min(
                        FLUX_RECONNECT_BASE_DELAY * (2 ** (reconnect_count - 1)),
                        FLUX_RECONNECT_MAX_DELAY,
                    ) * (0.5 + random.random())
                    logger.warning(
                        f"Flux WS dropped (code={e.code}), reconnect "
                        f"{reconnect_count}/{FLUX_MAX_RECONNECTS} in {delay:.2f}s"
                    )
                    stop_event.clear()
                    # Drain stale items from previous connection so the consumer
                    # does not process transcripts from the dropped session.
                    while not transcript_queue.empty():
                        try:
                            transcript_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await asyncio.sleep(delay)

                except Exception as e:
                    if "401" in str(e) or "403" in str(e):
                        stop_reason = "stt_auth_error"
                    elif stop_reason == "running":
                        stop_reason = "stt_provider_error"
                    logger.error(f"Flux connection error: {e}")
                    raise

        finally:
            if stop_reason == "running":
                stop_reason = "stt_stream_closed"
            if stream_stats:
                stream_stats.stop_reason = stop_reason
            # Clean up per-call state to prevent unbounded singleton growth
            if call_id:
                self._eager_states.pop(call_id, None)
                self._stream_stats.pop(call_id, None)
    
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """Detect if user finished speaking (empty final chunk = EndOfTurn)"""
        return transcript_chunk.is_final and not transcript_chunk.text
    
    def should_cancel_speculative(self, call_id: str) -> bool:
        """Check if speculative LLM call should be cancelled (TurnResumed)."""
        if call_id not in self._eager_states:
            return False
        state = self._eager_states[call_id]
        return state.cancel_event is not None and state.cancel_event.is_set()

    def get_stream_stats(self, call_id: str) -> dict:
        stats = self._stream_stats.get(call_id)
        return stats.to_dict() if stats else {}
    
    async def cleanup(self) -> None:
        """Release resources"""
        self._api_key = None
        self._eager_states.clear()
        self._stream_stats.clear()
        for _ws in list(self._pre_connections.values()):
            try:
                await _ws.close()
            except Exception:
                pass
        self._pre_connections.clear()
        logger.info("DeepgramFlux cleaned up")
    
    @property
    def name(self) -> str:
        return "deepgram-flux"
    
    def __repr__(self) -> str:
        return f"DeepgramFluxSTTProvider(model={self._model}, sample_rate={self._sample_rate})"
