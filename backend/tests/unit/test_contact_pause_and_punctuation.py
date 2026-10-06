"""Legacy pure parser diagnostics and current neutral-state/native-tool contracts."""
import pytest
from types import SimpleNamespace

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.domain.services.voice_pipeline.lead_slot_capture import pending_contact_revocations
from app.services.scripts.call_state_tracker import (
    CallState, update_state_from_agent_turn, update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt, turn_directive
from app.services.scripts.spoken_email_normalizer import extract_email_from_speech


@pytest.mark.parametrize("utterance,expected", [
    ("My email is anna.support@example.com. Please take it down.", "anna.support@example.com"),
    ("My email is anna.support@example.com.", "anna.support@example.com"),
    ("anna dot support at example dot com. Please take it down.", "anna.support@example.com"),
    ("Please use anna+sales@example.co.uk. Thanks.", "anna+sales@example.co.uk"),
    ("anna . support @ example . co . uk", "anna.support@example.co.uk"),
    ("anna.support@example.com.please", "anna.support@example.com.please"),
])
def test_sentence_full_stop_never_becomes_an_extra_domain_label(utterance, expected):
    assert extract_email_from_speech(utterance) == expected


def test_domain_segment_correction_keeps_next_sentence_out_of_the_domain():
    state = update_state_from_user_turn(CallState(), "My email is anna@example.net")
    state = update_state_from_user_turn(state, "The domain is example.com. Please change it.")
    assert state.email == "anna@example.com"


@pytest.mark.parametrize("utterance, expected", [
    # Exact final returned by the live Cartesia -> Flux synthetic audio probe.
    ("Please note my email Anna dot support at example dot com.", "anna.support@example.com"),
    ("My email Anna dot support at example dot com.", "anna.support@example.com"),
    ("My email: anna dot support at example dot com.", "anna.support@example.com"),
    ("My email is anna dot support at example dot com.", "anna.support@example.com"),
    ("Please note my email me at example dot com.", "me@example.com"),
    ("My email yes at example dot com.", "yes@example.com"),
])
def test_explicit_self_email_cue_keeps_spoken_syntax_without_needing_a_colon(utterance, expected):
    state = update_state_from_user_turn(CallState(), utterance)
    assert state.email == expected
    assert not state.email_confirmed
    assert state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
    assert turn_directive(state) is None


@pytest.mark.parametrize("utterance", [
    "Please note my colleague's email Anna dot support at example dot com.",
    "Please don't note my email Anna dot support at example dot com.",
    "My email is not Anna dot support at example dot com.",
    "My email all state estimation at example dot com.",
    "You can send me on Anna dot support at example dot com.",
    "My email Anna dot support at example dot com is not mine.",
])
def test_email_cue_does_not_invent_ownership_or_guess_carrier_boundaries(utterance):
    state = update_state_from_user_turn(CallState(), utterance)
    assert state.email is None and not state.email_confirmed


def test_third_party_correction_revokes_only_the_disowned_field():
    state = CallState(email="anna@example.com", email_confirmed=True,
                      phone="+14155552671", phone_confirmed=True)
    state = update_state_from_user_turn(state, "Actually that email is my colleague's, not mine. Leave it unconfirmed.")
    assert state.email is None
    assert state.email_capture.status is CaptureStatus.CANCELLED
    assert state.phone == "+14155552671" and state.phone_confirmed
    assert state.contact_capture_paused
    assert turn_directive(state) is None


@pytest.mark.parametrize("stop", [
    "Leave it unconfirmed.",
    "I do not want to share my own email.",
    "No thanks. Goodbye.",
])
def test_paused_confirmation_survives_later_turns_without_erasing_evidence(stop):
    state = update_state_from_user_turn(CallState(), "My email is anna@example.com")
    state = update_state_from_user_turn(state, stop, readback_issued=True)
    assert state.email == "anna@example.com" and not state.email_confirmed
    assert state.contact_capture_paused and state.active_contact_kind is None
    assert turn_directive(state) is None
    assert 'Say EXACTLY:' not in compose_system_prompt("BASE", state)
    state = update_state_from_agent_turn(state, "Please repeat your email.")
    state = update_state_from_user_turn(state, "Goodbye.")
    assert state.email == "anna@example.com" and state.active_contact_kind is None
    assert turn_directive(state) is None


def test_caller_can_resume_paused_capture_by_volunteering_a_new_value():
    state = update_state_from_user_turn(CallState(), "My email is anna@example.com")
    state = update_state_from_user_turn(state, "Leave it unconfirmed.")
    state = update_state_from_user_turn(state, "Actually my email is anna.service@example.com")
    assert not state.contact_capture_paused
    assert state.email == "anna.service@example.com" and not state.email_confirmed
    assert turn_directive(state) is None
    assert "anna.service@example.com" in compose_system_prompt("BASE", state)


def test_same_turn_literal_capture_uses_exact_address_and_stays_unconfirmed():
    state = update_state_from_user_turn(CallState(), "My email is anna.support@example.com. Please take it down.")
    assert state.email == "anna.support@example.com" and not state.email_confirmed
    assert turn_directive(state) is None
    assert '"value": "anna.support@example.com"' in compose_system_prompt("BASE", state)


def test_goodbye_preserves_unconfirmed_evidence_but_disowning_revokes_it():
    original = update_state_from_user_turn(CallState(), "My email is anna@example.com")
    for utterance, withdrawn in [("Goodbye.", False), ("That email is not mine.", True)]:
        state = update_state_from_user_turn(original, utterance)
        session = SimpleNamespace(captured_slots=state,
            _lead_capture_written={"email": ("anna@example.com", False)})
        assert bool(pending_contact_revocations(session)) is withdrawn


@pytest.mark.asyncio
async def test_native_pending_contact_survives_conversation_until_model_records_correction():
    from tests.unit.test_ag05_native_contact_revision import replay, record
    r = replay("openai")
    await r.step({"kind": "caller", "text": "My email is anna@example.com"})
    await record(r, "anna@example.com")
    pending = r.session.captured_slots.email_capture
    await r.step({"kind": "caller", "text": "Leave it unconfirmed."})
    await r.step({"kind": "response", "text": "Okay. What would you like to discuss?"})
    assert r.session.captured_slots.email_capture is pending
    assert not r.session.captured_slots.email_confirmed
    await r.step({"kind": "caller", "text": "Actually my email is anna.service@example.com"})
    assert r.session.captured_slots.email == "anna@example.com"
    await record(r, "anna.service@example.com")
    assert r.session.captured_slots.email == "anna.service@example.com"
    assert not r.session.captured_slots.email_confirmed
    assert not any("BACKEND CONTACT MODE" in str(event) for event in r.socket.sent)
