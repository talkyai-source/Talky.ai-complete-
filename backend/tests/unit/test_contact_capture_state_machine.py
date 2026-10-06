"""Legacy pure parser diagnostics plus current model-tool/persistence boundaries.

advance_capture diagnostics do not describe active voice orchestration.
"""
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
from tests.unit.test_ag05_native_contact_revision import replay, record


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


def test_non_e164_phone_valid_in_several_countries_asks_for_the_country():
    # London without its 0, or a Maine number: never guessed.
    state = _capture("phone", "my number is 207 946 0958", phone_region=None)
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


def test_legacy_phone_candidate_is_neutral_context_without_a_scripted_readback():
    state = update_state_from_user_turn(CallState(), "my number is 020 7946 0958", phone_region=None)
    assert state.phone == "+442079460958" and not state.phone_confirmed
    prompt = compose_system_prompt("BASE", state)
    assert '"value": "+442079460958"' in prompt
    assert '"status": "awaiting_confirmation"' in prompt
    assert "plus 4 4" not in prompt and "Say EXACTLY" not in prompt


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


def test_email_clarification_is_neutral_candidate_data_in_the_next_prompt():
    state = update_state_from_user_turn(CallState(), "all state estimation at gmail dot com")
    prompt = compose_system_prompt("BASE", state)
    assert '"status": "needs_clarification"' in prompt and '"value": null' in prompt
    assert "one letter at a time" not in prompt and "Say EXACTLY" not in prompt


def test_phone_missing_region_context_does_not_guess_a_number_or_script_question():
    state = update_state_from_user_turn(CallState(), "my number is 207 946 0958", phone_region=None)
    prompt = compose_system_prompt("BASE", state)
    assert '"status": "needs_clarification"' in prompt and '"value": null' in prompt
    assert "+1" not in prompt and "Say EXACTLY" not in prompt


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


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["transport_played", "transmitted"])
async def test_native_played_contact_question_does_not_start_regex_capture_mode(evidence):
    r = replay("openai")
    await r.step({"kind": "response", "text": "What is the best phone number to call you back on?",
        "receipt": "completed" if evidence == "transport_played" else "transmitted"})
    assert getattr(r.session.captured_slots, "agent_asked_kind", None) is None
    assert getattr(r.session.captured_slots, "active_contact_kind", None) is None
    assert getattr(r.session.captured_slots, "phone_capture", None) is None
    assert r.gateway.submissions


def test_cancelled_capture_is_visible_as_cancelled_without_a_usable_value():
    state = update_state_from_user_turn(CallState(), "bob at acme dot com")
    state = update_state_from_user_turn(state, "never mind, don't save that")
    prompt = compose_system_prompt("BASE", state, has_callback_executor=True)
    assert '"status": "cancelled"' in prompt and '"value": null' in prompt
    assert "Say EXACTLY" not in prompt and prompt.endswith("BASE")


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


def test_neutral_prompt_exposes_both_contact_candidates_without_forced_order():
    state = update_state_from_user_turn(CallState(),
        "My email is bob@acme.com and my phone number is +1 415 555 2671")
    prompt = compose_system_prompt("BASE", state)
    assert "bob@acme.com" in prompt and "+14155552671" in prompt
    assert prompt.count('"status": "awaiting_confirmation"') == 2
    assert "Say EXACTLY" not in prompt


@pytest.mark.asyncio
async def test_native_tool_sets_pending_and_confirms_from_later_caller_evidence():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    assert snapshot_slots(r.session.captured_slots) == {}
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    await r.step({"kind": "caller", "text": "yes, that's right"})
    assert not r.session.captured_slots.email_confirmed
    await record(r, "bob@acme.com", operation="confirm")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.CONFIRMED
    assert r.session.captured_slots.email_capture.readback is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected_status"),
    (
        ("yes, that's right", "confirmed"),
        ("never mind, don't save that", "cancelled"),
    ),
)
async def test_native_model_resolution_updates_contact_without_directive_interrupt(reply, expected_status):
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    await r.step({"kind": "caller", "text": reply})
    await record(r, "bob@acme.com" if expected_status == "confirmed" else None,
        operation="confirm" if expected_status == "confirmed" else "withdraw")
    assert r.session.captured_slots.email_capture.validation_status == expected_status
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)


@pytest.mark.asyncio
async def test_native_model_can_record_both_fields_without_forced_capture_order():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email is bob@acme.com and phone is +14155552671"})
    await record(r, "+14155552671", kind="phone")
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.active_contact_kind is None
    assert not r.session.captured_slots.email_confirmed and not r.session.captured_slots.phone_confirmed
    await r.step({"kind": "caller", "text": "Yes, both details are correct"})
    await record(r, "bob@acme.com", operation="confirm")
    await record(r, "+14155552671", kind="phone", operation="confirm")
    assert r.session.captured_slots.email_confirmed and r.session.captured_slots.phone_confirmed
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)


@pytest.mark.asyncio
async def test_native_none_confidence_does_not_create_contact_without_model_tool():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "bob at acme dot com", "confidence": None})
    assert snapshot_slots(r.session.captured_slots) == {}
    await record(r, "bob@acme.com")
    assert r.session.captured_slots.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION


@pytest.mark.asyncio
async def test_native_partial_tool_candidate_does_not_force_an_interruption_or_wording():
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email starts bob at"})
    await record(r, "bob at")
    assert r.session.captured_slots.email is None
    assert r.session.captured_slots.email_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)
    await r.step({"kind": "response", "text": "What comes after that?"})
    assert any(event.get("text") == "What comes after that?" for event in r.gateway.controls)


@pytest.mark.asyncio
async def test_realtime_provider_replaces_directive_then_cancels_active_response():
    from app.realtime.openai import (
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
    from app.realtime.bridge import RealtimeBridge
    from app.realtime.openai import RealtimeEvent

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
    # 2026-09-28: a caller-stated pending value is persisted, but unconfirmed.
    pending_item = snapshot_slots(pending)["email"]
    assert pending_item["confirmed"] is False
    assert pending_item["validation_status"] == "awaiting_confirmation"
    assert pending_item["confirmed_at"] is None

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


def _audit_native(conn):
    r = replay("openai")
    r.session._lead_capture_binding = {
        "call_id": "22222222-2222-2222-2222-222222222222",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "campaign_id": "33333333-3333-3333-3333-333333333333",
        "lead_id": "44444444-4444-4444-4444-444444444444",
    }
    r.bridge._knowledge_pool = _AuditPool(conn)
    r.bridge._schedule_transcript_flush = lambda: None  # separate transcript persistence contract
    return r


@pytest.mark.asyncio
async def test_realtime_confirmation_persists_canonical_value_and_audit_once():
    conn = _AuditConn()
    r = _audit_native(conn)
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    pending = [args for sql, args in conn.statements if "INSERT INTO call_lead_details" in sql]
    assert len(pending) == 1 and pending[0][8] is False
    conn.statements.clear()
    await r.step({"kind": "caller", "text": "yes, that's right"})
    await record(r, "bob@acme.com", operation="confirm")
    await record(r, "bob@acme.com", operation="confirm")
    inserts = [(sql, args) for sql, args in conn.statements if "INSERT INTO call_lead_details" in sql]
    assert len(inserts) == 1
    _, args = inserts[0]
    assert args[6] == args[11] == "bob@acme.com" and args[8] is True
    assert args[10] == "bob at acme dot com" and args[12] == "confirmed" and args[13] is not None


@pytest.mark.asyncio
async def test_realtime_pending_correction_revokes_prior_caller_contact_row():
    conn = _AuditConn()
    r = _audit_native(conn)
    await r.step({"kind": "caller", "text": "bob at acme dot com"})
    await record(r, "bob@acme.com")
    await r.step({"kind": "caller", "text": "yes"})
    await record(r, "bob@acme.com", operation="confirm")
    await r.step({"kind": "caller", "text": "Actually, change my email to alice@example.com"})
    await record(r, "alice@example.com")
    revocations = [(sql, args) for sql, args in conn.statements
        if "UPDATE call_lead_details" in sql and "source = 'caller_stated'" in sql]
    assert len(revocations) == 1
    sql, args = revocations[0]
    assert "value = NULL" in sql and args[2] == "email" and args[3] == "awaiting_confirmation"
    assert r.session.captured_slots.email == "alice@example.com" and not r.session.captured_slots.email_confirmed


@pytest.mark.asyncio
async def test_realtime_contact_persistence_is_serialized_across_corrections(
    monkeypatch,
):
    from app.domain.services.voice_pipeline import lead_slot_capture
    from app.realtime.bridge import RealtimeBridge

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


def test_realtime_guide_distinguishes_pending_confirmation_and_persistence():
    from app.realtime.prompts import RealtimePersona, build_realtime_instructions
    text = build_realtime_instructions(RealtimePersona()).lower()
    assert "record_contact" in text and "expected_value" in text
    assert "new value is pending" in text and "result says saved" in text
    assert "confirmation does not mean a message was sent" in text
    assert "say exactly" not in text


def test_turn_telemetry_never_logs_raw_contact_bearing_transcripts():
    from app.domain.services.voice_pipeline.turn_ender import TurnEnder

    source = inspect.getsource(TurnEnder.handle)
    assert "transcript=%r" not in source
    assert '"transcript": full_transcript' not in source
    assert '"response": response_text' not in source
    assert '"voice.turn.transcript"' not in source
