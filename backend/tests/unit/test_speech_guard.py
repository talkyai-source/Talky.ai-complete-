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


# QA F10-02 (ag04.relationship_reversal, ag04.quoted_relationship): the caller's
# latest own statement counts. A denial followed by "Actually, I am your
# customer." is an affirmation; words the caller quotes are someone else's.
_DENIAL = "I am not your customer."


@pytest.mark.parametrize("history,expected", [
    ([_DENIAL], "denied"),
    ([_DENIAL, "Actually, I am your customer."], "affirmed"),
    ([_DENIAL, "Yes, I'm a customer of yours."], "affirmed"),
    ([_DENIAL, 'The script says "I am your customer".'], "denied"),
    (["Actually, I am your customer.", _DENIAL], "denied"),
    (["What are your opening hours?"], None),
])
def test_the_callers_latest_own_statement_sets_the_relationship(history, expected):
    from app.domain.services.voice_pipeline.speech_guard import caller_relationship

    assert caller_relationship([_caller(text) for text in history]) == expected


def test_a_reversed_denial_lets_the_relationship_be_said_again():
    claim = "As our existing customer, your account is ready."
    reversed_ = _session(history=[_caller(_DENIAL), _caller("Actually, I am your customer.")])
    assert guard_spoken_sentence(reversed_, claim) == claim
    quoted = _session(history=[_caller(_DENIAL), _caller('The script says "I am your customer".')])
    assert guard_spoken_sentence(quoted, claim) == RELATIONSHIP_ACK_LINE


def test_live_state_carries_the_latest_relationship():
    from app.domain.services.voice_pipeline.live_structured_state import (
        reduce_cascaded_session_live_state,
    )

    session = _session(history=[_caller(_DENIAL), _caller("Actually, I am your customer.")])
    assert reduce_cascaded_session_live_state(session).customer_relationship == "affirmed"
    session = _session(history=[_caller(_DENIAL)])
    assert reduce_cascaded_session_live_state(session).customer_relationship == "denied"


# -- QA F10-01: a claim must match the action's real state ---------------------
# The offline replay (scripts.evaluate_ag04_conversations) let these reach the
# caller in all four model profiles: a "sent" or "could not be sent" while the
# email's outcome was unresolved, "delivered to your inbox" when the provider
# had only accepted it, and "I'm transferring you now" with no transfer.

# What the replay fixtures (ag04.action_unknown, ag04.action_in_progress) expect.
UNRESOLVED_ACTION_LINE = "I couldn't confirm the outcome of that request."


def _result(action, status, *, success=False, confirmed=False):
    return {action: {"action": action, "status": status, "success": success,
                     "confirmation_allowed": confirmed}}


_SENT = _result("send_email", "provider_accepted", success=True, confirmed=True)
_FAILED = _result("send_email", "failed")
_UNKNOWN = _result("send_email", "unknown")
_IN_PROGRESS = _result("send_email", "in_progress")


@pytest.mark.parametrize("results", [None, _FAILED])
def test_a_simple_past_send_claim_needs_a_sent_result(results):
    assert guard_spoken_sentence(_session(results=results), "The email was sent to you.") \
        == UNBACKED_ACTION_LINE
    assert guard_spoken_sentence(_session(results=_SENT), "The email was sent to you.") \
        == "The email was sent to you."


@pytest.mark.parametrize("results", [_UNKNOWN, _IN_PROGRESS])
@pytest.mark.parametrize("sentence", [
    "The email was sent to you.",
    "The email could not be sent.",
    "Please send the email again.",
])
def test_an_unresolved_outcome_is_neither_sent_failed_nor_resent(results, sentence):
    assert guard_spoken_sentence(_session(results=results), sentence) == UNRESOLVED_ACTION_LINE


def test_a_failure_or_retry_may_be_said_once_the_failure_is_real():
    for sentence in ("The email could not be sent.", "Please send the email again."):
        assert guard_spoken_sentence(_session(results=_FAILED), sentence) == sentence


@pytest.mark.parametrize("sentence", [
    "The email was delivered to your inbox.",
    "The email has been delivered.",
])
def test_delivery_is_never_confirmed_by_an_accepted_send(sentence):
    assert guard_spoken_sentence(_session(results=_SENT), sentence) \
        == "The provider accepted it for sending, but I can't confirm delivery."
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE
    assert guard_spoken_sentence(_session(results=_UNKNOWN), sentence) == UNRESOLVED_ACTION_LINE


@pytest.mark.parametrize("sentence", ["I'm transferring you now.", "I am connecting you now.",
                                      "Putting you through now."])
def test_a_transfer_in_progress_needs_an_accepted_transfer(sentence):
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE
    accepted = _result("transfer_call", "provider_accepted", success=True)
    assert guard_spoken_sentence(_session(results=accepted), sentence) == sentence
    unknown = _result("transfer_call", "unknown")
    assert guard_spoken_sentence(_session(results=unknown), sentence) == UNRESOLVED_ACTION_LINE


@pytest.mark.parametrize("results", [None, _FAILED, _UNKNOWN, _SENT])
@pytest.mark.parametrize("sentence", [
    "I couldn't confirm the outcome of that request.",
    "It needs to be checked before trying again.",
    "I can't confirm that the email was sent.",
    "The provider accepted the email for sending, but I can't confirm delivery.",
    "The quote was sent last week.",
    "Was the email sent?",
    "I'm not able to transfer you.",
])
def test_truthful_or_historical_lines_pass_whatever_the_state(results, sentence):
    assert guard_spoken_sentence(_session(results=results), sentence) == sentence


# A hedge or a date covers only the claim it governs (security review of the
# first cut: a whole-sentence exemption let an unbacked claim through).
@pytest.mark.parametrize("sentence", [
    "I'm not sure if you got it, but I've sent the email.",
    "I can't confirm the time, but I've booked you in for Tuesday.",
    "I can't confirm the time and I have sent the email.",
    "The email was sent just now.",
    "The email was sent a moment ago.",
    "Last time we spoke I was unsure, but the email was sent to you.",
])
def test_a_hedge_or_date_elsewhere_does_not_excuse_a_claim(sentence):
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE


@pytest.mark.parametrize("sentence", [
    "I'm transferring you now, okay?",
    "Last time you asked for sales, so I'm transferring you now.",
])
def test_a_transfer_claim_is_checked_whatever_surrounds_it(sentence):
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE


def test_a_delivery_claim_dated_just_now_is_still_a_delivery_claim():
    assert guard_spoken_sentence(_session(results=_SENT), "It was delivered a moment ago.") \
        == "The provider accepted it for sending, but I can't confirm delivery."
    assert guard_spoken_sentence(_session(results=_SENT), "The email was sent and delivered to your inbox.") \
        == "The provider accepted it for sending, but I can't confirm delivery."


@pytest.mark.parametrize("sentence", [
    "Your parcel was delivered yesterday.",
    "The quote was sent to you three weeks ago.",
    "I can't confirm that the email was sent and delivered.",
])
def test_an_explicitly_dated_or_hedged_claim_is_not_a_claim_about_now(sentence):
    assert guard_spoken_sentence(_session(), sentence) == sentence
