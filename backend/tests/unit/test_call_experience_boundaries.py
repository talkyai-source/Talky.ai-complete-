"""Conversation progress must follow caller evidence, not scripted milestones."""
from types import SimpleNamespace

import pytest

from app.domain.services.end_session_action import caller_signaled_end, repeated_decline_allows_end
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, advance_capture
from app.domain.services.voice_pipeline.turn_runner import _note_unheard_greeting_bargein
from app.services.scripts.prompts.live_state import build_live_state_block


@pytest.mark.parametrize("text", [
    "No thanks to email.", "I'm not interested in SMS.",
    "That's all for my email.", "I'm done giving my number.",
    "No, I don't have a card machine.", "Thanks, I am new to taking payments.",
])
def test_topic_refusal_or_factual_negative_never_authorizes_model_hangup(text):
    assert not caller_signaled_end(text)
    assert not repeated_decline_allows_end(text, 3)


@pytest.mark.parametrize("text", [
    "No thanks.", "I'm not interested.", "I'm done, thanks.",
    "No thanks to email. Goodbye.", "Please end the call.", "Stop calling me.",
    "I'm done with this call.", "Not interested in this conversation.", "That's all for now.",
])
def test_actual_call_refusal_or_goodbye_still_authorizes_hangup(text):
    assert caller_signaled_end(text)


def test_unheard_opening_interruptions_do_not_invent_delivered_identity():
    session = SimpleNamespace()
    for _ in range(3):
        _note_unheard_greeting_bargein(session)
    assert not getattr(session, "_has_introduced", False)
    block = build_live_state_block(agent_name="Ava", company_name="Northwind",
        opening_interrupted=bool(session._greeting_bargein_count))
    assert "interrupted" in block
    assert "latest" in block
    assert "ALREADY introduced" not in block
    assert "then stop and let them answer" not in block


@pytest.mark.parametrize("kind,original,correction,other", [
    ("email", "bob@example.com", "the first letter is r", "the first letter is t"),
    ("phone", "+14155552671", "the last three digits are two three four", "the last three digits are two three five"),
])
@pytest.mark.parametrize("uncertainty", ["low_confidence", "alternatives", "explicit_reask"])
def test_uncertain_segment_correction_is_not_promoted_to_a_readback(kind, original, correction, other, uncertainty):
    state = advance_capture(None, kind=kind, utterance=original, mode_active=True)
    state = advance_capture(state, kind=kind, utterance="yes", readback_issued=True, confirmation_verdict="affirm")
    kwargs = {"transcript_confidence": 0.1} if uncertainty == "low_confidence" else (
        {"transcript_alternatives": (other,)} if uncertainty == "alternatives" else {"explicit_reask": True})
    changed = advance_capture(state, kind=kind, utterance=correction, **kwargs)
    assert changed.status is CaptureStatus.NEEDS_CLARIFICATION
    assert changed.confirmed_at is None
    # Keep the previous segments for a focused repair; do not choose the new one.
    assert changed.segments == state.segments
    assert "unclear part" in changed.clarification_prompt


@pytest.mark.parametrize("kind,original,correction,expected", [
    ("email", "bob@example.com", "the first letter is r", "rob@example.com"),
    ("phone", "+14155552671", "the last three digits are two three four", "+14155552234"),
])
def test_clear_segment_correction_still_requires_fresh_confirmation(kind, original, correction, expected):
    state = advance_capture(None, kind=kind, utterance=original, mode_active=True)
    changed = advance_capture(state, kind=kind, utterance=correction)
    assert changed.status is CaptureStatus.AWAITING_CONFIRMATION
    assert changed.normalized_value == expected
    assert changed.confirmed_at is None


@pytest.mark.parametrize("text", ["No thanks to email.", "I'm not interested in SMS."])
def test_contact_channel_refusal_pauses_capture_without_ending_the_call(text):
    from app.services.scripts.call_state_tracker import CallState, update_state_from_user_turn
    state = update_state_from_user_turn(CallState(), "My email is bob@example.com")
    pending = state.email_capture
    changed = update_state_from_user_turn(state, text)
    assert changed.contact_capture_paused
    assert changed.email_capture == pending
    assert not caller_signaled_end(text)
    resumed = update_state_from_user_turn(changed, "Actually, my email is rob@example.com")
    assert not resumed.contact_capture_paused
    assert resumed.email_capture.normalized_value == "rob@example.com"


@pytest.mark.parametrize("segment", [False, True])
@pytest.mark.parametrize("evidence", ["confidence", "alternatives", "reask"])
def test_uncertain_recognition_cannot_restart_clarification_forever(segment, evidence):
    state = advance_capture(None, kind="email", utterance="bob@example.com", mode_active=True)
    text = "the first letter is r" if segment else "rob@example.com"
    other = "the first letter is t" if segment else "tom@example.com"
    kwargs = ({"transcript_confidence": 0.1} if evidence == "confidence" else
        {"transcript_alternatives": (other,)} if evidence == "alternatives" else {"explicit_reask": True})
    for _ in range(5):
        state = advance_capture(state, kind="email", utterance=text, mode_active=True, **kwargs)
    assert state.status is CaptureStatus.CANCELLED
    assert advance_capture(state, kind="email", utterance=text, mode_active=True, **kwargs) == state
    recovered = advance_capture(state, kind="email", utterance=text, mode_active=True)
    assert recovered.status is CaptureStatus.AWAITING_CONFIRMATION
    assert recovered.normalized_value == "rob@example.com"
