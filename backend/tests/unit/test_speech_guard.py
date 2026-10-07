"""speech_guard.py: what is replaced, and just as importantly what is not."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.speech_guard import (
    RELATIONSHIP_ACK_LINE,
    UNBACKED_ACTION_LINE,
    caller_denied_relationship,
    guard_spoken_sentence,
)


def _session(*, results=None, history=()):
    return SimpleNamespace(_voice_action_results=results or {}, conversation_history=list(history))


@pytest.mark.parametrize("sentence", [
    "I have sent the email.",
    "I've just emailed it over to you.",
    "We've sent you the link.",
    "I sent you a confirmation.",
    "I've booked you in for Tuesday at ten.",
    "We have scheduled the callback.",
    "I've transferred your request.",
    "I've passed your details on to the team.",
])
def test_unbacked_completed_action_claims_are_replaced(sentence):
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE


@pytest.mark.parametrize("sentence", [
    "We sent you a quote last month.",              # history, not a claim about now
    "I can send that over after the call if you like.",
    "Would you like me to book a slot for Tuesday?",
    "Shall I pass your details on to the team?",
    "The office opens at nine.",
    "Have you been sent the brochure yet?",
])
def test_ordinary_sentences_pass_through(sentence):
    assert guard_spoken_sentence(_session(), sentence) == sentence


def test_a_confirmed_action_may_be_reported():
    done = _session(results={"send_email": {"action": "send_email", "confirmation_allowed": True}})
    assert guard_spoken_sentence(done, "I have sent the email.") == "I have sent the email."


def test_a_failed_or_unconfirmed_action_may_not_be_reported():
    failed = _session(results={"send_email": {"action": "send_email", "status": "execution_failed",
                                              "confirmation_allowed": False}})
    assert guard_spoken_sentence(failed, "I have sent the email.") == UNBACKED_ACTION_LINE


def _caller(text):
    return Message(role=MessageRole.USER, content=text)


@pytest.mark.parametrize("text", [
    "I am not your customer.",
    "I'm not a customer of yours.",
    "We're not one of your clients.",
    "I've never been your customer.",
    "I don't have an account with you.",
])
def test_explicit_denials_are_recognised(text):
    assert caller_denied_relationship([_caller(text)])


@pytest.mark.parametrize("text", [
    "Yes, I'm a customer.",
    "I'm not sure, maybe.",
    "Not now, thanks.",
])
def test_other_replies_are_not_denials(text):
    assert not caller_denied_relationship([_caller(text)])


def test_agent_lines_never_count_as_a_caller_denial():
    agent = Message(role=MessageRole.ASSISTANT, content="I am not your customer.")
    assert not caller_denied_relationship([agent])


def test_relationship_claim_is_replaced_only_after_a_denial():
    claim = "As our existing customer, your account is ready."
    assert guard_spoken_sentence(_session(), claim) == claim
    denied = _session(history=[_caller("I am not your customer.")])
    assert guard_spoken_sentence(denied, claim) == RELATIONSHIP_ACK_LINE
