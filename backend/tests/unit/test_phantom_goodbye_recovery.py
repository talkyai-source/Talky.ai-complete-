"""Suppressed hangup recovery must preserve speech/history and clean retry nudges.

Transport/recovery regressions remain. Readback/interstitial parser tests were
retired; standalone capture-mode utility tests below do not claim live wiring.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services.voice_pipeline import capture_mode
from app.domain.services.voice_pipeline.turn_runner import (
    _same_utterance,
    _spoken_remainder,
)

_ENVELOPE = '{"action":"end_session","reason":"conversation_complete","farewell":"Bye."}'






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

    assert len(model_prompts) == 1  # No scripted retry after a denied effect.
    assert submitted == []
    assistant_history = [m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT]
    assert assistant_history == []
    assert not getattr(session, "_end_call_requested", False)
    assert not session._end_session_action_handled
    assert session.state != CallState.ENDED
    service.media_gateway.hangup_call.assert_not_awaited()
