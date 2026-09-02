"""C3: one fail-closed contact-capture machine for every voice pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    advance_capture,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt
from app.domain.services.voice_pipeline.lead_slot_capture import snapshot_slots


def _capture(kind: str, utterance: str, **kwargs):
    return advance_capture(None, kind=kind, utterance=utterance, **kwargs)


def test_email_moves_from_awaiting_confirmation_to_confirmed():
    state = _capture("email", "bob at acme dot com")
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.raw_value == "bob at acme dot com"
    assert state.normalized_value == "bob@acme.com"

    confirmed_at = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    state = advance_capture(
        state,
        kind="email",
        utterance="yes, that's right",
        readback_issued=True,
        confirmation_verdict="affirm",
        now=confirmed_at,
    )
    assert state.status is CaptureStatus.CONFIRMED
    assert state.confirmed_at == confirmed_at


def test_raw_audit_value_is_only_the_contact_span_not_the_whole_turn():
    state = _capture(
        "email",
        "My private account issue is unrelated; my email is Bob@Acme.com, thanks",
    )
    assert state.raw_value == "Bob@Acme.com"
    assert "private" not in state.raw_value.lower()


def test_email_segment_retry_prompt_uses_grammar_the_machine_can_parse():
    state = _capture("email", "my email is bob@acme.com")
    state = advance_capture(
        state,
        kind="email",
        utterance="the username should be @@@",
    )
    assert state.status is CaptureStatus.INVALID
    assert "the username is" in state.clarification_prompt.lower()

    recovered = advance_capture(
        state,
        kind="email",
        utterance="the username is b o b",
    )
    assert recovered.status is CaptureStatus.AWAITING_CONFIRMATION
    assert recovered.normalized_value == "bob@acme.com"


def test_phone_segment_retry_prompt_uses_grammar_the_machine_can_parse():
    state = _capture("phone", "call me on +1 415 555 2671")
    state = advance_capture(
        state,
        kind="phone",
        utterance="the last three digits are two six",
    )
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION
    assert "last" in state.clarification_prompt.lower()
    assert "digits are" in state.clarification_prompt.lower()

    recovered = advance_capture(
        state,
        kind="phone",
        utterance="the last three digits are six seven one",
    )
    assert recovered.status is CaptureStatus.AWAITING_CONFIRMATION
    assert recovered.normalized_value == "+14155552671"


def test_confirmed_contact_is_sticky_without_explicit_correction():
    state = _capture("email", "bob at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )

    assert advance_capture(
        state,
        kind="email",
        utterance="bob@acme.com",
        transcript_confidence=0.1,
    ) == state
    assert advance_capture(
        state,
        kind="email",
        utterance="never mind, skip that",
    ) == state
    assert advance_capture(
        state,
        kind="email",
        utterance="jane@other.com",
    ) == state

    corrected = advance_capture(
        state,
        kind="email",
        utterance="actually, change it to jane@other.com",
    )
    assert corrected.status is CaptureStatus.AWAITING_CONFIRMATION
    assert corrected.normalized_value == "jane@other.com"


def test_confirmed_contact_can_be_explicitly_withdrawn_by_field_name():
    state = _capture("email", "bob at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )

    withdrawn = advance_capture(
        state,
        kind="email",
        utterance="Never mind, do not use that email address.",
    )

    assert withdrawn.status is CaptureStatus.CANCELLED
    assert withdrawn.normalized_value is None


@pytest.mark.parametrize(
    ("kind", "original", "correction", "expected"),
    (
        (
            "email",
            "my email is bob@acme.com",
            "No sorry, my email is alice@example.com",
            "alice@example.com",
        ),
        (
            "phone",
            "my phone number is +1 415 555 0101",
            "my new phone number is +44 20 7946 0958",
            "+442079460958",
        ),
    ),
)
def test_confirmed_contact_accepts_natural_explicit_whole_value_correction(
    kind: str,
    original: str,
    correction: str,
    expected: str,
):
    state = _capture(kind, original)
    state = advance_capture(
        state,
        kind=kind,
        utterance="yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )

    corrected = advance_capture(
        state,
        kind=kind,
        utterance=correction,
    )

    assert corrected.status is CaptureStatus.AWAITING_CONFIRMATION
    assert corrected.normalized_value == expected


def test_invalid_email_is_explicit_and_never_normalized():
    state = _capture("email", "my email is bob at invalid")
    assert state.status is CaptureStatus.INVALID
    assert state.normalized_value is None
    assert state.confirmed_at is None


@pytest.mark.parametrize(
    "address",
    (
        "bob..smith@example.com",
        "bob.@example.com",
        "bob@example-.com",
    ),
)
def test_structurally_invalid_email_never_reaches_confirmation(address: str):
    state = _capture("email", f"my email is {address}")
    assert state.status is CaptureStatus.INVALID
    assert state.normalized_value is None


def test_ambiguous_multiword_email_requests_clarification():
    state = _capture("email", "all state estimation at gmail dot com")
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION
    assert state.normalized_value is None
    assert "spell" in (state.clarification_prompt or "").lower()


@pytest.mark.parametrize(
    "spoken",
    (
        "b o b at gmail dot com",
        "b as in Bravo o as in Oscar b as in Bravo at gmail dot com",
    ),
)
def test_spelled_email_reply_enters_confirmation_instead_of_looping(spoken: str):
    state = _capture("email", spoken)
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "bob@gmail.com"


def test_spoken_letter_correction_changes_only_that_local_part_character():
    state = _capture("email", "bxb at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="the second letter is o as in Oscar",
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "bob@acme.com"
    assert state.segments == ("bob", "acme.com")


def test_domain_only_correction_preserves_confirmed_local_segment():
    state = _capture("email", "bob at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="the domain should be example dot org",
    )
    assert state.normalized_value == "bob@example.org"
    assert state.segments == ("bob", "example.org")
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION


def test_is_actually_segment_corrections_do_not_capture_the_word_actually():
    state = _capture("email", "bob at acme dot com")
    domain = advance_capture(
        state,
        kind="email",
        utterance="the domain is actually example dot org",
    )
    local = advance_capture(
        state,
        kind="email",
        utterance="the username is actually rob",
    )
    assert domain.normalized_value == "bob@example.org"
    assert local.normalized_value == "rob@acme.com"


def test_domain_correction_does_not_fuse_trailing_filler_into_the_tld():
    state = _capture("email", "bob at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="the domain should be example dot org please",
    )
    assert state.normalized_value == "bob@example.org"
    assert state.raw_value == "bob@example.org"


def test_clean_repeat_recovers_from_recognition_clarification():
    state = _capture(
        "email",
        "bob at acme dot com",
        transcript_alternatives=("bob at acne dot com",),
    )
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION

    state = advance_capture(
        state,
        kind="email",
        utterance="bob at acme dot com",
        transcript_confidence=None,
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.validation_status == state.status.value


def test_non_e164_phone_requires_explicit_region_context():
    state = _capture("phone", "my number is 020 7946 0958", phone_region=None)
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION
    assert state.normalized_value is None
    assert "country" in (state.clarification_prompt or "").lower()

    state = advance_capture(
        state,
        kind="phone",
        utterance="the complete number is +44 20 7946 0958",
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "+442079460958"


def test_non_e164_phone_normalizes_with_explicit_region_context():
    state = _capture("phone", "my number is 020 7946 0958", phone_region="GB")
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "+442079460958"


def test_e164_phone_needs_no_region_context():
    state = _capture("phone", "call me on +1 415 555 2671")
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "+14155552671"


def test_phone_last_four_correction_preserves_unaffected_prefix():
    state = _capture("phone", "call me on +1 415 555 2671")
    state = advance_capture(
        state,
        kind="phone",
        utterance="the last four digits should be 1234",
    )
    assert state.normalized_value == "+14155551234"
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION


def test_invalid_phone_segment_correction_keeps_candidate_for_retry():
    state = _capture("phone", "call me on +1 415 555 2671")
    state = advance_capture(
        state,
        kind="phone",
        utterance="the first two digits should be 00",
    )
    assert state.status is CaptureStatus.INVALID
    assert state.normalized_value == "+14155552671"

    state = advance_capture(
        state,
        kind="phone",
        utterance="the first two digits should be 14",
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "+14155552671"


def test_invalid_email_local_correction_is_visible_and_retryable():
    state = _capture("email", "bob at acme dot com")
    state = advance_capture(
        state,
        kind="email",
        utterance="the username should be bob dot dot smith",
    )
    assert state.status is CaptureStatus.INVALID
    assert state.normalized_value == "bob@acme.com"

    state = advance_capture(
        state,
        kind="email",
        utterance="the username should be rob",
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.normalized_value == "rob@acme.com"


def test_flux_confidence_none_is_not_treated_as_low_confidence():
    state = _capture(
        "email",
        "bob at acme dot com",
        transcript_confidence=None,
    )
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION


def test_conflicting_recognition_alternatives_require_clarification():
    state = _capture(
        "email",
        "bob at acme dot com",
        transcript_alternatives=(
            "bob at acme dot com",
            "bob at acne dot com",
        ),
    )
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION
    assert "heard" in (state.clarification_prompt or "").lower()


def test_explicit_reask_signal_requires_clarification_without_confidence_guessing():
    state = _capture(
        "email",
        "bob at acme dot com",
        transcript_confidence=None,
        explicit_reask=True,
    )
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION


@pytest.mark.parametrize(
    ("kind", "utterance"),
    (
        ("phone", "the project number is 12345678"),
        ("email", "I got your email yesterday"),
    ),
)
def test_ordinary_number_or_email_mentions_do_not_enter_contact_mode(
    kind: str,
    utterance: str,
):
    assert _capture(kind, utterance, phone_region="GB") is None


def test_confirmation_loop_is_bounded_and_becomes_clarification():
    state = _capture("email", "bob at acme dot com")
    for _ in range(3):
        state = advance_capture(
            state,
            kind="email",
            utterance="hmm, not sure",
            readback_issued=True,
            confirmation_verdict="unclear",
        )
    assert state.status is CaptureStatus.NEEDS_CLARIFICATION
    assert state.attempts == 3


@pytest.mark.parametrize("kind", ["email", "phone"])
def test_caller_can_cancel_capture(kind: str):
    utterance = "bob at acme dot com" if kind == "email" else "call me on +1 415 555 2671"
    state = _capture(kind, utterance)
    state = advance_capture(
        state,
        kind=kind,
        utterance="never mind, don't save that",
    )
    assert state.status is CaptureStatus.CANCELLED
    assert state.normalized_value is None
    assert state.confirmed_at is None


def test_email_clarification_mode_is_injected_into_the_next_turn_prompt():
    state = update_state_from_user_turn(
        CallState(), "all state estimation at gmail dot com"
    )
    prompt = compose_system_prompt("BASE", state).lower()
    assert "spell" in prompt
    assert "one letter at a time" in prompt


def test_phone_missing_region_prompt_asks_for_country_instead_of_guessing_us():
    state = update_state_from_user_turn(
        CallState(), "my number is 020 7946 0958", phone_region=None
    )
    prompt = compose_system_prompt("BASE", state).lower()
    assert "country" in prompt
    assert "+1" not in prompt


def test_agent_phone_question_arms_mode_for_bare_national_number_reply():
    state = update_state_from_agent_turn(
        CallState(),
        "What is the best callback number for you?",
    )
    assert state.active_contact_kind == "phone"

    state = update_state_from_user_turn(
        state,
        "020 7946 0958",
        phone_region="GB",
    )
    assert state.phone_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.phone == "+442079460958"


def test_agent_phone_mode_accepts_spoken_digit_words_with_explicit_region():
    state = update_state_from_agent_turn(
        CallState(),
        "What is the best callback number for you?",
    )
    state = update_state_from_user_turn(
        state,
        "oh two oh seven nine four six oh nine five eight",
        phone_region="GB",
    )

    assert state.phone_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.phone == "+442079460958"


@pytest.mark.parametrize(
    "agent_turn",
    (
        "Please contact our billing department. Is there anything else?",
        "I can email the quote. What project is this for?",
        "You can contact us later, okay?",
    ),
)
def test_unrelated_agent_contact_mentions_do_not_arm_capture_mode(agent_turn: str):
    state = update_state_from_agent_turn(CallState(), agent_turn)

    assert state.active_contact_kind is None
    assert state.email_capture is None
    assert state.phone_capture is None


def test_agent_reask_is_a_real_flux_ambiguity_signal_then_clean_retry_recovers():
    state = update_state_from_user_turn(CallState(), "bob at acme dot com")
    state = update_state_from_agent_turn(
        state,
        "I heard two different versions. Please repeat the email slowly.",
    )
    assert state.email_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert state.email_capture.attempts == 1

    state = update_state_from_user_turn(state, "bob at acme dot com")
    assert state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION


def test_normal_agent_readback_with_again_does_not_create_false_ambiguity():
    state = update_state_from_user_turn(
        CallState(),
        "My email is bob@acme.com",
    )

    state = update_state_from_agent_turn(
        state,
        "Just to confirm again, your email is bob at acme dot com, correct?",
    )

    assert state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert state.email_capture.attempts == 0


def test_both_live_pipelines_consume_agent_contact_mode_signal():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
    from app.domain.services.voice_pipeline.turn_runner import TurnRunner

    assert "update_state_from_agent_turn" in inspect.getsource(TurnRunner.run)
    assert "update_state_from_agent_turn" in inspect.getsource(
        RealtimeBridge._observe_contact_agent_turn
    )


def test_cancelled_capture_is_not_reasked_or_rendered_as_a_fact():
    state = update_state_from_user_turn(CallState(), "bob at acme dot com")
    state = update_state_from_user_turn(state, "never mind, don't save that")
    assert compose_system_prompt("BASE", state) == "BASE"


def test_cancel_applies_only_to_the_active_contact_mode():
    state = update_state_from_user_turn(CallState(), "bob at acme dot com")
    state = update_state_from_user_turn(
        state,
        "yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )
    state = update_state_from_user_turn(state, "call me on +1 415 555 2671")
    state = update_state_from_user_turn(state, "never mind, don't save that")

    assert state.email_capture.status is CaptureStatus.CONFIRMED
    assert state.email == "bob@acme.com"
    assert state.phone_capture.status is CaptureStatus.CANCELLED


def test_one_turn_can_retain_both_contacts_but_serializes_confirmation():
    state = update_state_from_user_turn(
        CallState(),
        "My email is bob@acme.com and my phone number is +1 415 555 2671",
    )
    assert state.email == "bob@acme.com"
    assert state.phone == "+14155552671"
    assert state.active_contact_kind == "email"

    prompt = compose_system_prompt("BASE", state)
    assert "bob" in prompt.lower()
    assert "+14155552671" not in prompt

    state = update_state_from_user_turn(
        state,
        "yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )
    assert state.email_confirmed is True
    assert state.active_contact_kind == "phone"
    assert "+14155552671" in compose_system_prompt("BASE", state)


@pytest.mark.asyncio
async def test_realtime_bridge_uses_same_machine_and_confirms_only_after_readback():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    session = SimpleNamespace(captured_slots=None)
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
        contact_phone_region="GB",
    )
    await bridge._observe_contact_turn("bob at acme dot com")
    assert session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION

    bridge._remember_contact_turn(
        "assistant", "So that's bob at acme dot com — did I get that right?"
    )
    await bridge._observe_contact_turn("yes, that's right")
    assert session.captured_slots.email_capture.status is CaptureStatus.CONFIRMED
    assert session.captured_slots.email == "bob@acme.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected_status"),
    (
        ("yes, that's right", "confirmed"),
        ("never mind, don't save that", "cancelled"),
    ),
)
async def test_realtime_resolution_supersedes_pending_contact_directive(
    reply: str,
    expected_status: str,
):
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    class RT:
        def __init__(self):
            self.directives = []

        async def interrupt_with_text(self, text):
            self.directives.append(text)

    class Gateway:
        async def clear_output_buffer(self, _call_id):
            return None

    rt = RT()
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=rt,
        media_gateway=Gateway(),
    )
    await bridge._observe_contact_turn("bob at acme dot com")
    bridge._remember_contact_turn(
        "assistant",
        "So that's bob at acme dot com, did I get that right?",
    )
    await bridge._observe_contact_turn(reply)

    assert len(rt.directives) == 2
    assert "awaiting_confirmation" in rt.directives[0]
    assert expected_status in rt.directives[1]
    assert "stop asking" in rt.directives[1].lower()


@pytest.mark.asyncio
async def test_realtime_dual_contact_retires_email_before_advancing_to_phone():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    class RT:
        def __init__(self):
            self.directives = []

        async def interrupt_with_text(self, text):
            self.directives.append(text)

    class Gateway:
        async def clear_output_buffer(self, _call_id):
            return None

    rt = RT()
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=rt,
        media_gateway=Gateway(),
    )
    await bridge._observe_contact_turn(
        "My email is bob@acme.com and my phone number is +1 415 555 2671"
    )
    bridge._remember_contact_turn(
        "assistant",
        "So that's bob at acme dot com, did I get that right?",
    )
    await bridge._observe_contact_turn("yes")

    assert len(rt.directives) == 2
    assert "awaiting_confirmation (email)" in rt.directives[0]
    assert "confirmed (email)" in rt.directives[1]
    assert "awaiting_confirmation (phone)" in rt.directives[1]
    assert rt.directives[1].index("confirmed (email)") < rt.directives[1].index(
        "awaiting_confirmation (phone)"
    )


@pytest.mark.asyncio
async def test_realtime_bridge_none_confidence_is_neutral_but_alternatives_vary():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    session = SimpleNamespace(captured_slots=None)
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
    )
    await bridge._observe_contact_turn(
        "bob at acme dot com",
        {"confidence": None, "alternatives": []},
    )
    assert session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION

    await bridge._observe_contact_turn(
        "bob at acme dot com",
        {
            "confidence": None,
            "alternatives": [{"transcript": "bob at acne dot com"}],
        },
    )
    assert session.captured_slots.email_capture.status is CaptureStatus.NEEDS_CLARIFICATION


@pytest.mark.asyncio
async def test_realtime_ambiguity_interrupts_speculation_with_backend_directive():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    class RT:
        def __init__(self):
            self.directives = []

        async def interrupt_with_text(self, text):
            self.directives.append(text)

    class Gateway:
        def __init__(self):
            self.cleared = []

        async def clear_output_buffer(self, call_id):
            self.cleared.append(call_id)

    rt = RT()
    gateway = Gateway()
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=rt,
        media_gateway=gateway,
    )
    await bridge._observe_contact_turn(
        "bob at acme dot com",
        {"confidence": None, "alternatives": ["bob at acne dot com"]},
    )

    assert gateway.cleared == ["voice-call"]
    assert len(rt.directives) == 1
    assert "needs_clarification" in rt.directives[0]
    assert "two different versions" in rt.directives[0]
    assert "do not confirm" in rt.directives[0].lower()


@pytest.mark.asyncio
async def test_realtime_provider_replaces_directive_then_cancels_active_response():
    from app.infrastructure.realtime.openai_realtime import (
        OpenAIRealtimeSession,
        RealtimeEvent,
    )

    class WS:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

    realtime = OpenAIRealtimeSession(api_key="sk-test")
    realtime._ws = WS()
    realtime._response_active = True
    realtime._event_queue.put_nowait(RealtimeEvent(kind="audio", audio=b"stale"))
    realtime._event_queue.put_nowait(
        RealtimeEvent(kind="caller_transcript", text="keep", is_final=True)
    )
    await realtime.interrupt_with_text("BACKEND CONTACT MODE: invalid")

    assert realtime._ws.sent[0]["type"] == "session.update"
    assert "BACKEND CONTACT MODE: invalid" in realtime._ws.sent[0]["session"]["instructions"]
    assert realtime._ws.sent[1] == {"type": "response.cancel"}
    assert realtime._pending_response_create is True
    queued = list(realtime._event_queue._queue)
    assert not any(event and event.kind == "audio" for event in queued)
    assert any(event and event.kind == "caller_transcript" for event in queued)
    assert realtime._response_epoch == 1

    realtime._response_active = False
    realtime._pending_response_create = False
    realtime._ws.sent.clear()
    await realtime.interrupt_with_text("BACKEND CONTACT MODE: confirmed")

    updated = realtime._ws.sent[0]["session"]["instructions"]
    assert updated.count("CONTACT CAPTURE STATE v1") == 1
    assert "BACKEND CONTACT MODE: confirmed" in updated
    assert "BACKEND CONTACT MODE: invalid" not in updated
    assert not any(item["type"] == "conversation.item.create" for item in realtime._ws.sent)


@pytest.mark.asyncio
async def test_interrupted_realtime_readback_cannot_confirm_contact():
    from app.domain.models.conversation import Message
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
    from app.infrastructure.realtime.openai_realtime import RealtimeEvent

    session = SimpleNamespace(
        captured_slots=update_state_from_user_turn(
            CallState(),
            "bob at acme dot com",
        )
    )

    async def events():
        yield RealtimeEvent(
            kind="agent_transcript",
            text="So that's bob at acme dot com, did I get that right?",
            is_final=True,
        )
        yield RealtimeEvent(
            kind="interrupted",
            raw={"during_response": True},
        )
        yield RealtimeEvent(
            kind="response_done",
            raw={"response": {"status": "cancelled"}},
        )
        yield RealtimeEvent(
            kind="caller_transcript",
            text="yes",
            is_final=True,
        )

    class RT:
        def events(self):
            return events()

    class Gateway:
        async def clear_output_buffer(self, _call_id):
            return None

    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=RT(),
        media_gateway=Gateway(),
        contact_session=session,
    )
    await bridge._pump_model_events()

    assert session.captured_slots.email_confirmed is False
    assert session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION


def test_only_confirmed_contact_exposes_full_audit_payload_for_persistence():
    pending = update_state_from_user_turn(CallState(), "bob at acme dot com")
    assert "email" not in snapshot_slots(pending)

    confirmed = update_state_from_user_turn(
        pending,
        "yes, that's right",
        readback_issued=True,
        confirmation_verdict="affirm",
    )
    item = snapshot_slots(confirmed)["email"]
    assert item["value"] == "bob@acme.com"
    assert item["raw_value"] == "bob at acme dot com"
    assert item["normalized_value"] == "bob@acme.com"
    assert item["validation_status"] == "confirmed"
    assert item["confirmed_at"] is not None


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _AuditConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def transaction(self):
        return _AsyncContext(None)

    async def execute(self, *_args):
        return None

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if "FROM calls" in sql:
            return {"is_test": False}
        return {"id": "written"}


class _AuditPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_kwargs):
        return _AsyncContext(self.conn)


@pytest.mark.asyncio
async def test_realtime_confirmation_persists_canonical_value_and_audit_once():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    conn = _AuditConn()
    call_id = "22222222-2222-2222-2222-222222222222"
    tenant_id = "11111111-1111-1111-1111-111111111111"
    session = SimpleNamespace(
        captured_slots=None,
        _dialer_call_id=call_id,
        _dialer_tenant_id=tenant_id,
        _dialer_campaign_id="33333333-3333-3333-3333-333333333333",
        _dialer_lead_id="44444444-4444-4444-4444-444444444444",
    )
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
        knowledge_pool=_AuditPool(conn),
    )
    await bridge._observe_contact_turn("bob at acme dot com")
    if bridge._contact_tasks:
        await asyncio.gather(*tuple(bridge._contact_tasks))
    assert not [sql for sql, _args in conn.statements if "INSERT INTO call_lead_details" in sql]

    bridge._remember_contact_turn(
        "assistant", "So that's bob at acme dot com — did I get that right?"
    )
    await bridge._observe_contact_turn("yes, that's right")
    if bridge._contact_tasks:
        await asyncio.gather(*tuple(bridge._contact_tasks))

    inserts = [item for item in conn.statements if "INSERT INTO call_lead_details" in item[0]]
    assert len(inserts) == 1
    _sql, args = inserts[0]
    assert args[6] == "bob@acme.com"          # value
    assert args[8] is True                     # confirmed
    assert args[10] == "bob at acme dot com"  # raw_value
    assert args[11] == "bob@acme.com"         # normalized_value
    assert args[12] == "confirmed"
    assert args[13] is not None                # confirmed_at


@pytest.mark.asyncio
async def test_realtime_pending_correction_revokes_prior_caller_contact_row():
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    conn = _AuditConn()
    session = SimpleNamespace(
        captured_slots=None,
        _dialer_call_id="22222222-2222-2222-2222-222222222222",
        _dialer_tenant_id="11111111-1111-1111-1111-111111111111",
        _dialer_campaign_id="33333333-3333-3333-3333-333333333333",
        _dialer_lead_id="44444444-4444-4444-4444-444444444444",
    )
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
        knowledge_pool=_AuditPool(conn),
    )
    await bridge._observe_contact_turn("bob at acme dot com")
    bridge._remember_contact_turn(
        "assistant", "So that's bob at acme dot com — did I get that right?"
    )
    await bridge._observe_contact_turn("yes")
    if bridge._contact_persist_tail:
        await bridge._contact_persist_tail

    await bridge._observe_contact_turn(
        "Actually, change my email to alice@example.com"
    )
    if bridge._contact_persist_tail:
        await bridge._contact_persist_tail

    revocations = [
        item
        for item in conn.statements
        if "UPDATE call_lead_details" in item[0]
        and "source = 'caller_stated'" in item[0]
    ]
    assert len(revocations) == 1
    sql, args = revocations[0]
    assert "value = NULL" in sql
    assert args[2] == "email"
    assert args[3] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_realtime_contact_persistence_is_serialized_across_corrections(
    monkeypatch,
):
    from app.domain.services.voice_pipeline import lead_slot_capture
    from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    completed = []

    async def _capture(session, **_kwargs):
        value = session.captured_slots.email
        if value == "old@example.com":
            first_started.set()
            await release_first.wait()
        completed.append(value)
        return 1

    monkeypatch.setattr(lead_slot_capture, "capture_turn_slots", _capture)
    session = SimpleNamespace(
        captured_slots=CallState(
            email="old@example.com",
            email_confirmed=True,
        )
    )
    bridge = RealtimeBridge(
        call_id="voice-call",
        realtime_session=object(),
        media_gateway=object(),
        contact_session=session,
        knowledge_pool=object(),
    )

    bridge._schedule_contact_persist()
    await first_started.wait()
    session.captured_slots = CallState(
        email="new@example.com",
        email_confirmed=True,
    )
    bridge._schedule_contact_persist()
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(*tuple(bridge._contact_tasks))

    assert completed == ["old@example.com", "new@example.com"]


def test_campaign_phone_region_is_threaded_only_when_explicitly_configured():
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )

    common = {
        "knowledge_driven": True,
        "company_name": "Acme",
        "agent_names": ["Alex"],
    }
    configured = build_telephony_session_config(
        campaign={
            "id": "configured-region",
            "script_config": {**common, "default_country_code": "gb"},
        }
    )
    absent = build_telephony_session_config(
        campaign={"id": "no-region", "script_config": common}
    )

    assert configured.contact_phone_region == "GB"
    assert absent.contact_phone_region is None


def test_realtime_model_is_told_the_same_confirm_before_commit_contract():
    from app.services.scripts.realtime_instructions import (
        RealtimePersona,
        build_realtime_instructions,
    )

    text = build_realtime_instructions(RealtimePersona()).lower()
    assert "contact details" in text
    assert "country code" in text
    assert "confirm" in text
    assert "do not say you saved" in text
    assert "only that segment" in text


def test_turn_telemetry_never_logs_raw_contact_bearing_transcripts():
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder

    source = inspect.getsource(TurnEnder.handle)
    assert "transcript=%r" not in source
    assert '"transcript": full_transcript' not in source
    assert '"response": response_text' not in source
    assert '"voice.turn.transcript"' not in source


def test_cascaded_contact_evidence_is_bound_to_the_same_detached_turn():
    from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder
    from app.domain.services.voice_pipeline.turn_runner import TurnRunner

    dispatch = inspect.getsource(TranscriptHandler.handle)
    ender = inspect.getsource(TurnEnder.handle)
    runner = inspect.getsource(TurnRunner.run)
    assert "transcript_alternatives=_alternatives" in dispatch
    assert "_active_turn_transcript_alternatives" in ender
    assert "_active_turn_transcript_alternatives" in runner
