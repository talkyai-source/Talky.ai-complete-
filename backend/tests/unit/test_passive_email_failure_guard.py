"""Honest passive failure must survive the same output gate as false success."""
from pathlib import Path

import pytest

from app.domain.services.llm_guardrails import LLMGuardrails
from tests.qualification.ag04_native import NativeReplay, _corpus


ROOT = Path(__file__).resolve().parents[3]
FAILURE = {
    "success": False, "status": "failed", "confirmation_allowed": False,
    "provider": "synthetic-email", "request_id": "synthetic-failed-request-1",
    "message_id": None, "message": "Synthetic provider rejected the send request.",
}


@pytest.mark.parametrize("results", [{}, {"send_email": FAILURE}])
@pytest.mark.parametrize("modal", ["could not", "couldn't", "cannot", "can't", "can not"])
def test_explicit_passive_email_failure_is_not_a_completion_claim(modal, results):
    assert LLMGuardrails().validate_response(
        f"The email {modal} be sent.", action_results=results,
    ) == (True, None)


@pytest.mark.parametrize("text", [
    "The callback was not scheduled or confirmed.",
    "The form was not submitted or filed.",
    "The email was not sent or delivered.",
])
def test_existing_coordinated_failures_remain_allowed(text):
    assert LLMGuardrails().validate_response(text, action_results={}) == (True, None)


@pytest.mark.parametrize("text", [
    "The email was sent to you.",
    "The email was sent but the email could not be delivered.",
    "The email was sent but could not be delivered.",
    "The email was sent if it could not be delivered.",
    'The email was sent, not "could not be sent".',
    "The email could not be sent but the email was sent now.",
    "The email could not be sent earlier, but the email was sent now.",
    "The email could not be sent. I've sent it now.",
    'The old error said "the email could not be sent", but the email was sent now.',
    'The old error said "the email could not be sent"; I have sent it now.',
    "If the email could not be sent, I would tell you. The email was sent.",
    "If the email could not be sent, the details were sent to you instead.",
    "The email could not be sent, but I've booked the callback.",
])
def test_negative_quoted_or_hypothetical_failure_cannot_license_other_completion(text):
    valid, reason = LLMGuardrails().validate_response(text, action_results={"send_email": FAILURE})
    assert valid is False
    assert reason.startswith(("action_failed:", "unconfirmed_action:"))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_actual_native_failed_action_recovers_with_exact_passive_failure(provider):
    case = {
        "id": "passive-email-failure", "semantic_ids": [], "expect": {},
        "synthetic_action_results": {"send_email": FAILURE},
        "steps": [
            {"kind": "caller", "item": "request", "text": "Please email the details."},
            {"kind": "tool", "name": "send_email", "arguments": {}},
            {"kind": "response", "text": "The email was sent to you."},
            {"kind": "response", "text": "The email could not be sent."},
        ],
    }
    row = await NativeReplay(case, provider, _corpus(ROOT)).run()
    assert row["submitted_speech"] == ["The email could not be sent."]
    assert row["observed"]["repair_requests"] == 1
    assert row["observed"]["failure"] is False
    assert len(row["effects"]["executor_attempts"]) == 1
    receipt = row["effects"]["executor_attempts"][0]["receipt"]
    assert receipt == {"version": 1, "action": "send_email", **FAILURE}
    assert row["effects"]["recorded_results"]["send_email"] == receipt
    assert row["effects"]["accepted"] == 0
    assert all(turn["content"] != "The email was sent to you." for turn in row["history"])
