"""Suppressed hangup recovery must preserve speech/history and clean retry nudges.

Transport/recovery regressions remain. Readback/interstitial parser tests were
retired; standalone capture-mode utility tests below do not claim live wiring.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import capture_mode
from app.domain.services.voice_pipeline.turn_runner import (
    _PHANTOM_GOODBYE_RECOVERIES,
    _PHANTOM_RETRY_INSTRUCTION,
    TurnRunner,
    _same_utterance,
    _spoken_remainder,
)

_ENVELOPE = '{"action":"end_session","reason":"conversation_complete","farewell":"Bye."}'


class _Session:
    """Just enough session for the recovery path."""

    def __init__(self):
        self.call_id = "call-abc123"
        self.conversation_history = []
        self.tts_active = False


def _runner(stream_returns):
    pipeline = MagicMock()
    pipeline._stream_llm_and_tts = AsyncMock(side_effect=stream_returns)
    pipeline.synthesize_and_send_audio = AsyncMock()
    return TurnRunner(pipeline), pipeline


# --------------------------------------------------------------------------
# _spoken_remainder: did the caller hear anything at all?
# --------------------------------------------------------------------------


def test_prose_before_the_envelope_is_what_the_caller_heard():
    assert _spoken_remainder("Sure, I can help with that. " + _ENVELOPE) == (
        "Sure, I can help with that."
    )


def test_envelope_only_turn_was_silent():
    assert _spoken_remainder(_ENVELOPE) == ""
    assert _spoken_remainder("   " + _ENVELOPE) == ""


def test_plain_prose_is_all_spoken():
    assert _spoken_remainder("Got it, thanks.") == "Got it, thanks."


# --------------------------------------------------------------------------
# The recovery path itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prose_already_spoken_is_kept_and_nothing_extra_is_said():
    # The model answered AND asked to hang up. Only the hangup is suppressed;
    # speaking a canned line on top would talk over a finished answer.
    runner, pipeline = _runner([])
    session = _Session()

    out = await runner._recover_suppressed_turn(
        session, None, "Yes, that address is updated. " + _ENVELOPE
    )

    assert out == "Yes, that address is updated."
    pipeline._stream_llm_and_tts.assert_not_awaited()
    pipeline.synthesize_and_send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_turn_is_retried_and_the_retry_is_what_the_caller_gets():
    runner, pipeline = _runner([("Thanks, I have it as the new address.", 12.0, 8.0)])
    session = _Session()

    out = await runner._recover_suppressed_turn(session, None, _ENVELOPE)

    assert out == "Thanks, I have it as the new address."
    pipeline._stream_llm_and_tts.assert_awaited_once()
    # The retry streams its own audio, so no canned line is synthesized.
    pipeline.synthesize_and_send_audio.assert_not_awaited()
    assert out not in _PHANTOM_GOODBYE_RECOVERIES


@pytest.mark.asyncio
async def test_the_retry_nudge_is_removed_from_history_again():
    runner, pipeline = _runner([("Of course.", 1.0, 1.0)])
    session = _Session()
    session.conversation_history.append(
        Message(role=MessageRole.USER, content="no, it is dot co dot uk")
    )

    await runner._recover_suppressed_turn(session, None, _ENVELOPE)

    contents = [m.content for m in session.conversation_history]
    assert _PHANTOM_RETRY_INSTRUCTION not in contents
    assert contents == ["no, it is dot co dot uk"]


@pytest.mark.asyncio
async def test_the_nudge_is_visible_to_the_model_during_the_retry():
    seen = {}

    async def _capture(session, websocket=None):
        seen["roles"] = [(m.role, m.content) for m in session.conversation_history]
        return "Sure.", 1.0, 1.0

    runner, pipeline = _runner([])
    pipeline._stream_llm_and_tts = AsyncMock(side_effect=_capture)
    session = _Session()

    await runner._recover_suppressed_turn(session, None, _ENVELOPE)

    assert seen["roles"][-1] == (MessageRole.SYSTEM, _PHANTOM_RETRY_INSTRUCTION)


@pytest.mark.asyncio
async def test_the_nudge_is_removed_even_when_the_retry_raises():
    runner, pipeline = _runner([])
    pipeline._stream_llm_and_tts = AsyncMock(side_effect=RuntimeError("provider down"))
    session = _Session()

    out = await runner._recover_suppressed_turn(session, None, _ENVELOPE)

    assert _PHANTOM_RETRY_INSTRUCTION not in [
        m.content for m in session.conversation_history
    ]
    assert out == _PHANTOM_GOODBYE_RECOVERIES[0]


@pytest.mark.asyncio
async def test_canned_line_only_once_the_retry_is_also_silent():
    runner, pipeline = _runner([(_ENVELOPE, 1.0, 1.0)])
    session = _Session()

    out = await runner._recover_suppressed_turn(session, None, _ENVELOPE)

    assert out == _PHANTOM_GOODBYE_RECOVERIES[0]
    pipeline.synthesize_and_send_audio.assert_awaited_once()
    assert pipeline.synthesize_and_send_audio.await_args.args[1] == out
    assert session.tts_active is True


@pytest.mark.asyncio
async def test_the_canned_line_never_repeats_itself_verbatim():
    # Firing twice on one call used to read the identical sentence twice, which
    # is exactly what a caller hears as the agent looping.
    runner, _ = _runner([(_ENVELOPE, 1.0, 1.0)] * 3)
    session = _Session()

    said = [
        await runner._recover_suppressed_turn(session, None, _ENVELOPE)
        for _ in range(3)
    ]

    assert said == list(_PHANTOM_GOODBYE_RECOVERIES)
    assert len(set(said)) == 3


# --------------------------------------------------------------------------
# The downstream damage: a canned line masquerading as the agent's read-back
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Consecutive identical replies
# --------------------------------------------------------------------------


def test_same_utterance_ignores_case_spacing_and_punctuation():
    assert _same_utterance("Can you repeat that?", "can you repeat that")
    assert _same_utterance("Sure  thing.", "Sure thing!")


def test_same_utterance_is_false_for_different_lines_and_for_empty():
    assert not _same_utterance("Sure thing.", "Of course.")
    assert not _same_utterance("", "")
    assert not _same_utterance("   ", "")


# --------------------------------------------------------------------------
# Capture mode must arm on a read-back, not only on the ask
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "So that's j dot smith at gmail dot com, did I get that right?",
        "Just to confirm, john at example dot org - is that correct?",
        "I have you as sales at acme dot co dot uk. Is that right?",
        "Let me read that back: j.smith@gmail.com, is that correct?",
    ],
)
def test_a_readback_arms_capture_mode(line):
    assert capture_mode.detect_email_readback(line) is True
    assert capture_mode.detect_capture_trigger(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "I will send it over to john at gmail dot com.",
        "Sure, no problem at all.",
        "Great, and is that correct so far?",
    ],
)
def test_a_statement_does_not_arm_capture_mode(line):
    assert capture_mode.detect_email_readback(line) is False
    assert capture_mode.detect_capture_trigger(line) is False


def test_the_plain_email_ask_still_arms_capture_mode():
    assert capture_mode.detect_email_ask("What's your email address?") is True
    assert capture_mode.detect_capture_trigger("What's your email address?") is True


def test_maybe_enter_arms_the_provider_on_a_readback():
    flux = MagicMock()
    flux.enter_capture_mode = MagicMock(return_value=None)
    call_id = "call-readback-1"
    try:
        capture_mode.maybe_enter(
            flux, call_id, "So that's j dot smith at gmail dot com, is that right?"
        )
        flux.enter_capture_mode.assert_called_once_with(call_id)
    finally:
        capture_mode.clear(call_id)


# --------------------------------------------------------------------------
# End to end: the whole chain the early return used to skip
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_suppressed_turn_keeps_prose_without_automatic_contact_parser():
    """Keep spoken prose/history; contact tools now own capture admission."""
    from tests.unit.test_voice_pipeline_service import (
        _make_service_for_disposition,
        _make_session,
    )

    armed = []
    flux = MagicMock()
    flux.enter_capture_mode = MagicMock(side_effect=lambda cid: armed.append(cid))

    service = _make_service_for_disposition(["What's your email address? " + _ENVELOPE])
    service.stt_provider = flux
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "sure, go ahead"

    try:
        await service.handle_turn_end(session, AsyncMock())

        spoken = [
            m.content
            for m in session.conversation_history
            if m.role == MessageRole.ASSISTANT
        ]
        assert spoken == ["What's your email address?"]
        assert not any(line in spoken for line in _PHANTOM_GOODBYE_RECOVERIES)
        assert armed == []  # Spoken prose no longer enables a regex capture workflow.
    finally:
        capture_mode.clear(session.call_id)


@pytest.mark.asyncio
async def test_actual_cerebras_thanks_envelope_is_recovered_without_saying_goodbye():
    """Replay the Oct 2 synthetic model output through the real full turn path."""
    from app.domain.models.session import CallState
    from tests.unit.test_voice_pipeline_service import (
        _make_service_for_disposition,
        _make_session,
    )

    envelope = '{"action":"end_session","reason":"user_done","farewell":"Glad I could help. Take care."}'
    acknowledgment = "You're welcome. Is there anything else you'd like to ask?"
    responses = iter([envelope, acknowledgment])
    model_prompts = []

    class ReplayProvider:
        async def stream_chat_with_timeout(self, *args, **kwargs):
            model_prompts.append(kwargs)
            yield next(responses)

    service = _make_service_for_disposition([])
    service.llm_provider = ReplayProvider()
    submitted = []

    async def capture_tts(_session, text, *args, **kwargs):
        submitted.append(text)
        return False

    service.synthesize_and_send_audio = capture_tts
    session = _make_session()
    session.campaign_id = "synthetic-campaign"
    session._has_introduced = True
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    session.current_user_input = "Thanks, that answers my question."

    await service.handle_turn_end(session, AsyncMock())

    assert len(model_prompts) == 2  # One bounded recovery, not a silent turn.
    assert " ".join(submitted) == acknowledgment
    assistant_history = [m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT]
    assert assistant_history == [acknowledgment]
    assert not getattr(session, "_end_call_requested", False)
    assert not session._end_session_action_handled
    assert session.state != CallState.ENDED
    service.media_gateway.hangup_call.assert_not_awaited()
    assert _PHANTOM_RETRY_INSTRUCTION not in [m.content for m in session.conversation_history]
