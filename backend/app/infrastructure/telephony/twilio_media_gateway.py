"""Twilio Media Streams gateway.

Twilio's Media Streams protocol differs from the browser / Vonage raw-binary
WebSocket: audio is exchanged as JSON **text** frames carrying base64-encoded
G.711 mu-law at 8 kHz mono:

  inbound   {"event":"media","media":{"payload":"<base64 mu-law>"}}
  outbound  {"event":"media","streamSid":"<sid>","media":{"payload":"<base64 mu-law>"}}
  clear     {"event":"clear","streamSid":"<sid>"}     (drop buffered playback)

This subclass reuses ALL of BrowserMediaGateway's buffering / barge-in / metrics
machinery and swaps only the WIRE format. The pipeline runs natively at 8 kHz,
so no resampling is needed — mu-law is a sample-for-sample codec. We override
the send path to wrap linear16 PCM as mu-law JSON frames, and add a helper to
decode inbound mu-law before feeding the normal input path.

See: https://www.twilio.com/docs/voice/media-streams/websocket-messages
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Dict

from app.infrastructure.telephony.browser_media_gateway import (
    BrowserMediaGateway,
    BrowserSession,
)
from app.utils.audio_utils import pcm_to_ulaw, ulaw_to_pcm

logger = logging.getLogger(__name__)


class TwilioMediaGateway(BrowserMediaGateway):
    """BrowserMediaGateway speaking Twilio Media Streams' JSON + mu-law wire format."""

    def __init__(self) -> None:
        super().__init__()
        # Twilio requires the streamSid on every outbound media / clear frame.
        # It arrives in the WS "start" event — after the gateway session is
        # created — so it's stored at gateway level keyed by call_id.
        self._stream_sids: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "twilio"

    def set_stream_sid(self, call_id: str, stream_sid: str) -> None:
        """Bind the Twilio streamSid for a call so outbound frames can be sent."""
        if stream_sid:
            self._stream_sids[call_id] = stream_sid

    async def feed_twilio_media(self, call_id: str, ulaw_bytes: bytes) -> None:
        """Decode an inbound Twilio media payload (mu-law 8 kHz) to linear16 and
        feed it into the normal input path (STT)."""
        if not ulaw_bytes:
            return
        pcm = ulaw_to_pcm(ulaw_bytes)  # 8-bit mu-law -> 16-bit PCM, same 8 kHz rate
        await self.on_audio_received(call_id, pcm)

    async def _send_payload(self, session: BrowserSession, payload: bytes) -> None:
        """Override: encode linear16 8 kHz -> mu-law, wrap in a Twilio media JSON
        frame, and send as WS text. ``payload`` is frame-aligned int16 PCM."""
        stream_sid = self._stream_sids.get(session.call_id)
        if not stream_sid:
            # streamSid not known yet (pre-"start"); drop — TTS hasn't begun.
            return
        try:
            ulaw = pcm_to_ulaw(payload)
        except Exception as exc:  # never let encoding break the call
            logger.debug("twilio mu-law encode failed call=%s: %s", session.call_id, exc)
            return
        frame = json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(ulaw).decode("ascii")},
        })
        started = datetime.utcnow()
        try:
            await asyncio.wait_for(
                session.websocket.send_text(frame),
                timeout=self._ws_send_timeout_ms / 1000.0,
            )
            session.last_send_latency_ms = (datetime.utcnow() - started).total_seconds() * 1000
            session.chunks_sent += 1
            session.total_bytes_sent += len(payload)
            if session.playback_tracking_active:
                session.playback_bytes_sent += len(payload)
        except asyncio.TimeoutError:
            session.ws_send_timeouts += 1
            session.dropped_output_bytes += len(payload)
            logger.warning(
                "Twilio WS send timeout call=%s after %sms; dropped %s bytes",
                session.call_id, self._ws_send_timeout_ms, len(payload),
            )
        except Exception:
            session.ws_send_errors += 1
            raise

    async def clear_output_buffer(self, call_id: str) -> None:
        """Override: on barge-in, drop our local buffer AND tell Twilio to flush
        its own playback buffer immediately via a ``clear`` frame — Twilio's
        native barge-in mechanism, snappier than fading the tail."""
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            return
        session.output_buffer = bytearray()
        session.pending_byte = b""
        session.playback_tracking_active = False
        session.playback_bytes_sent = 0
        session.playback_complete_event.clear()
        stream_sid = self._stream_sids.get(call_id)
        if stream_sid:
            try:
                await asyncio.wait_for(
                    session.websocket.send_text(
                        json.dumps({"event": "clear", "streamSid": stream_sid})
                    ),
                    timeout=self._ws_send_timeout_ms / 1000.0,
                )
            except Exception as exc:
                logger.debug("twilio clear send failed call=%s: %s", call_id, exc)

    async def on_call_ended(self, call_id: str, reason: str) -> None:
        self._stream_sids.pop(call_id, None)
        await super().on_call_ended(call_id, reason)
