"""The recording notice on a real phone call must be provable, protected and followed.

Live 2026-10-21 ("Estimation new", agent-first, two-party policy): 36 calls after
the 2026-10-20 notice fixes, ZERO recordings kept.

* 9 notices played to the end and were still "unconfirmed": the deployed
  voice_gateway (http_server.cpp /v1/sessions/tts/finish) answers
  ``status="transmitted", evidence="transmitted"`` while the disclosure gate
  demanded ``status="completed"``. No telephony recording could pass since 10-05.
* On those same 9 calls the pre-synthesised greeting after the notice was
  refused with ``utterance_already_finalized``: the next send reused the id the
  finish call had just finalised, so callers heard the notice, then silence.
* 20 notices were cut off by the callee's pickup "Hello?" (agent-first calls
  start the notice at answer); the first-line guard only covered caller-first.
"""
from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services import recording_policy_service as policy
from app.domain.services.telephony.modes import agent_first
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.voice_pipeline.first_line_guard import (
    first_line_in_progress,
    is_first_line_greeting_repeat,
)
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter, GatewayResponseError
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway

CALL = "10000000-0000-4000-8000-000000001021"
TENANT = "20000000-0000-4000-8000-000000001021"
PBX = "talky-out-1021"
NOTICE = "This call is being recorded."
DECISION = policy.RecordingDecision(True, True, NOTICE, None, 90, "tenant_policy_two_party_announce")
FRAME_S16 = b"\x01\x00" * 160  # 20 ms at 8 kHz -> one 160-byte PCMU frame


class DeployedGateway:
    """The /tts contract of the voice_gateway binary running in production
    (session.cpp enqueue/finish_tts_utterance, http_server.cpp responses)."""

    def __init__(self, *, pending_polls=0, cancelled=False):
        self.receipt_uid = None
        self.final = False
        self.accepted = 0
        self.last_seq = -1
        self.played = []
        # Frames still in the transmit queue answer "pending" (sent != accepted);
        # an interrupt between the last chunk and finish answers "interrupted".
        self.pending_polls = pending_polls
        self.cancelled = cancelled

    async def __call__(self, method, path, payload=None, **_kwargs):
        if path == "/health":
            return {"tts_receipt_protocol": 1}
        if path == "/v1/sessions/tts/play":
            uid = payload["utterance_id"]
            if uid == self.receipt_uid and self.final:
                raise GatewayResponseError(400, '{"error":"utterance_already_finalized"}')
            if uid != self.receipt_uid:
                self.receipt_uid, self.final, self.accepted, self.last_seq = uid, False, 0, -1
            self.last_seq = payload["chunk_seq"]
            frames = len(base64.b64decode(payload["pcmu_base64"])) // 160
            self.accepted += frames
            self.played.append(uid)
            return {"status": "queued", "session_id": payload["session_id"], "queued_frames": frames}
        if path == "/v1/sessions/tts/finish":
            uid = payload["utterance_id"]
            if uid != self.receipt_uid:
                raise GatewayResponseError(409, '{"error":"unknown_utterance"}')
            receipt = {"receipt_protocol": 1, "utterance_id": uid, "evidence": "transmitted",
                       "transmitted_frames": self.accepted}
            if self.cancelled:
                return {**receipt, "status": "interrupted"}
            if payload["last_chunk_seq"] != self.last_seq:
                raise GatewayResponseError(409, '{"error":"final_chunk_sequence_mismatch"}')
            self.final = True
            if self.pending_polls:
                self.pending_polls -= 1
                return {**receipt, "status": "pending"}
            return {**receipt, "status": "transmitted"}
        raise AssertionError(f"unexpected gateway call {method} {path}")


class Provider:
    name = "synthetic"

    async def stream_synthesize(self, text, **kwargs):
        yield SimpleNamespace(data=FRAME_S16)


async def _phone_call(monkeypatch, **gateway_state):
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", AsyncMock(return_value=DECISION))
    policy.clear_disclosure_state(CALL)
    deployed = DeployedGateway(**gateway_state)
    adapter = AsteriskAdapter()
    adapter._gateway = deployed
    adapter._gateway_sessions[PBX] = "gw-1021"
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    await gateway.on_call_started(CALL, {"adapter": adapter, "pbx_call_id": PBX})
    pipe = SimpleNamespace(
        tts_provider=Provider(), media_gateway=gateway, stt_provider=object(),
        latency_tracker=MagicMock(), tts_sample_rate=8000, _record_silent_turn=MagicMock(),
    )
    playback = TtsPlayback(pipe)
    monkeypatch.setattr(playback, "_try_emergency_voice_clip", AsyncMock(return_value=False))
    pipe.synthesize_and_send_audio = playback.synthesize_and_send
    session = SimpleNamespace(call_id=CALL, turn_id=0, voice_id="v", conversation_history=[], tts_active=False)
    voice = SimpleNamespace(call_id=CALL, call_session=session, pipeline=pipe,
                            media_gateway=gateway, _dialer_tenant_id=TENANT)
    return voice, gateway, deployed


@pytest.mark.asyncio
async def test_a_notice_the_deployed_gateway_transmitted_counts_as_delivered(monkeypatch):
    voice, _gateway, deployed = await _phone_call(monkeypatch)
    await _speak_recording_disclosure(voice)
    assert deployed.final is True
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert policy.consent_satisfied(DECISION, CALL)
    # Transmission is not a hearing claim.
    assert voice.call_session._tts_playout_completed is False


@pytest.mark.asyncio
async def test_a_notice_still_draining_is_confirmed_once_transmission_completes(monkeypatch):
    voice, _gateway, _deployed = await _phone_call(monkeypatch, pending_polls=2)
    await _speak_recording_disclosure(voice)
    assert policy.get_disclosure_state(CALL) == "transmitted"


@pytest.mark.asyncio
async def test_a_notice_interrupted_before_its_finish_never_counts(monkeypatch):
    voice, _gateway, _deployed = await _phone_call(monkeypatch, cancelled=True)
    await _speak_recording_disclosure(voice)
    assert policy.get_disclosure_state(CALL) == "failed"
    assert not policy.consent_satisfied(DECISION, CALL)


@pytest.mark.asyncio
async def test_the_greeting_after_a_finished_notice_is_not_refused(monkeypatch):
    voice, gateway, deployed = await _phone_call(monkeypatch)
    await _speak_recording_disclosure(voice)
    notice_uid = deployed.receipt_uid
    # _send_outbound_greeting's pre-synthesised fast path: plain sends, no begin_playback.
    await gateway.send_audio(CALL, FRAME_S16)
    await gateway.flush_tts_buffer(CALL)
    assert deployed.played[-1] != notice_uid
    assert deployed.final is False


@pytest.mark.asyncio
async def test_an_unfinished_utterance_keeps_its_identity_for_later_chunks(monkeypatch):
    """Only a finish retires an id: ordinary chunks of one reply share it."""
    _voice, gateway, deployed = await _phone_call(monkeypatch)
    await gateway.send_audio(CALL, FRAME_S16)
    await gateway.flush_tts_buffer(CALL)
    await gateway.send_audio(CALL, FRAME_S16)
    await gateway.flush_tts_buffer(CALL)
    assert len(set(deployed.played)) == 1


def _agent_first_session(**attrs) -> CallSession:
    s = CallSession(
        call_id=CALL, campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id=PBX, system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = "agent"
    s.tts_active = True
    for key, value in attrs.items():
        setattr(s, key, value)
    return s


@pytest.mark.parametrize("text", ["Hello?", "Hello. Hello?", "Hi there", "hey"])
def test_a_pickup_greeting_does_not_cut_off_the_notice_on_an_agent_first_call(text):
    s = _agent_first_session(_disclosure_in_flight=True)
    assert first_line_in_progress(s, now=time.monotonic())
    assert is_first_line_greeting_repeat(s, text)


@pytest.mark.parametrize("text", ["Hello, can I help?", "Who is this?", "Stop", "", None])
def test_real_words_or_unknown_speech_still_interrupt_the_notice(text):
    s = _agent_first_session(_disclosure_in_flight=True)
    assert not is_first_line_greeting_repeat(s, text)


def test_after_the_notice_a_hello_to_a_silent_agent_is_a_real_turn():
    # While the agent speaks, a bare "Hello?" never stops it (first_line_guard,
    # 2026-10-22); once it is silent, the "Hello?" is the caller's turn.
    s = _agent_first_session(_first_line_started_at=time.monotonic() - 0.2, tts_active=False)
    assert not first_line_in_progress(s)
    assert not is_first_line_greeting_repeat(s, "Hello?")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["returns", "raises"])
async def test_the_notice_is_marked_in_flight_only_while_it_plays(monkeypatch, outcome):
    voice, _gateway, _deployed = await _phone_call(monkeypatch)
    seen = []

    async def speak(session, text, **kwargs):
        seen.append(getattr(session, "_disclosure_in_flight", False))
        if outcome == "raises":
            raise RuntimeError("synthetic provider failure")
        return False

    voice.pipeline.synthesize_and_send_audio = speak
    await agent_first._speak_recording_disclosure(voice)
    assert seen == [True]
    assert voice.call_session._disclosure_in_flight is False


@pytest.mark.asyncio
async def test_a_caller_first_notice_stays_in_flight_until_its_own_wrapper_ends(monkeypatch):
    """caller_first_disclosure holds the flag around the helper; the helper
    must hand it back as it found it, not clear it early."""
    voice, _gateway, _deployed = await _phone_call(monkeypatch)
    voice.call_session._disclosure_in_flight = True
    await agent_first._speak_recording_disclosure(voice)
    assert voice.call_session._disclosure_in_flight is True
