"""Tests for the Twilio Media Streams bridge + gateway (mu-law JSON framing)."""
from __future__ import annotations

import base64
import json

import numpy as np
import pytest

import app.api.v1.endpoints.twilio_bridge as tb
from app.domain.models.voice_contract import VoiceCallState, map_twilio_status
from app.infrastructure.telephony.factory import MediaGatewayFactory
from app.infrastructure.telephony.twilio_media_gateway import TwilioMediaGateway
from app.utils.audio_utils import pcm_to_ulaw


class _FakeWS:
    """Captures frames sent by the gateway."""

    def __init__(self):
        self.text_frames: list[str] = []
        self.byte_frames: list[bytes] = []

    async def send_text(self, msg: str) -> None:
        self.text_frames.append(msg)

    async def send_bytes(self, b: bytes) -> None:
        self.byte_frames.append(b)


def _tone(n_samples: int, amp: int = 10000) -> bytes:
    t = np.linspace(0, 2 * np.pi * 4, n_samples, endpoint=False)
    return (np.sin(t) * amp).astype(np.int16).tobytes()


async def _make_gateway(call_id: str, ws: _FakeWS) -> TwilioMediaGateway:
    gw = TwilioMediaGateway()
    await gw.initialize({
        "sample_rate": 8000,
        "input_sample_rate": 8000,
        "channels": 1,
        "bit_depth": 16,
        "target_buffer_ms": 20,
        "tts_source_format": "s16le",
    })
    await gw.on_call_started(call_id, {"websocket": ws})
    return gw


# ── Factory + status mapping ────────────────────────────────────────────────

def test_factory_builds_twilio_gateway():
    gw = MediaGatewayFactory.create("twilio")
    assert isinstance(gw, TwilioMediaGateway)
    assert gw.name == "twilio"
    assert "twilio" in MediaGatewayFactory.list_gateways()


def test_status_mapping():
    assert map_twilio_status("ringing") == VoiceCallState.RINGING
    assert map_twilio_status("in-progress") == VoiceCallState.ANSWERED
    assert map_twilio_status("completed") == VoiceCallState.COMPLETED
    assert map_twilio_status("busy") == VoiceCallState.BUSY
    assert map_twilio_status("no-answer") == VoiceCallState.NO_ANSWER
    assert map_twilio_status("failed") == VoiceCallState.FAILED
    assert map_twilio_status("canceled") == VoiceCallState.FAILED
    assert map_twilio_status("bogus") is None


# ── TwiML + session config ──────────────────────────────────────────────────

def test_twiml_stream_response_shape(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://voice.example.com")
    twiml = tb._twiml_stream_response("CA1", "+15551112222", "+15553334444")
    assert "<Connect><Stream" in twiml
    assert "wss://voice.example.com/api/v1/twilio/media-stream" in twiml
    assert 'name="callSid"' in twiml and "CA1" in twiml
    assert twiml.startswith("<?xml")


@pytest.mark.asyncio
async def test_build_twilio_session_config_is_8khz(monkeypatch):
    # Provider selection is now sourced per-tenant from the resolver (keyed on
    # the dialed DID), not the process-global. Stub the DID resolution to a
    # tenant whose config is a Cartesia/groq selection.
    from unittest.mock import AsyncMock
    from app.domain.models.ai_config import AIProviderConfig

    resolved = AIProviderConfig(
        llm_provider="groq",
        llm_model="llama-3.3-70b-versatile",
        llm_temperature=0.6,
        llm_max_tokens=150,
        tts_provider="cartesia",
        tts_voice_id="voice-x",
        tts_model="sonic-3",
    )
    monkeypatch.setattr(
        "app.domain.services.tenant_ai_config_resolver.resolve_ai_config_for_did",
        AsyncMock(return_value=("tenant-x", resolved)),
    )
    cfg = await tb._build_twilio_session_config("+15553334444")
    assert cfg.gateway_type == "twilio"
    assert cfg.session_type == "twilio"
    assert cfg.stt_sample_rate == 8000
    assert cfg.tts_sample_rate == 8000
    assert cfg.gateway_sample_rate == 8000
    assert cfg.gateway_input_sample_rate == 8000
    # Provider derived from the resolved config (not hardcoded), tenant threaded.
    assert cfg.llm_provider_type == "groq"
    assert cfg.tenant_id == "tenant-x"


# ── Gateway wire framing (the load-bearing part) ────────────────────────────

@pytest.mark.asyncio
async def test_outbound_audio_is_mulaw_json_frames():
    ws = _FakeWS()
    gw = await _make_gateway("call-out", ws)
    gw.set_stream_sid("call-out", "MZ-stream-1")

    # 20ms @ 8kHz = 160 samples; send two frames' worth.
    await gw.send_audio("call-out", _tone(320))

    assert ws.text_frames, "no outbound frames sent"
    frame = json.loads(ws.text_frames[0])
    assert frame["event"] == "media"
    assert frame["streamSid"] == "MZ-stream-1"
    ulaw = base64.b64decode(frame["media"]["payload"])
    # mu-law is 1 byte/sample; a 20ms frame = 160 samples = 160 bytes.
    assert len(ulaw) == 160


@pytest.mark.asyncio
async def test_outbound_dropped_until_stream_sid_known():
    ws = _FakeWS()
    gw = await _make_gateway("call-nosid", ws)
    # No set_stream_sid → outbound must be a no-op (can't frame for Twilio yet).
    await gw.send_audio("call-nosid", _tone(320))
    assert ws.text_frames == []


@pytest.mark.asyncio
async def test_inbound_mulaw_decoded_and_queued():
    ws = _FakeWS()
    gw = await _make_gateway("call-in", ws)
    # 50ms of audio (> 20ms INPUT_BUFFER_MIN) so it gets queued for STT.
    ulaw = pcm_to_ulaw(_tone(400))
    await gw.feed_twilio_media("call-in", ulaw)
    q = gw.get_audio_queue("call-in")
    assert q is not None and not q.empty()
    chunk = q.get_nowait()
    # Decoded back to 16-bit PCM (2 bytes/sample).
    assert len(chunk) % 2 == 0 and len(chunk) > 0


@pytest.mark.asyncio
async def test_clear_sends_twilio_clear_frame():
    ws = _FakeWS()
    gw = await _make_gateway("call-clear", ws)
    gw.set_stream_sid("call-clear", "MZ-clear")
    await gw.clear_output_buffer("call-clear")
    events = [json.loads(f).get("event") for f in ws.text_frames]
    assert "clear" in events
    clear_frame = next(json.loads(f) for f in ws.text_frames if json.loads(f).get("event") == "clear")
    assert clear_frame["streamSid"] == "MZ-clear"
