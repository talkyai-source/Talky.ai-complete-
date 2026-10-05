"""An unresolved external result proves neither completion nor nonexecution."""

import pytest

from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.voice_pipeline.action_tools import safe_failure_speech


def receipt(action="send_email", status="unknown"):
    return {
        "version": 1,
        "action": action,
        "success": False,
        "status": status,
        "confirmation_allowed": False,
        "request_id": "synthetic-original-request",
        "message_id": None,
        "message": "The outcome is not confirmed. Review before repeating.",
    }


@pytest.mark.parametrize("status", ["unknown", "in_progress"])
@pytest.mark.parametrize(
    "text",
    [
        "The email was not sent.",
        "The email could not be sent.",
        "The email cannot be delivered.",
        "I haven't sent the email.",
        "The email failed.",
        "The email has failed.",
        "The email was not sent. Please ask me to send it again.",
        "Please send the email again.",
        "I will resend the email now.",
        "Please retry sending the email.",
        "Please retry the email.",
    ],
)
def test_unknown_email_cannot_be_described_as_failed_or_safe_to_repeat(status, text):
    result = receipt(status=status)
    before = dict(result)
    assert LLMGuardrails().validate_response(text, action_results={"send_email": result}) == (
        False,
        f"action_failed:send_email:{status}",
    )
    assert result == before


@pytest.mark.parametrize("status", ["unknown", "in_progress"])
@pytest.mark.parametrize(
    "action,text",
    [
        ("submit_form", "The form was not submitted."),
        ("submit_form", "Please resubmit the form."),
        ("schedule_callback", "The callback was not scheduled."),
        ("schedule_callback", "Please book the callback again."),
        ("transfer_call", "The transfer was not started."),
        ("transfer_call", "Please retry the transfer."),
    ],
)
def test_other_named_unresolved_actions_keep_the_same_truth_boundary(status, action, text):
    assert LLMGuardrails().validate_response(
        text, action_results={action: receipt(action, status)}
    ) == (
        False,
        f"action_failed:{action}:{status}",
    )


@pytest.mark.parametrize("status", ["unknown", "in_progress"])
@pytest.mark.parametrize(
    "text",
    [
        "I can't confirm whether the email was sent.",
        "I cannot confirm whether the email was sent.",
        "I can't confirm that the email was sent.",
        "I can't confirm whether the email was sent, but I can explain the next step.",
        "The sending outcome is unknown. It needs checking before another attempt.",
        "Please check the existing request before trying again.",
        "Do not resend the email until the existing request has been checked.",
        "Please do not send the email again.",
        "The phrase 'resend the email' is an instruction, not an outcome.",
        "The phrase 'resend the email, and resend the email' is not an outcome.",
        "The caller said 'resend the email and retry the email'.",
        "Do not resend the email, and please do not send the email again.",
        "Do not resend the email, and do not retry the email.",
        "If the email failed, its saved receipt would need review.",
        "The form was not submitted.",
        "Your opening hours are nine to five.",
        "Please try again now.",
        "Please retry your password.",
    ],
)
def test_uncertainty_reconciliation_and_unrelated_speech_are_preserved(status, text):
    assert LLMGuardrails().validate_response(
        text, action_results={"send_email": receipt(status=status)}
    ) == (True, None)


@pytest.mark.parametrize("status", ["unknown", "in_progress"])
def test_existing_safe_fallback_is_still_admitted(status):
    result = receipt(status=status)
    text = safe_failure_speech("send_email", result)
    assert (
        text
        == "I couldn't confirm the outcome of that request. It needs to be checked before trying again."
    )
    assert LLMGuardrails().validate_response(text, action_results={"send_email": result}) == (
        True,
        None,
    )


@pytest.mark.parametrize(
    "text",
    [
        "The email was not sent.",
        "The email could not be sent.",
        "I haven't sent the email.",
        "The email failed.",
        "The email was not sent or delivered.",
    ],
)
@pytest.mark.parametrize("results", [{}, {"send_email": receipt(status="failed")}])
def test_no_unresolved_receipt_does_not_expand_old_failure_policy(text, results):
    assert LLMGuardrails().validate_response(text, action_results=results) == (True, None)


@pytest.mark.parametrize(
    "text",
    [
        "The email was sent to you.",
        "I can't confirm whether the email was sent. The email was sent now.",
        "I can't confirm whether the email was sent, but the email was sent now.",
        "I can't confirm that the email was sent, but the email was sent now.",
        "I cannot confirm whether the email was sent and the email was sent now.",
        "I cannot confirm whether the email was sent; however, I have sent it now.",
        "I cannot confirm whether the email failed, so resend the email.",
        "The caller said 'resend the email', and I will resend the email now.",
        "The caller said 'resend the email' and I will resend the email now.",
        "The email could not be sent, but the email was sent now.",
        "The old error said 'the email could not be sent'; the email was sent now.",
        "If the email could not be sent, the details were sent to you instead.",
        "Do not resend the email, but please send the email again.",
        "The sending outcome is unknown. Please resend the email.",
    ],
)
def test_uncertainty_or_negation_cannot_license_a_separate_claim_or_repeat(text):
    assert LLMGuardrails().validate_response(text, action_results={"send_email": receipt()}) == (
        False,
        "action_failed:send_email:unknown",
    )


@pytest.mark.parametrize("status", [None, "unknown", "failed"])
@pytest.mark.parametrize(
    "action,text",
    [
        ("schedule_callback", "The callback, as requested, was scheduled."),
        ("send_email", "The email, as requested, was sent."),
        ("submit_form", "The form, as requested, was submitted."),
        ("send_email", "I have sent the price and payment information."),
    ],
)
def test_complete_positive_predicates_survive_embedded_clause_punctuation(status, action, text):
    results = {} if status is None else {action: receipt(action, status)}
    valid, reason = LLMGuardrails().validate_response(text, action_results=results)
    assert valid is False
    assert reason == (
        f"unconfirmed_action:{action}" if status is None else f"action_failed:{action}:{status}"
    )


@pytest.mark.parametrize("status", [None, "unknown", "failed"])
@pytest.mark.parametrize(
    "text",
    [
        "I cannot confirm whether the email, as requested, was sent.",
        "I cannot confirm whether the callback, as requested, was scheduled.",
        "I cannot confirm whether the form, as requested, was submitted.",
        "The caller said 'the email, as requested, was not sent'.",
    ],
)
def test_whole_predicate_uncertainty_and_quoted_failure_remain_truthful(status, text):
    results = (
        {}
        if status is None
        else {
            action: receipt(action, status)
            for action in (
                "send_email",
                "schedule_callback",
                "submit_form",
            )
        }
    )
    assert LLMGuardrails().validate_response(text, action_results=results) == (True, None)


@pytest.mark.parametrize(
    "action,text",
    [
        ("schedule_callback", "The callback, as requested, was not scheduled."),
        ("send_email", "The email, as requested, was not sent."),
        ("submit_form", "The form, as requested, was not submitted."),
        ("send_email", "I have not sent the price and payment information."),
    ],
)
def test_whole_negative_predicate_still_requires_a_definite_outcome(action, text):
    guard = LLMGuardrails()
    assert guard.validate_response(text, action_results={action: receipt(action)}) == (
        False,
        f"action_failed:{action}:unknown",
    )
    assert guard.validate_response(text, action_results={action: receipt(action, "failed")}) == (
        True,
        None,
    )


@pytest.mark.parametrize("results", [{}, {"send_email": receipt(status="failed")}])
def test_modal_failure_cannot_swallow_a_later_delivery_claim(results):
    valid, reason = LLMGuardrails().validate_response(
        "The email could not be sent but delivered.",
        action_results=results,
    )
    assert valid is False
    assert reason.startswith(("unconfirmed_action:send_email", "action_failed:send_email"))
