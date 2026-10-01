"""Action confirmations bind current caller/contact parameters, not model intent."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.action_execution import _confirmed, _parameters, _callback_time, _capabilities
from app.domain.services.voice_pipeline.contact_capture import ContactCaptureState, CaptureStatus
from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.voice_action_config import normalize_action_config


def test_bare_yes_requires_completed_current_proposal_and_new_turn():
    proposal = {"turn": 1, "summary": "send the email to caller@example.test"}
    session = SimpleNamespace(turn_id=1, _voice_action_delivered_text=proposal["summary"])
    assert not _confirmed(session, proposal, "yes")
    session.turn_id = 2
    assert _confirmed(session, proposal, "yes")
    session._voice_action_delivered_text = "An unrelated question."
    assert not _confirmed(session, proposal, "yes")
    assert _confirmed(session, proposal, "yes, send the email to caller@example.test")
    assert not _confirmed(session, proposal, "yes, send the email to other@example.test")
    assert not _confirmed(session, proposal, "No, don't send the email to caller@example.test")


def test_contact_correction_changes_action_parameters_and_revision():
    capture = ContactCaptureState(kind="email", status=CaptureStatus.CONFIRMED, normalized_value="caller@example.test",
        confirmed_at=datetime.now(timezone.utc))
    session = SimpleNamespace(captured_slots=SimpleNamespace(email_capture=capture))
    context = {"brief": {"email_action": {"subject": "Details", "body": "Approved text"}}}
    original, _ = _parameters(session, context, "send_email", {})
    session.captured_slots.email_capture = replace(capture,normalized_value="corrected@example.test", confirmed_at=datetime.now(timezone.utc))
    corrected, _ = _parameters(session, context, "send_email", {})
    assert original != corrected
    with pytest.raises(ValueError, match="differs"):
        _parameters(session, context, "send_email", {"recipient":"caller@example.test"})
    session.captured_slots.email_capture = replace(capture,status=CaptureStatus.AWAITING_CONFIRMATION)
    with pytest.raises(ValueError, match="Confirm"):
        _parameters(session, context, "send_email", {})


@pytest.mark.parametrize("arguments", [
    {"requested_time":"Thursday","timezone":"Europe/London"},
    {"requested_time":"2026-10-25T01:30:00","timezone":"Europe/London"},
    {"requested_time":"2026-10-25T01:30:00+05:00","timezone":"Europe/London"},
    {"requested_time":"2026-10-25T01:30:00","timezone":"unknown"},
    {"requested_time":"2000-01-01T13:00:00+00:00","timezone":"UTC"},
])
def test_ambiguous_or_mismatched_callback_time_never_schedules(arguments):
    with pytest.raises(ValueError):
        _callback_time(arguments)


def test_explicit_callback_offset_and_zone_are_preserved():
    when = datetime.now(timezone.utc) + timedelta(days=1)
    actual, zone, _ = _callback_time({"requested_time":when.isoformat(),"timezone":"UTC"})
    assert datetime.fromisoformat(actual) == when and zone == "UTC"


def test_inbound_or_opted_out_contact_has_no_outbound_callback_capability():
    context = {"brief":{"approved_next_actions":["schedule_callback"]}, "email_connected":False,
        "direction":"inbound","campaign_status":"active","phone_number":"+14155552671", "lead_id":"test", "callback_worker_ready":True}
    assert "schedule_callback" not in _capabilities(context)
    context.update(direction="outbound",do_not_call=True)
    assert "schedule_callback" not in _capabilities(context)


def test_provider_acceptance_does_not_allow_claiming_email_delivery():
    guard = LLMGuardrails()
    result = {"send_email":{"success":True,"confirmation_allowed":True,"status":"provider_accepted"}}
    assert not guard.validate_response("The email was delivered to your inbox.",action_results=result)[0]
    assert guard.validate_response("The email was sent.",action_results=result)[0]


def test_form_destination_and_supported_fields_are_validated():
    with pytest.raises(ValueError):
        normalize_action_config({"form_action":{"name":"Form","recipient":"https://example.test/hook","subject":"Details","fields":["email"]}})
    with pytest.raises(ValueError):
        normalize_action_config({"form_action":{"name":"Form","recipient":"forms@example.test","subject":"Details","fields":["invented_payment"]}})
