"""
Telephony Media Gateway

Implements the MediaGateway interface for SIP/RTP telephony paths that use an
HTTP callback model instead of a persistent WebSocket (i.e. the Asterisk + C++
Voice Gateway path).

Sample-rate strategy (post 8kHz -> 16kHz migration):
  - The C++ Voice Gateway is fixed at PCMU 8kHz on the wire (160-byte / 20ms
    packets). That stays.
  - Internally we run at 16kHz linear16 so Deepgram Flux receives audio at its
    native feature-extraction rate. We upsample 8 -> 16 on ingress and
    downsample 16 -> 8 on egress. soxr_hq is used for both (band-limited sinc).

Audio flow (inbound — caller → STT):
  C++ Gateway  →  POST /api/v1/sip/telephony/audio/{session_id}   (PCMU 8kHz)
               →  telephony_bridge.receive_gateway_audio()
               →  TelephonyMediaGateway.on_audio_received()
               →  ulaw_to_pcm()            (G.711 µ-law -> linear16 8kHz)
               →  resample_audio() 8 -> 16 (linear16 16kHz for Flux)
               →  input_queue              (consumed by VoicePipelineService)

Audio flow (outbound — TTS → caller):
  VoicePipelineService.synthesize_and_send_audio()
               →  TelephonyMediaGateway.send_audio()                (linear16 16kHz)
               →  pcm_float32_to_int16()   (if TTS source is Float32)
               →  resample_audio() 16 -> 8 (linear16 8kHz)
               →  pcm_to_ulaw()            (linear16 -> G.711 µ-law)
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

    # TTS audio buffer for packetization (must send in 160-byte chunks for 8kHz PCMU
    # over the wire — that is the C++ gateway contract and stays fixed even after
    # the internal 16kHz migration; we downsample to 8kHz before this buffer).
    tts_buffer: bytes = field(default_factory=bytes)

    # STT input accumulation buffer — kept for safety only.
    # Post bug #4 fix the C++ gateway batches 40ms per callback, which after
    # upsample to 16kHz is 1280 bytes — exactly Flux's optimal chunk size.
    # Re-batching here only adds latency, so the threshold below is set to one
    # frame's worth and audio passes straight through. Field is retained so
    # session telemetry / future tweaks have a place to land.
    _stt_accumulator: bytes = field(default_factory=bytes)

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

    # Rate-limit timestamp for the queue-overrun warning (one log/sec/call).
    last_queue_drop_warn_at: float = 0.0


class TelephonyMediaGateway(MediaGateway):
    """
    Media gateway for SIP telephony sessions that deliver audio via HTTP
    callbacks from the C++ Voice Gateway (Asterisk path).

    Wire format with the C++ gateway (fixed):
        G.711 µ-law, 8 kHz, mono (PCMU). 160-byte / 20ms packets.

    Internal format (post 8kHz -> 16kHz migration):
        linear16, 16 kHz, mono. We upsample on ingress (after µ-law decode)
        and downsample on egress (before µ-law encode) so Deepgram Flux always
        sees its native rate.
    """

    # PCMU wire rate to / from the C++ Voice Gateway. Cannot change without
    # rebuilding the C++ binary, so stays at 8 kHz.
    _WIRE_SAMPLE_RATE: int = 8000

    def __init__(self) -> None:
        self._sessions: Dict[str, TelephonySession] = {}
        # Internal rate fed to STT and produced by TTS. Defaults to 16 kHz so
        # Flux receives audio at its native feature-extraction rate.
        self._sample_rate: int = 16000
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
        sample_rate (int): Internal rate fed to STT / produced by TTS.
                           Default 16000. PCMU wire rate to the C++ gateway is
                           always 8000 regardless of this value — we resample
                           on the boundary.
        channels    (int): Must be 1 for PCMU (default 1).
        bit_depth   (int): Must be 16 for linear16 (default 16).
        tts_source_format (str): "s16le" or "f32le" (default "s16le").
        """
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._channels = int(config.get("channels", 1))
        self._bit_depth = int(config.get("bit_depth", 16))
        raw_fmt = str(config.get("tts_source_format", "s16le")).lower()
        self._tts_source_format = raw_fmt if raw_fmt in ("s16le", "f32le") else "s16le"

        logger.info(
            "TelephonyMediaGateway initialized: internal=%dHz, wire=%dHz PCMU, "
            "%d-bit, tts_source_format=%s",
            self._sample_rate,
            self._WIRE_SAMPLE_RATE,
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
            "TelephonyMediaGateway: session started call_id=%s pbx=%s "
            "wire=pcmu/%dHz internal=linear16/%dHz (upsample on ingress, downsample on egress)",
            call_id[:12], pbx_call_id[:12], self._WIRE_SAMPLE_RATE, self._sample_rate,
            extra={
                "call_id": call_id,
                "codec": "pcmu",
                "wire_sample_rate": self._WIRE_SAMPLE_RATE,
                "internal_sample_rate": self._sample_rate,
            },
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
        it as 16 kHz linear16 PCM for the STT pipeline.

        Wire: G.711 µ-law, 8 kHz, 8-bit (the C++ gateway contract).
        We decode to 8 kHz linear16, then upsample to 16 kHz so Deepgram Flux
        receives audio at its native feature-extraction rate. soxr_hq is used
        for the resample (band-limited sinc).
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

        # Decode PCMU 8kHz -> linear16 8kHz, then upsample to internal rate.
        # Hot path: use soxr_mq (medium-quality) — ~3x faster than soxr_hq with
        # no perceptual difference on phone-bandwidth audio (frequencies above
        # ~3.4 kHz are empty on G.711-fed calls anyway).
        try:
            from app.utils.audio_utils import ulaw_to_pcm, resample_audio
            pcm_chunk_8k = ulaw_to_pcm(audio_chunk)
            if self._sample_rate != self._WIRE_SAMPLE_RATE:
                pcm_chunk = resample_audio(
                    pcm_chunk_8k,
                    from_rate=self._WIRE_SAMPLE_RATE,
                    to_rate=self._sample_rate,
                    channels=self._channels,
                    bit_depth=self._bit_depth,
                    res_type="soxr_mq",
                )
            else:
                pcm_chunk = pcm_chunk_8k
        except Exception as exc:
            logger.debug(
                "TelephonyMediaGateway: ingress decode/resample failed for %s: %s",
                call_id[:12], exc,
            )
            return

        session.chunks_received += 1
        session.total_bytes_received += len(audio_chunk)

        # Recording buffer at internal rate with memory cap.
        # 16 kHz / 16-bit mono: 60 min ≈ 115.2 MB. 8 kHz path stays at 57.6 MB.
        _MAX_RECORDING_BYTES = (self._sample_rate * 2) * 60 * 60  # 60 min
        session.recording_buffer.append(pcm_chunk)
        session.recording_buffer_bytes += len(pcm_chunk)
        while session.recording_buffer_bytes > _MAX_RECORDING_BYTES and session.recording_buffer:
            evicted = session.recording_buffer.pop(0)
            session.recording_buffer_bytes -= len(evicted)

        # Track how many PCM16 samples the caller side has produced (at internal rate)
        session.caller_sample_count += len(pcm_chunk) // 2

        # Forward straight to STT — no re-batching.
        # Post bug #4 the C++ gateway batches at 40ms, which after upsample is
        # exactly Flux's optimal 1,280-byte chunk. Re-batching to 100ms here
        # would only add 60ms of latency for no quality benefit.
        batch = pcm_chunk

        try:
            session.input_queue.put_nowait(batch)
        except asyncio.QueueFull:
            # Drop oldest super-frame and make room (keeps latency low)
            try:
                session.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                session.input_queue.put_nowait(batch)
            except asyncio.QueueFull:
                session.dropped_input_chunks += 1
                # Rate-limited warning so backpressure is visible in production
                # logs instead of being lost in a silent counter. One per call
                # per second is enough to alert without flooding.
                if (now - session.last_queue_drop_warn_at) > 1.0:
                    session.last_queue_drop_warn_at = now
                    logger.warning(
                        "stt_input_queue_overrun call_id=%s dropped_total=%d — "
                        "STT pipeline is not draining audio fast enough; check "
                        "Deepgram WS health or per-frame resample CPU",
                        call_id, session.dropped_input_chunks,
                        extra={
                            "call_id": call_id,
                            "dropped_input_chunks": session.dropped_input_chunks,
                            "alert": "stt_input_queue_overrun",
                        },
                    )

    # ------------------------------------------------------------------
    # Outbound audio (TTS → caller)
    # ------------------------------------------------------------------

    async def send_audio(self, call_id: str, audio_chunk: bytes) -> None:
        """
        Convert TTS output to PCMU and deliver it to the caller via the
        CallControlAdapter (which forwards it to the C++ gateway).

        Handles two TTS source formats at the internal sample rate (16 kHz):
        - "f32le": Float32 PCM (Google, Cartesia) -> Int16 -> downsample 16->8 -> µ-law
        - "s16le": Int16 PCM  (Deepgram)          -> downsample 16->8 -> µ-law

        IMPORTANT: The C++ gateway requires audio in 160-byte packets (20ms @ 8kHz
        PCMU). The wire stays 8 kHz regardless of the internal rate; we resample
        to wire rate just before µ-law encoding.
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            logger.warning(f"[TelephonyGW] send_audio: no active session for {call_id[:12]}")
            return

        if not audio_chunk:
            return

        try:
            loop = asyncio.get_running_loop()
            from app.utils.audio_utils import (
                pcm_float32_to_int16,
                pcm_to_ulaw,
                resample_audio,
            )
            if self._tts_source_format == "f32le":
                pcm16 = pcm_float32_to_int16(audio_chunk)
            else:
                pcm16 = audio_chunk

            # Downsample internal rate -> 8 kHz wire rate before µ-law encode.
            # soxr_mq for the same reason as ingress: phone bandwidth caps the
            # perceptual difference vs soxr_hq, and TTS bursts are frequent.
            if self._sample_rate != self._WIRE_SAMPLE_RATE:
                pcm16_wire = resample_audio(
                    pcm16,
                    from_rate=self._sample_rate,
                    to_rate=self._WIRE_SAMPLE_RATE,
                    channels=self._channels,
                    bit_depth=self._bit_depth,
                    res_type="soxr_mq",
                )
            else:
                pcm16_wire = pcm16
            pcmu = pcm_to_ulaw(pcm16_wire)

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

        # Buffer the PCMU audio and send in real-time-paced bursts to the C++ gateway.
        #
        # WITHOUT pacing: TTS generates a 7-second utterance in ~0.5s and sends all
        # 350 packets immediately.  The gateway buffers them and plays at real-time.
        # When barge-in fires, interrupt_tts must clear 7s of buffered audio — the
        # caller hears the agent speaking for seconds after interrupting.
        #
        # WITH pacing: asyncio.sleep() yields the event loop so STT barge-in callbacks
        # can fire between bursts.  The C++ gateway never accumulates more than
        # TARGET_AHEAD_S seconds of pre-buffered audio.  When barge-in fires:
        #   1. synthesize_and_send_audio detects the event and stops sending.
        #   2. clear_output_buffer clears the Python buffer and calls interrupt_tts.
        #   3. Only ≤ TARGET_AHEAD_S of audio is left in the gateway — at most ~200ms.
        #
        # BATCHING (3 packets = 60ms per sleep): the original 1-packet-per-sleep design
        # called asyncio.sleep() ~300 times for a 6-second utterance.  Under event loop
        # load (STT RTP ingest + Groq streaming), each sleep can drift 10-50ms beyond
        # its requested duration.  300 drift events accumulate into audible gaps.
        # Batching 3 packets per sleep reduces yields to ~100 — 3× less drift surface —
        # while keeping barge-in detection latency within one 60ms burst window.
        #
        # 160 bytes = 20ms of audio at 8kHz PCMU on the wire (8000 samples/s
        # × 0.020s × 1 byte). The wire rate is fixed by the C++ gateway
        # contract; the internal rate has no effect on packet sizing.
        # Opportunistic batching: when multiple packets are already buffered we
        # POST them together, cutting HTTP round-trips on the steady-state path
        # (50/s -> ~25/s at the default batch size of 2).  The batch size is
        # clamped so worst-case barge-in detection stays within PACKET_DURATION
        # x TTS_BATCH_PACKETS = 40 ms (default).  The first packet after
        # silence is still sent as soon as it is available.  The C++ gateway's
        # enqueue_tts_ulaw accepts any multiple of 160 bytes.
        PACKET_SIZE = 160
        PACKET_DURATION_S = PACKET_SIZE / self._WIRE_SAMPLE_RATE   # 0.020 s at 8 kHz
        TARGET_AHEAD_S = 0.200                                     # keep <= 200 ms ahead
        try:
            MAX_BATCH_PACKETS = max(
                1, int(os.getenv("TELEPHONY_TTS_BATCH_PACKETS", "2"))
            )
        except ValueError:
            MAX_BATCH_PACKETS = 2

        session.tts_buffer += pcmu

        while len(session.tts_buffer) >= PACKET_SIZE:
            # Barge-in check at the top of every burst: exit immediately if the
            # pipeline's event fired while we were sleeping or processing the last burst.
            if session.barge_in_event and session.barge_in_event.is_set():
                session.tts_buffer = b""
                return

            # Opportunistic batch: take up to MAX_BATCH_PACKETS worth of bytes
            # that are already buffered; never wait for more to accumulate.
            available_packets = len(session.tts_buffer) // PACKET_SIZE
            batch_packets = min(available_packets, MAX_BATCH_PACKETS)
            send_size = batch_packets * PACKET_SIZE

            packet = session.tts_buffer[:send_size]
            session.tts_buffer = session.tts_buffer[send_size:]

            now = loop.time()
            deadline = session._tts_send_deadline

            # Initialise (or re-initialise after a silence gap) the pacing cursor.
            # After a gap the deadline is in the past — reset to now so the first
            # burst sends immediately (fills the initial TARGET_AHEAD buffer) before
            # pacing kicks in.
            if deadline is None or deadline < now:
                deadline = now

            # How far ahead will the gateway be after receiving this batch?
            next_deadline = deadline + PACKET_DURATION_S * batch_packets
            overshoot = next_deadline - now - TARGET_AHEAD_S
            if overshoot > 0.001:
                # Wait for either the pacing window to elapse OR a barge-in
                # event to fire. wait_for() returns early on event.set(), so
                # we exit within microseconds of Flux signalling StartOfTurn
                # instead of waiting up to ~40 ms for the next loop iteration.
                if session.barge_in_event is not None:
                    try:
                        await asyncio.wait_for(
                            session.barge_in_event.wait(),
                            timeout=overshoot,
                        )
                        # Event fired during the wait — bail out immediately.
                        session.tts_buffer = b""
                        return
                    except asyncio.TimeoutError:
                        # Pacing window elapsed naturally; continue sending.
                        pass
                else:
                    # No barge-in event registered (rare); fall back to plain sleep.
                    await asyncio.sleep(overshoot)

            # Post-sleep barge-in check: the event may have fired between the
            # sleep returning and the next iteration starting.
            if session.barge_in_event and session.barge_in_event.is_set():
                session.tts_buffer = b""
                return

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
