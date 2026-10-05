"""Native caller admission -> real session activity -> watchdog classification.

Provider parsers and bridge methods are real; SDK/media ports are synthetic.
No acoustic source attribution, elapsed five-minute call, or real hangup proof.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services.telephony.lifecycle import _collect_expired_sessions
from scripts.evaluate_ag04_conversations import offline_network_guard
from tests.qualification.ag04_native import NativeReplay, _corpus


@pytest.fixture(params=["openai", "xai"])
async def native(request):
    attempts = []
    with offline_network_guard(attempts):
        replay = NativeReplay(
            {"id": "native-activity", "semantic_ids": [], "expect": {}, "steps": []},
            request.param,
            _corpus(Path(__file__).resolve().parents[3]),
        )
        old = datetime.utcnow() - timedelta(seconds=301)
        session = CallSession(
            call_id=replay.call_id, campaign_id="synthetic-campaign", lead_id="synthetic-lead",
            provider_call_id="synthetic-provider", system_prompt="Synthetic activity probe",
            voice_id="synthetic-voice", state=CallState.ACTIVE,
            started_at=old, last_activity_at=old,
        )
        for key, value in vars(replay.session).items():
            setattr(session, key, value)
        replay.session = session
        replay.bridge._contact_session = session
        replay.bridge._action_session = session
        try:
            yield replay
        finally:
            replay.gateway.release.set()
            await replay.bridge.stop()
            replay.transcripts.clear_buffer(replay.call_id)
        assert attempts == []


def classify(replay):
    return _collect_expired_sessions(
        [(replay.call_id, SimpleNamespace(call_session=replay.session))],
        inactivity_timeout_s=300, max_duration_s=600, soft_cap_s=0,
    )


def age_activity(replay):
    old = datetime.utcnow() - timedelta(seconds=301)
    replay.session.last_activity_at = old
    return old


async def caller(replay, text="What information is available?", *, item="current", revision=False):
    await replay.step({"kind": "caller", "item": item, "text": text, "revision": revision})


async def final(replay, text, item):
    await replay.wire({"type": "conversation.item.input_audio_transcription.completed",
                       "item_id": item, "transcript": text})
    await replay.drain()


@pytest.mark.asyncio
async def test_current_caller_and_completed_response_are_not_classified_inactive(native):
    old = native.session.last_activity_at
    await caller(native)
    await native.step({"kind": "response", "text": "What would you like to know?"})
    assert native.bridge._latest_caller_text == "What information is available?"
    assert native.gateway.submissions
    assert native.session.last_activity_at > old
    assert classify(native) == ([], [])


@pytest.mark.asyncio
async def test_current_correction_is_not_new_caller_activity(native):
    await caller(native)
    old = age_activity(native)
    await caller(native, "Actually, what are the opening hours?", revision=True)
    assert native.session.last_activity_at == old
    assert classify(native) == ([native.call_id], [])


@pytest.mark.asyncio
async def test_legacy_unanchored_admitted_final_uses_same_activity_boundary(native):
    old = native.session.last_activity_at
    await final(native, "What information is available?", "legacy-first")
    assert native.bridge._latest_caller_text == "What information is available?"
    assert native.session.last_activity_at > old
    assert classify(native) == ([], [])


@pytest.mark.asyncio
async def test_real_activity_cannot_extend_absolute_duration(native):
    native.session.started_at = datetime.utcnow() - timedelta(seconds=601)
    old = native.session.last_activity_at
    await caller(native)
    assert native.session.last_activity_at > old
    assert classify(native) == ([], [native.call_id])


@pytest.mark.asyncio
async def test_genuine_no_input_retains_existing_inactivity_expiry(native):
    old = native.session.last_activity_at
    await native.drain()
    assert native.session.last_activity_at == old
    assert classify(native) == ([native.call_id], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   "])
async def test_empty_final_does_not_prolong_inactivity(native, text):
    old = native.session.last_activity_at
    await caller(native, text)
    assert native.session.last_activity_at == old
    assert classify(native) == ([native.call_id], [])


@pytest.mark.asyncio
async def test_current_retraction_does_not_prolong_inactivity(native):
    await caller(native)
    old = age_activity(native)
    await caller(native, "", revision=True)
    assert native.bridge._latest_caller_text == ""
    assert native.session.last_activity_at == old


@pytest.mark.asyncio
async def test_duplicate_provider_final_does_not_prolong_inactivity(native):
    await caller(native)
    old = age_activity(native)
    await caller(native, revision=True)
    assert native.session.last_activity_at == old


@pytest.mark.asyncio
async def test_late_historical_final_is_not_current_activity(native):
    await native.step({"kind": "start", "item": "older"})
    await native.step({"kind": "start", "item": "newer"})
    old = native.session.last_activity_at
    await final(native, "I am not your customer.", "older")
    assert native.bridge._live_state.customer_relationship.value == "denied"
    assert native.session.last_activity_at == old


@pytest.mark.asyncio
async def test_unowned_final_does_not_prolong_inactivity(native):
    await native.step({"kind": "start", "item": "known"})
    old = native.session.last_activity_at
    await final(native, "What are your hours?", "unowned")
    assert native.session.last_activity_at == old
    assert native.bridge._latest_caller_text == ""


@pytest.mark.asyncio
async def test_older_revision_does_not_prolong_inactivity(native):
    await caller(native, item="older")
    await caller(native, "What are your hours?", item="newer")
    old = age_activity(native)
    await final(native, "Actually, tell me more.", "older")
    assert native.session.last_activity_at == old
    assert native.bridge._latest_caller_text == "What are your hours?"


@pytest.mark.asyncio
async def test_vad_without_final_and_cancelled_input_do_not_prolong_inactivity(native):
    old = native.session.last_activity_at
    await native.step({"kind": "start", "item": "not-final"})
    assert native.session.last_activity_at == old
    native.bridge._stop.set()
    await final(native, "What are your hours?", "not-final")
    assert native.session.last_activity_at == old


@pytest.mark.asyncio
async def test_agent_output_is_not_caller_activity(native):
    old = native.session.last_activity_at
    await native.step({"kind": "response", "text": "What would you like to know?"})
    assert native.gateway.submissions
    assert native.session.last_activity_at == old


@pytest.mark.asyncio
@pytest.mark.parametrize("pcm", [b"\x00\x00" * 160, b"\x00\x20" * 160])
async def test_raw_audio_energy_alone_cannot_keep_call_active(native, pcm):
    old = native.session.last_activity_at
    await native.gateway.queue.put(pcm)
    original_send = native.provider.send_caller_audio

    async def send_and_stop(data):
        await original_send(data)
        native.bridge._stop.set()

    native.provider.send_caller_audio = send_and_stop
    await asyncio.wait_for(native.bridge._pump_caller_audio(), timeout=2)
    assert any(row.get("type") == "input_audio_buffer.append" for row in native.socket.sent)
    assert native.session.last_activity_at == old
    assert classify(native) == ([native.call_id], [])
