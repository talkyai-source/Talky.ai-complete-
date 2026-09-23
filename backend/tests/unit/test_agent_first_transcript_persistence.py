"""The spoken greeting and recording notice must reach the PERSISTED
transcript, not just `conversation_history` (2026-09-24).

`conversation_history` is process memory only — it exists to give the LLM
context for the next turn. `calls.transcript_json` is fed exclusively by
`TranscriptService.accumulate_turn` (see `save_call_transcript_on_hangup`,
which reads the buffer keyed on `voice_session.call_id`). Before this fix,
`agent_first._speak_recording_disclosure` and `_send_outbound_greeting`
appended the spoken text to `conversation_history` only, so the two
sentences the callee actually heard first were silently missing from the
saved record.

Evidence: 6e0e221b.call.json's transcript_json holds 3 words even though a
60-character disclosure plus a greeting were spoken (talky-api.log:398-499);
53d16d3e.transcript.txt starts with the caller even though "Thanks for
calling. How can I help?" was the first thing said.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import RecordingDecision
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
    _speak_recording_disclosure,
)
from app.domain.services.transcript_service import TranscriptService

TENANT = "22222222-2222-2222-2222-222222222222"
CALL_UUID = "11111111-1111-1111-1111-111111111111"

GREETING = "Hi, this is Sarah calling from Acme — do you have a quick minute?"

_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=True,
    announcement_text="This call may be recorded for quality and training purposes.",
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_two_party_announce",
)

_NO_ANNOUNCE = RecordingDecision(
    should_record=True,
    announcement_required=False,
    announcement_text=None,
    opt_out_dtmf_digit=None,
    retention_days=90,
    reason="tenant_policy_one_party",
)


@pytest.fixture(autouse=True)
def _clean_state():
    rps._DISCLOSURE_LEDGER.clear()
    TranscriptService.clear_all_buffers()
    yield
    rps._DISCLOSURE_LEDGER.clear()
    TranscriptService.clear_all_buffers()


class _FakeMediaGateway:
    async def send_audio(self, call_id, chunk):
        pass

    async def flush_tts_buffer(self, call_id):
        pass

    async def clear_output_buffer(self, call_id):
        pass


class _FakePipeline:
    """Real TranscriptService instance attached — accumulate_turn calls
    reach the actual class-level buffer, so assertions read it back exactly
    the way `save_call_transcript_on_hangup` does."""

    def __init__(self, interrupted=False):
        self.transcript_service = TranscriptService()
        self._interrupted = interrupted

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None):
        return self._interrupted


def _make_voice_session(**pipeline_kwargs):
    session = types.SimpleNamespace(
        call_id=CALL_UUID,
        talklee_call_id="tlk-1",
        turn_id=0,
        llm_active=False,
        tts_active=False,
        barge_in_event=None,
        conversation_history=[],
    )
    voice_session = types.SimpleNamespace(
        call_id=CALL_UUID,
        call_session=session,
        pipeline=_FakePipeline(**pipeline_kwargs),
        media_gateway=_FakeMediaGateway(),
        _dialer_tenant_id=TENANT,
        _presynth_greeting_audio=[b"\x00\x01" * 10],
        _presynth_greeting_text=GREETING,
    )
    return voice_session, session


def _patched_policy(decision):
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    return (
        patch("app.core.container.get_container", return_value=container),
        patch.object(
            rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision)
        ),
    )


@pytest.mark.asyncio
async def test_disclosure_is_written_to_the_persisted_transcript_buffer():
    """Reproduces 6e0e221b: the spoken notice must reach the transcript
    buffer TranscriptService.accumulate_turn feeds, not just history."""
    voice_session, session = _make_voice_session()
    ts = voice_session.pipeline.transcript_service
    p_container, p_policy = _patched_policy(_ANNOUNCE)

    with p_container, p_policy:
        await _speak_recording_disclosure(voice_session)

    contents = [t.content for t in ts.get_turns(CALL_UUID)]
    assert contents, (
        "the persisted transcript buffer must contain the spoken notice — "
        "it was silently missing before this fix"
    )
    assert "recorded" in contents[0].lower()
    assert ts.get_turns(CALL_UUID)[0].role == "assistant"


@pytest.mark.asyncio
async def test_greeting_and_disclosure_both_land_in_the_transcript_in_order():
    """Reproduces 53d16d3e/6e0e221b end to end: both the notice and the
    opener must appear in calls.transcript_json, in the order spoken."""
    voice_session, session = _make_voice_session()
    ts = voice_session.pipeline.transcript_service
    p_container, p_policy = _patched_policy(_ANNOUNCE)

    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    contents = [t.content for t in ts.get_turns(CALL_UUID)]
    assert len(contents) == 2, (
        f"expected [notice, greeting] in the persisted transcript, got {contents!r}"
    )
    assert "recorded" in contents[0].lower()
    assert contents[1] == GREETING
    assert all(t.role == "assistant" for t in ts.get_turns(CALL_UUID))
    # Non-vacuity: conversation_history alone was never the bug — the
    # missing half was the persisted buffer, and it must not now be short.
    assert [m.content for m in session.conversation_history] == contents


@pytest.mark.asyncio
async def test_no_announcement_still_persists_the_greeting_alone():
    """One-party tenants never speak a notice, so only the greeting should
    land in the persisted transcript — proves the disclosure branch above
    isn't the only thing feeding the buffer."""
    voice_session, session = _make_voice_session()
    ts = voice_session.pipeline.transcript_service
    p_container, p_policy = _patched_policy(_NO_ANNOUNCE)

    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    contents = [t.content for t in ts.get_turns(CALL_UUID)]
    assert contents == [GREETING]


@pytest.mark.asyncio
async def test_interrupted_greeting_persists_only_the_trimmed_spoken_text():
    """A barge-in trims what's appended to history; the persisted transcript
    must carry the SAME trimmed text, not the full unheard greeting."""
    voice_session, session = _make_voice_session(interrupted=False)
    voice_session.pipeline._interrupted = False
    # Multiple chunks so a barge-in after the first one leaves a real,
    # partial fraction of the greeting "spoken" for the trim math to bite.
    voice_session._presynth_greeting_audio = [b"\x00\x01" * 10] * 8
    ts = voice_session.pipeline.transcript_service
    p_container, p_policy = _patched_policy(_NO_ANNOUNCE)

    # Force a barge-in partway through the pre-synth fast path.
    class _BargeEvent:
        def __init__(self):
            self._n = 0

        def is_set(self):
            self._n += 1
            return self._n > 1  # let the first chunk send, then interrupt

        def clear(self):
            pass

    session.barge_in_event = _BargeEvent()

    with p_container, p_policy:
        await _send_outbound_greeting(voice_session)

    persisted = [t.content for t in ts.get_turns(CALL_UUID)]
    history = [m.content for m in session.conversation_history]
    assert persisted == history, (
        "the persisted transcript must match exactly what history recorded "
        "as actually spoken, barge-in trimming included"
    )
    assert persisted[0] != GREETING, (
        "harness sanity: the barge-in must have actually trimmed the text"
    )
