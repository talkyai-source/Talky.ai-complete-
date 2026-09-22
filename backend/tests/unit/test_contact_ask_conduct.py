"""Answer first, and stop asking for contact details once the caller objects.

Call 2427af7e (2026-09-22). The operator's goal says "capture email on every
call". The agent asked for an email before answering a single question, and
kept asking through three explicit objections:

    "Why you are asking my email?"
    "I haven't asked for that. Why you asking?"
    "That I haven't asked for that, why you directly ask me that."

Nothing recognised the objection, so nothing told the agent to stop. These
tests replay that call's own lines.
"""
from __future__ import annotations

import pytest

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

STOP = "Do NOT ask for their email or phone number again"
ANSWER = "Answer it directly and specifically"


def _after_ask(utterance: str) -> CallState:
    asked = update_state_from_agent_turn(
        CallState(), "Could I get your email to send a sample report?"
    )
    return update_state_from_user_turn(asked, utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "Why you are asking my email?",
        "I haven't asked for that. Why you asking?",
        "That I haven't asked for that, why you directly ask me that.",
        "No. I am not interested in sharing my phone number.",
        "I don't want to give that out",
    ],
)
def test_each_objection_from_production_is_recognised(utterance):
    state = _after_ask(utterance)
    assert state.contact_ask_objections == 1
    assert STOP in compose_system_prompt("BASE", state)


def test_objecting_ends_contact_mode_so_the_next_words_are_not_parsed_as_an_address():
    assert _after_ask("Why you are asking my email?").active_contact_kind is None


def test_a_bare_why_only_counts_straight_after_a_contact_ask():
    """'Why are you asking?' about anything else is not a contact objection."""
    cold = update_state_from_user_turn(CallState(), "why are you asking about my projects")
    assert cold.contact_ask_objections == 0
    assert _after_ask("Why are you asking?").contact_ask_objections == 1


def test_keeping_a_number_private_is_not_declining_the_call():
    """Two of these used to tell the agent to close the call (call cf6bfed1)."""
    state = CallState()
    for _ in range(2):
        state = update_state_from_user_turn(
            state, "No. I am not interested in sharing my phone number."
        )
    assert state.declined_count == 0
    assert "Close politely and end the call" not in compose_system_prompt("BASE", state)


def test_a_real_decline_still_counts():
    state = update_state_from_user_turn(CallState(), "no thanks, not interested")
    assert state.declined_count == 1
    assert state.contact_ask_objections == 0


def test_asking_to_be_sent_something_lifts_the_objection():
    """They did exactly this on 2427af7e after three objections."""
    state = _after_ask("Why you are asking my email?")
    state = update_state_from_user_turn(state, "Can you send that to my email.")
    assert state.contact_ask_objections == 0
    assert STOP not in compose_system_prompt("BASE", state)


@pytest.mark.parametrize(
    "utterance",
    [
        "What is your website?",
        "Okay. Can you share me some sort of your sample and work criteria?",
        "what is the annual review actually",   # STT dropped the question mark
        "How much does it cost?",
    ],
)
def test_a_question_puts_answer_first_at_the_top_of_the_prompt(utterance):
    prompt = compose_system_prompt("BASE", update_state_from_user_turn(CallState(), utterance))
    assert ANSWER in prompt
    assert prompt.index(ANSWER) < prompt.index("BASE")


@pytest.mark.parametrize(
    "utterance",
    [
        "Can you send that to my email.",   # a send request needs an address
        "I work on all across.",
        "Yeah. We have been quite busy.",
    ],
)
def test_statements_and_send_requests_do_not_suppress_the_ask(utterance):
    state = update_state_from_user_turn(CallState(), utterance)
    assert state.caller_asked_question is False
    assert ANSWER not in compose_system_prompt("BASE", state)


def test_answer_first_is_dropped_once_a_contact_detail_is_held():
    state = update_state_from_user_turn(CallState(), "my email is bob at gmail dot com")
    state = update_state_from_user_turn(state, "What else do you need?")
    assert state.email
    assert ANSWER not in compose_system_prompt("BASE", state)


def test_the_whole_call_replays_correctly():
    """The production call, turn by turn, through the new logic."""
    s = CallState()
    s = update_state_from_agent_turn(s, "Could I get your email to send a sample report?")
    s = update_state_from_user_turn(s, "Why you are asking my email?")
    assert STOP in compose_system_prompt("BASE", s)
    s = update_state_from_user_turn(s, "What is your website?")
    assert STOP in compose_system_prompt("BASE", s)
    assert ANSWER in compose_system_prompt("BASE", s)
    s = update_state_from_user_turn(s, "Can you send that to my email.")
    assert STOP not in compose_system_prompt("BASE", s)
    s = update_state_from_agent_turn(s, "Could you share the email address?")
    s = update_state_from_user_turn(s, "Yeah. It is, uh, john co at g mail dot com.")
    prompt = compose_system_prompt("BASE", s)
    assert "johnco@gmail.com or john.co@gmail.com" in prompt
