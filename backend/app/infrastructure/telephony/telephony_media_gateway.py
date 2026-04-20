"""
Telephony Media Gateway

Implements the MediaGateway interface for SIP/RTP telephony paths that use an
HTTP callback model instead of a persistent WebSocket (i.e. the Asterisk + C++
Voice Gateway path).

Audio flow (inbound — caller → STT):
  C++ Gateway  →  POST /api/v1/sip/telephony/audio/{session_id}
               →  telephony_bridge.receive_gateway_audio()
               →  TelephonyMediaGateway.on_audio_received()
               →  ulaw_to_pcm()            (G.711 μ-law → linear16)
               →  input_queue              (consumed by VoicePipelineService)

Audio flow (outbound — TTS → caller):
  VoicePipelineService.synthesize_and_send_audio()
               →  TelephonyMediaGateway.send_audio()
               →  pcm_float32_to_int16()   (if TTS source is Float32)
               →  pcm_to_ulaw()            (linear16 → G.711 μ-law)
               →  adapter.send_tts_audio() (CallControlAdapter → C++ Gateway)

The class intentionally mirrors the session management pattern of
BrowserMediaGateway so both gateways are interchangeable from the pipeline's
perspective.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.domain.interfaces.media_gateway import MediaGateway

logger = logging.getLogger(__name__)


@dataclass
class TelephonySession:
    """Per-call state for a telephony HTTP-callback session."""

    call_id: str
    pbx_call_id: str
    # adapter is typed as Any to avoid a circular import with CallControlAdapter.
    # It is expected to implement send_tts_audio(pbx_call_id, pcmu_bytes).
    adapter: Any
    created_at: datetime = field(default_factory=datetime.utcnow)
    input_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=200)
    )
    recording_buffer: List[bytes] = field(default_factory=list)
    recording_buffer_bytes: int = 0
    # TTS recording: list of (sample_offset, pcm_bytes) for timeline placement.
    # sample_offset is a running write cursor (MixMonitor-style), NOT a
    # wall-clock timestamp.  See send_audio() for the cursor logic.
    tts_recording_buffer: List[Tuple[int, bytes]] = field(default_factory=list)
    is_active: bool = True

    # Monotonic start time (seconds) — set in on_call_started()
    recording_start_time: float = 0.0
    # Running sample counter for caller audio (incremented on each received chunk)
    caller_sample_count: int = 0
    # Running write cursor for agent audio recording (MixMonitor pattern).
    # Advances by chunk duration after each TTS chunk.  Jumps forward to
    # wall-clock position when a new utterance starts after a silence gap.
    agent_rec_cursor: int = 0

    # TTS audio buffer for packetization (must send in 160-byte chunks for 8kHz PCMU)
    tts_buffer: bytes = field(default_factory=bytes)

    # Real-time pacing cursor for TTS delivery (see send_audio).
    # Tracks the monotonic clock position up to which audio has been scheduled
    # to play at the C++ gateway.  None = start of a new burst.
    _tts_send_deadline: Optional[float] = field(default=None)

    # Optional barge-in event: when set, send_audio exits the pacing loop early
    # so synthesize_and_send_audio detects the interruption without waiting for
    # the current TTS chunk to fully drain.
    barge_in_event: Optional[asyncio.Event] = field(default=None)

    # Metrics
    chunks_received: int = 0
    chunks_sent: int = 0
    total_bytes_received: int = 0
    total_bytes_sent: int = 0
    dropped_input_chunks: int = 0

    # One-shot flag: emits t_tts_first_audio once per call for baseline
    # first-turn latency measurement.
    first_tts_logged: bool = False

    # Gap detection — tracks C++ gateway fire-and-forget callback drops
    last_audio_received_at: float = 0.0
    audio_gap_count: int = 0


class TelephonyMediaGateway(MediaGateway):
    """
    Media gateway for SIP telephony sessions that deliver audio via HTTP
    callbacks from the C++ Voice Gateway (Asterisk path).

    Audio format (inbound):
        G.711 μ-law, 8 kHz, mono (PCMU) — decoded to linear16 for STT.

    Audio format (outbound):
        linear16 or Float32 from TTS — encoded to G.711 μ-law for the gateway.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, TelephonySession] = {}
        self._sample_rate: int = 8000
        self._channels: int = 1
        self._bit_depth: int = 16
        # "s16le" (Deepgram linear16 TTS) or "f32le" (Google / Cartesia TTS)
        self._tts_source_format: str = "s16le"

    # ------------------------------------------------------------------
    # MediaGateway interface — identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "telephony"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Configure the gateway.

        Config keys
        -----------
        sample_rate (int): Must be 8000 for PCMU (default 8000).
        channels    (int): Must be 1 for PCMU (default 1).
        bit_depth   (int): Must be 16 for linear16 (default 16).
        tts_source_format (str): "s16le" or "f32le" (default "s16le").
        """
        self._sample_rate = int(config.get("sample_rate", 8000))
        self._channels = int(config.get("channels", 1))
        self._bit_depth = int(config.get("bit_depth", 16))
        raw_fmt = str(config.get("tts_source_format", "s16le")).lower()
        self._tts_source_format = raw_fmt if raw_fmt in ("s16le", "f32le") else "s16le"

        logger.info(
            "TelephonyMediaGateway initialized: %dHz, %d-bit, tts_source_format=%s",
            self._sample_rate,
            self._bit_depth,
            self._tts_source_format,
        )

    async def on_call_started(self, call_id: str, metadata: Dict[str, Any]) -> None:
        """
        Register a new telephony session.

        Expected metadata keys
        ----------------------
        adapter    : CallControlAdapter instance (for TTS output).
        pbx_call_id: The PBX channel/call UUID (used when calling send_tts_audio).
        """
        adapter = metadata.get("adapter")
        pbx_call_id = metadata.get("pbx_call_id", call_id)

        if adapter is None:
            raise ValueError(
                "TelephonyMediaGateway.on_call_started: 'adapter' key is required "
                "in metadata (must be a CallControlAdapter instance)."
            )

        loop = asyncio.get_running_loop()
        now = loop.time()
        session = TelephonySession(
            call_id=call_id,
            pbx_call_id=pbx_call_id,
            adapter=adapter,
            input_queue=asyncio.Queue(maxsize=200),
            recording_start_time=now,
            last_audio_received_at=now,
        )
        self._sessions[call_id] = session
        logger.info(
            "TelephonyMediaGateway: session started call_id=%s pbx=%s codec=pcmu sample_rate=8000 — "
            "8kHz G.711 degrades STT accuracy vs 16kHz wideband (Deepgram trained on 16kHz)",
            call_id[:12], pbx_call_id[:12],
            extra={"call_id": call_id, "codec": "pcmu", "sample_rate": 8000},
        )

    async def on_call_ended(self, call_id: str, reason: str = "hangup") -> None:
        """Mark session inactive and remove it from the registry."""
        session = self._sessions.get(call_id)
        if session:
            session.is_active = False
            self._sessions.pop(call_id, None)
            logger.info(
                "TelephonyMediaGateway: session ended call_id=%s reason=%s",
                call_id[:12],
                reason,
            )

    async def cleanup(self) -> None:
        """End all active sessions."""
        for call_id in list(self._sessions.keys()):
            await self.on_call_ended(call_id, "gateway_cleanup")

    # ------------------------------------------------------------------
    # Inbound audio (caller → STT)
    # ------------------------------------------------------------------

    async def on_audio_received(self, call_id: str, audio_chunk: bytes) -> None:
        """
        Accept a PCMU audio chunk from the C++ gateway callback and enqueue
        it as linear16 PCM for the STT pipeline.

        The C++ gateway delivers raw G.711 μ-law bytes (8-bit, 8 kHz).
        We decode to 16-bit linear PCM here so the rest of the pipeline
        sees the same format as browser sessions.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return

        if not audio_chunk:
            return

        # Gap detection — fire-and-forget C++ gateway callbacks can be silently dropped
        loop = asyncio.get_running_loop()
        now = loop.time()
        gap_ms = (now - session.last_audio_received_at) * 1000
        session.last_audio_received_at = now
        # Expected batch interval is 80ms (4 frames × 20ms). Flag anything >150ms.
        if session.chunks_received > 0 and gap_ms > 150:
            session.audio_gap_count += 1
            logger.warning(
                "telephony_audio_gap call_id=%s gap_ms=%.0f total_gaps=%d — "
                "C++ gateway may have dropped a callback (200ms fire-and-forget timeout)",
                call_id, gap_ms, session.audio_gap_count,
                extra={"call_id": call_id, "gap_ms": round(gap_ms), "audio_gap_count": session.audio_gap_count},
            )

        # Decode PCMU → linear16
        try:
            from app.utils.audio_utils import ulaw_to_pcm
            pcm_chunk = ulaw_to_pcm(audio_chunk)
        except Exception as exc:
            logger.debug("TelephonyMediaGateway: ulaw_to_pcm failed for %s: %s", call_id[:12], exc)
            return

        session.chunks_received += 1
        session.total_bytes_received += len(audio_chunk)

        # Recording buffer with memory cap.
        # 8 kHz / 16-bit mono: 60 min ≈ 57.6 MB.
        _MAX_RECORDING_BYTES = 57_600_000
        session.recording_buffer.append(pcm_chunk)
        session.recording_buffer_bytes += len(pcm_chunk)
        while session.recording_buffer_bytes > _MAX_RECORDING_BYTES and session.recording_buffer:
            evicted = session.recording_buffer.pop(0)
            session.recording_buffer_bytes -= len(evicted)

        # Track how many PCM16 samples the caller side has produced
        session.caller_sample_count += len(pcm_chunk) // 2

        try:
            session.input_queue.put_nowait(pcm_chunk)
        except asyncio.QueueFull:
            # Drop oldest frame and make room (keeps latency low)
            try:
                session.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                session.input_queue.put_nowait(pcm_chunk)
            except asyncio.QueueFull:
                session.dropped_input_chunks += 1

    # ------------------------------------------------------------------
    # Outbound audio (TTS → caller)
    # ------------------------------------------------------------------

    async def send_audio(self, call_id: str, audio_chunk: bytes) -> None:
        """
        Convert TTS output to PCMU and deliver it to the caller via the
        CallControlAdapter (which forwards it to the C++ gateway).

        Handles two TTS source formats:
        - "f32le": Float32 PCM (Google, Cartesia) → Int16 → μ-law
        - "s16le": Int16 PCM  (Deepgram)          → μ-law directly
        
        IMPORTANT: The C++ gateway requires audio in 160-byte packets (20ms @ 8kHz).
        This method buffers incoming TTS audio and sends it in proper 160-byte chunks.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            logger.warning(f"[TelephonyGW] send_audio: no active session for {call_id[:12]}")
            return

        if not audio_chunk:
            return

        try:
            loop = asyncio.get_running_loop()
            if self._tts_source_format == "f32le":
                from app.utils.audio_utils import pcm_float32_to_int16, pcm_to_ulaw
                pcm16 = pcm_float32_to_int16(audio_chunk)
                pcmu = pcm_to_ulaw(pcm16)
            else:
                from app.utils.audio_utils import pcm_to_ulaw
                pcm16 = audio_chunk
                pcmu = pcm_to_ulaw(audio_chunk)

            # -- Agent recording (MixMonitor-style running cursor) --
            #
            # TTS delivers audio in rapid bursts: a 3-second utterance might
            # arrive as 15 chunks within 0.5 seconds of wall-clock time.
            # If we stamped each chunk with wall-clock or caller_sample_count,
            # they'd all land at nearly the same offset and overlap.
            #
            # Correct approach (how Asterisk MixMonitor works):
            #   1. Compute where we are on the real-time timeline (wall clock).
            #   2. If the cursor is behind the wall clock, a silence gap occurred
            #      — jump the cursor forward (silence is implicitly inserted by
            #      the mixer seeing a gap between the end of the previous chunk
            #      and the start of this one).
            #   3. Write the chunk at the cursor position.
            #   4. Advance the cursor by the chunk's sample count so the NEXT
            #      chunk of the same burst is placed contiguously after this one.
            chunk_samples = len(pcm16) // 2
            wall_pos = int(
                (loop.time() - session.recording_start_time)
                * self._sample_rate
            )
            if wall_pos > session.agent_rec_cursor:
                session.agent_rec_cursor = wall_pos

            session.tts_recording_buffer.append(
                (session.agent_rec_cursor, pcm16)
            )
            session.agent_rec_cursor += chunk_samples
            
        except Exception as exc:
            logger.warning(f"[TelephonyGW] TTS encode failed for {call_id[:12]}: {exc}", exc_info=True)
            return

        # Buffer the PCMU audio and send in 160-byte packets with real-time pacing.
        #
        # WITHOUT pacing: TTS generates a 7-second utterance in ~0.5s and sends all
        # 350 packets to the C++ gateway immediately.  The gateway buffers them all
        # and plays at real-time rate.  When barge-in fires, interrupt_tts must clear
        # 7s of buffered audio — if the endpoint is slow or absent, the caller hears
        # the agent speaking for seconds after interrupting.
        #
        # WITH pacing: asyncio.sleep() yields the event loop so STT barge-in callbacks
        # can fire between packets.  The C++ gateway never accumulates more than
        # TARGET_AHEAD_S seconds of pre-buffered audio.  When barge-in fires:
        #   1. synthesize_and_send_audio detects the event and stops sending.
        #   2. clear_output_buffer clears the Python buffer and calls interrupt_tts.
        #   3. Only ≤ TARGET_AHEAD_S of audio is left in the gateway — at most
        #      ~200ms instead of 7s.
        #
        # 160 bytes = 20ms of audio at 8kHz PCMU (8000 samples/s × 0.020s × 1 byte).
        # Opportunistic batching: when multiple packets are already buffered we
        # POST them together, cutting HTTP round-trips on the steady-state path
        # (50/s → ~25/s at the default batch size of 2).  The batch size is
        # clamped so worst-case barge-in detection stays within PACKET_DURATION
        # × TTS_BATCH_PACKETS = 40 ms (default).  The first packet after
        # silence is still sent as soon as it is available — TTFB of the first
        # TTS byte is unchanged from the pre-batching behaviour.  The C++
        # gateway's enqueue_tts_ulaw accepts any multiple of 160 bytes.
        PACKET_SIZE = 160
        PACKET_DURATION_S = PACKET_SIZE / self._sample_rate   # 0.020 s at 8 kHz
        TARGET_AHEAD_S    = 0.200                              # keep ≤ 200 ms ahead
        try:
            MAX_BATCH_PACKETS = max(
                1, int(os.getenv("TELEPHONY_TTS_BATCH_PACKETS", "2"))
            )
        except ValueError:
            MAX_BATCH_PACKETS = 2

        session.tts_buffer += pcmu

        while len(session.tts_buffer) >= PACKET_SIZE:
            # Barge-in check at the top of the loop: if the pipeline's event is
            # already set before we even sleep, discard remaining buffer and return
            # so synthesize_and_send_audio can react immediately.
            if session.barge_in_event and session.barge_in_event.is_set():
                session.tts_buffer = b""
                return

            # Opportunistic batch: take up to MAX_BATCH_PACKETS worth of bytes
            # that are ALREADY buffered; never wait for more to accumulate.
            available_packets = len(session.tts_buffer) // PACKET_SIZE
            batch_packets = min(available_packets, MAX_BATCH_PACKETS)
            send_size = batch_packets * PACKET_SIZE

            packet = session.tts_buffer[:send_size]
            session.tts_buffer = session.tts_buffer[send_size:]

            now = loop.time()
            deadline = session._tts_send_deadline

            # Initialise (or re-initialise after a silence gap) the pacing cursor.
            # After a gap the deadline is in the past, so treat it as "now" to allow
            # the first few packets to send immediately (fill the initial TARGET_AHEAD
            # buffer) before pacing kicks in.
            if deadline is None or deadline < now:
                deadline = now

            # How far ahead will the gateway be after receiving this batch?
            next_deadline = deadline + PACKET_DURATION_S * batch_packets
            overshoot = next_deadline - now - TARGET_AHEAD_S
            if overshoot > 0.001:
                # Yield the event loop while waiting.  STT barge-in callbacks fire
                # here, so the barge-in check at the top of the next iteration
                # catches the event within (batch_packets × 20 ms).
                await asyncio.sleep(overshoot)

            try:
                await session.adapter.send_tts_audio(session.pbx_call_id, packet)
                session.chunks_sent += 1
                session.total_bytes_sent += len(packet)
                if not session.first_tts_logged:
                    session.first_tts_logged = True
                    logger.info(
                        "t_tts_first_audio call_id=%s bytes=%d",
                        call_id, len(packet),
                        extra={"call_id": call_id, "t_tts_first_audio": 1},
                    )
            except Exception as exc:
                logger.warning(f"[TelephonyGW] send_tts_audio failed for {call_id[:12]}: {exc}")

            session._tts_send_deadline = next_deadline

    # ------------------------------------------------------------------
    # Pipeline interface helpers
    # ------------------------------------------------------------------

    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """Return the inbound PCM audio queue for the STT pipeline."""
        session = self._sessions.get(call_id)
        return session.input_queue if session else None

    def is_session_active(self, call_id: str) -> bool:
        """True if the session exists and is still active."""
        session = self._sessions.get(call_id)
        return bool(session and session.is_active)

    async def flush_tts_buffer(self, call_id: str) -> None:
        """
        Flush any remaining buffered TTS audio at the end of synthesis.
        
        Pads the final packet to 160 bytes with silence if needed.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return
        
        if len(session.tts_buffer) > 0:
            # Pad to 160 bytes with silence (0x7F is μ-law silence)
            PACKET_SIZE = 160
            padding_needed = PACKET_SIZE - len(session.tts_buffer)
            final_packet = session.tts_buffer + (b'\x7F' * padding_needed)
            
            try:
                logger.info(f"[TelephonyGW] Flushing final {len(session.tts_buffer)} bytes (padded to 160) for {call_id[:12]}")
                await session.adapter.send_tts_audio(session.pbx_call_id, final_packet)
                session.chunks_sent += 1
                session.total_bytes_sent += len(final_packet)
                session.tts_buffer = b""
            except Exception as exc:
                logger.warning(f"[TelephonyGW] flush_tts_buffer failed for {call_id[:12]}: {exc}")

    def set_barge_in_event(self, call_id: str, event: asyncio.Event) -> None:
        """
        Register the pipeline's barge-in event for a session.

        When the event is set, send_audio's pacing loop exits early so
        synthesize_and_send_audio can react within one 20ms packet window
        instead of waiting for the full TTS chunk to drain.
        """
        session = self._sessions.get(call_id)
        if session:
            session.barge_in_event = event

    async def clear_output_buffer(self, call_id: str) -> None:
        """
        Drop buffered telephony output immediately after barge-in.

        Clears both:
        1. Our local packetisation buffer (tts_buffer) — stops further packets
           being sent to the C++ gateway.
        2. The C++ gateway's internal audio queue — stops audio the gateway has
           already buffered from continuing to play at the caller's ear.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return

        # Send a single silent PCMU packet (160 bytes × 0x7F) before discarding
        # the buffer.  µ-law 0x7F encodes to zero PCM amplitude, so this gives the
        # C++ gateway one 20ms frame of silence to land on before interrupt_tts
        # flushes its queue.  It is not a true ramp (PCMU is non-linear), but it
        # prevents the gateway from cutting a non-zero waveform dead — the most
        # audible part of the click/pop artifact.
        PCMU_SILENCE = b"\x7f" * 160
        if session.tts_buffer:
            try:
                await session.adapter.send_tts_audio(session.pbx_call_id, PCMU_SILENCE)
            except Exception:
                pass

        session.tts_buffer = b""
        # Reset real-time pacing cursor so the next utterance gets a fresh burst window.
        session._tts_send_deadline = None
        # Tell the C++ gateway to discard its buffered TTS queue immediately.
        # Without this, audio already sent to the gateway continues to play
        # until its internal buffer drains — the caller hears the AI speaking
        # for 0.5–2s after barge-in has fired.
        if hasattr(session.adapter, "interrupt_tts"):
            try:
                await session.adapter.interrupt_tts(session.pbx_call_id)
            except Exception as exc:
                logger.debug("clear_output_buffer: interrupt_tts failed: %s", exc)

    # ------------------------------------------------------------------
    # Recording buffer (required by MediaGateway interface)
    # ------------------------------------------------------------------

    def get_recording_buffer(self, call_id: str):
        session = self._sessions.get(call_id)
        return session.recording_buffer if session else None

    def get_tts_recording_buffer(self, call_id: str):
        """Return the TTS (agent side) recording buffer."""
        session = self._sessions.get(call_id)
        return session.tts_recording_buffer if session else None

    def clear_recording_buffer(self, call_id: str) -> None:
        session = self._sessions.get(call_id)
        if session:
            session.recording_buffer.clear()
            session.tts_recording_buffer.clear()
