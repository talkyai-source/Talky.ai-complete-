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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    is_active: bool = True

    # TTS audio buffer for packetization (must send in 160-byte chunks for 8kHz PCMU)
    tts_buffer: bytes = field(default_factory=bytes)

    # Metrics
    chunks_received: int = 0
    chunks_sent: int = 0
    total_bytes_received: int = 0
    total_bytes_sent: int = 0
    dropped_input_chunks: int = 0


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

        session = TelephonySession(
            call_id=call_id,
            pbx_call_id=pbx_call_id,
            adapter=adapter,
            input_queue=asyncio.Queue(maxsize=200),
        )
        self._sessions[call_id] = session
        logger.info("TelephonyMediaGateway: session started call_id=%s pbx=%s", call_id[:12], pbx_call_id[:12])

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

        # Decode PCMU → linear16
        try:
            from app.utils.audio_utils import ulaw_to_pcm
            pcm_chunk = ulaw_to_pcm(audio_chunk)
        except Exception as exc:
            logger.debug("TelephonyMediaGateway: ulaw_to_pcm failed for %s: %s", call_id[:12], exc)
            return

        session.chunks_received += 1
        session.total_bytes_received += len(audio_chunk)
        session.recording_buffer.append(pcm_chunk)

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
            logger.debug(f"[TelephonyGW] send_audio: empty audio chunk for {call_id[:12]}")
            return

        logger.debug(f"[TelephonyGW] send_audio: received {len(audio_chunk)} bytes for {call_id[:12]}, format={self._tts_source_format}")

        try:
            if self._tts_source_format == "f32le":
                from app.utils.audio_utils import pcm_float32_to_int16, pcm_to_ulaw
                logger.debug(f"[TelephonyGW] Converting Float32 → Int16 → μ-law")
                pcm16 = pcm_float32_to_int16(audio_chunk)
                pcmu = pcm_to_ulaw(pcm16)
            else:
                from app.utils.audio_utils import pcm_to_ulaw
                logger.debug(f"[TelephonyGW] Converting Int16 → μ-law")
                pcmu = pcm_to_ulaw(audio_chunk)
            
            logger.debug(f"[TelephonyGW] Converted to {len(pcmu)} bytes PCMU")
        except Exception as exc:
            logger.warning(f"[TelephonyGW] TTS encode failed for {call_id[:12]}: {exc}", exc_info=True)
            return

        # Buffer the PCMU audio and send in 160-byte packets
        # 160 bytes = 20ms of 8kHz PCMU audio (8000 samples/sec ÷ 1000 × 20ms = 160 bytes)
        session.tts_buffer += pcmu
        
        # Send complete 160-byte packets
        PACKET_SIZE = 160
        packets_sent = 0
        
        while len(session.tts_buffer) >= PACKET_SIZE:
            packet = session.tts_buffer[:PACKET_SIZE]
            session.tts_buffer = session.tts_buffer[PACKET_SIZE:]
            
            try:
                logger.debug(f"[TelephonyGW] Sending 160-byte packet to adapter for pbx_call_id={session.pbx_call_id[:12]}")
                await session.adapter.send_tts_audio(session.pbx_call_id, packet)
                session.chunks_sent += 1
                session.total_bytes_sent += len(packet)
                packets_sent += 1
            except Exception as exc:
                logger.warning(f"[TelephonyGW] send_tts_audio failed for {call_id[:12]}: {exc}")
                # Don't break - try to send remaining packets
        
        if packets_sent > 0:
            logger.info(f"[TelephonyGW] ✅ Sent {packets_sent} packets ({packets_sent * 160} bytes) to adapter (buffered: {len(session.tts_buffer)} bytes)")
        else:
            logger.debug(f"[TelephonyGW] Buffering {len(pcmu)} bytes (total buffered: {len(session.tts_buffer)} bytes)")

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

    # ------------------------------------------------------------------
    # Recording buffer (required by MediaGateway interface)
    # ------------------------------------------------------------------

    def get_recording_buffer(self, call_id: str):
        session = self._sessions.get(call_id)
        return session.recording_buffer if session else None

    def clear_recording_buffer(self, call_id: str) -> None:
        session = self._sessions.get(call_id)
        if session:
            session.recording_buffer.clear()
