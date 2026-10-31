"""agent_first non-turn audio must never be tracked as turn latency
(2026-09-24, call 6e0e221b).

THE DEFECT
----------
`_speak_recording_disclosure`'s `synthesize_and_send_audio` call
(agent_first.py ~329) left `track_latency` at its default (True). On a
callee-first (first_speaker="user") outbound call, this notice is the FIRST
TTS spoken on the call -- before `start_turn`/`mark_llm_start` ever run for
turn 0 -- so its TTS stamps `tts_first_chunk_time` / `response_start_time` /
`audio_start_time` on whatever turn-0 LatencyMetrics object already exists.
When the real turn-0 reply plays moments later, the subtraction against the
notice's early stamps produces negative/garbage "Turn 0 latency" -- the same
class of bug the 2026-09-23 silence-monitor-nudge fix closed for the nudge
path (see test_silence_nudge_latency_not_tracked.py). The slow-path greeting
TTS (agent_first.py ~601, the real-time fallback opener) is not an LLM turn
either and had the identical gap.

THE FIX: both call sites now pass `track_latency=False`, exactly like every
other non-turn TTS call site already does (turn_streamer.py's canned
apology/recovery, audio_ingest.py's silence nudge, voice_pipeline_service.py's
end-session farewell).

Drives the REAL `_speak_recording_disclosure` / `_send_outbound_greeting`
against a real pydantic `CallSession` (session attributes -- llm_active,
tts_active, conversation_history -- matter here, see
test_session_scratch_attrs.py for why a SimpleNamespace session would not
prove this) and inspects the actual kwargs of the call to the (mocked) TTS
collaborator -- the collaborator is not the unit under test; the call SITE's
arguments are.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.models.session import CallSession
from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import RecordingDecision
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
    _speak_recording_disclosure,
)

TENANT = "22222222-2222-2222-2222-222222222222"
CALL_UUID = "11111111-1111-1111-1111-111111111111"

_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text="This call may be recorded for quality and training purposes.",
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_two_party_announce",
)


def _session() -> CallSession:
    return CallSession(
        call_id=CALL_UUID,
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )


def _pipeline() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        clear_barge_in_event=lambda session: None,
        synthesize_and_send_audio=AsyncMock(return_value=False),
    )


def _patched_policy(decision):
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    return (
        patch("app.core.container.get_container", return_value=container),
        patch.object(
            rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision)
        ),
    )


@pytest.fixture(autouse=True)
def _clean_ledger():
    """The disclosure ledger is module-global; keep tests independent."""
    rps._DISCLOSURE_LEDGER.clear()
    yield
    rps._DISCLOSURE_LEDGER.clear()


@pytest.mark.asyncio
async def test_recording_disclosure_passes_track_latency_false():
    session = _session()
    pipeline = _pipeline()
    voice_session = types.SimpleNamespace(
        call_id=CALL_UUID,
        call_session=session,
        pipeline=pipeline,
        _dialer_tenant_id=TENANT,
    )
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await _speak_recording_disclosure(voice_session)

    calls = pipeline.synthesize_and_send_audio.await_args_list
    assert calls, "no disclosure was spoken -- nothing to check"
    for call in calls:
        assert call.kwargs.get("track_latency") is False, (
            f"recording-disclosure call {call} did not pass "
            "track_latency=False -- its TTS stamps the turn-0 LatencyMetrics "
            "before the real reply exists (6e0e221b)"
        )


@pytest.mark.asyncio
async def test_slow_path_greeting_passes_track_latency_false():
    """agent_first.py ~601: the real-time fallback greeting (pre-synth
    unavailable) is not an LLM turn either and must not be tracked."""
    session = _session()
    pipeline = _pipeline()
    voice_session = types.SimpleNamespace(
        call_id=CALL_UUID,
        call_session=session,
        pipeline=pipeline,
        media_gateway=types.SimpleNamespace(),
        _dialer_tenant_id=TENANT,
        _first_speaker="agent",
        # Force the slow path -- no pre-synthesized greeting available.
        _presynth_greeting_audio=None,
        _presynth_greeting_text=None,
    )
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    # Two TTS calls land on the same mock: the disclosure (proven above) and
    # the slow-path greeting itself.
    calls = pipeline.synthesize_and_send_audio.await_args_list
    assert len(calls) == 2, f"expected [disclosure, greeting], got {calls!r}"
    for call in calls:
        assert call.kwargs.get("track_latency") is False, (
            f"agent_first non-turn TTS call {call} did not pass "
            "track_latency=False"
        )
