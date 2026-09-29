"""The agent owns understanding the email; the caller's yes to a read-back owns correctness.

Browser test call 56578fa2 (2026-09-29, "Estimation new"): the caller said
"Allstate estimation at Gmail dot com". The parser was unsure (one word or a
dot?), the agent guessed "allstate dot estimation", the caller corrected it
over four turns, the agent finally read back "allstateestimation at gmail dot
com" and the caller said "Yeah. Perfect." -- and nothing was confirmed:

* the read-back reader turned "...gmail dot com. Is that correct?" into
  "gmail.com.is", and refused a read-back that named two addresses;
* the first read-back was pinned and every later correction was ignored.

Now the agent's LATEST read-back is the value on the table, and a clear yes to
it confirms exactly that value. Nothing is persisted before that yes.
"""
from __future__ import annotations

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import turn_runner as tr
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.spoken_email_normalizer import extract_email_from_agent_readback


# ── reading the address out of the agent's read-back ──────────────────────

@pytest.mark.parametrize("turn,expected", [
    # The sentence after the address is not part of it (was "gmail.com.is").
    ("Let me confirm that — allstate dot estimation at gmail dot com. Is that correct?",
     "allstate.estimation@gmail.com"),
    ("Allstate dot state dot estimation at gmail dot com. Is that correct?",
     "allstate.state.estimation@gmail.com"),
    # Two addresses in one read-back: the one next to the question counts.
    ("Allstate estimation at gmail dot com — so allstateestimation at gmail dot com. Is that correct?",
     "allstateestimation@gmail.com"),
    # Letter-by-letter spelling and "all one word" are how people read it back.
    ("So that's a-l-l-s-t-a-t-e-estimation, all one word, at gmail dot com. Is that right?",
     "allstateestimation@gmail.com"),
    ("So that's allstateestimation at gmail dot com, no dots — is that right?",
     "allstateestimation@gmail.com"),
    ("Thanks, bob at gmail dot com, correct?", "bob@gmail.com"),
])
def test_the_read_back_address_is_read_correctly(turn, expected):
    assert extract_email_from_agent_readback(turn) == expected


@pytest.mark.parametrize("turn", [
    "Should I reach you at your gmail dot com address?",
    "You work at microsoft and it's bob at gmail dot com, right?",
    "I'll send it to all state estimation at gmail dot com.",  # no question
])
def test_sentences_that_are_not_a_read_back_give_nothing(turn):
    assert extract_email_from_agent_readback(turn) is None


# ── the value on the table follows the latest read-back ───────────────────

def _history(*lines):
    return [Message(role=MessageRole.ASSISTANT if who == "agent" else MessageRole.USER, content=text)
            for who, text in lines]


def test_each_new_read_back_replaces_an_unconfirmed_one():
    state = CallState(email="allstate.estimation@gmail.com", email_confirmed=False)
    history = _history(
        ("agent", "Allstate estimation at gmail dot com — so allstateestimation at gmail dot com. Is that correct?"),
        ("user", "Yeah. Perfect."),
    )
    pending, read_back = tr.email_on_the_table(state, "Yeah. Perfect.", history)
    assert pending.email == "allstateestimation@gmail.com"
    assert read_back is True


def test_a_confirmed_email_is_never_replaced_by_a_later_read_back():
    state = CallState(email="bob@gmail.com", email_confirmed=True)
    history = _history(("agent", "So that's rob at gmail dot com, right?"), ("user", "yes"))
    pending, _ = tr.email_on_the_table(state, "yes", history)
    assert pending.email == "bob@gmail.com"


def test_a_caller_correction_wins_and_the_wrong_read_back_is_never_confirmed():
    turns = [
        ("agent", "What is the best email?"),
        ("user", "rob at gmail dot com"),
        ("agent", "So that is rob at gmail dot com, right?"),
        ("user", "no, it is bob at gmail dot com"),
    ]
    assert _replay(turns).email_confirmed is False
    state = _replay(turns + [("agent", "So that is bob at gmail dot com, right?"), ("user", "yes")])
    assert (state.email, state.email_confirmed) == ("bob@gmail.com", True)


# ── the whole call, replayed ──────────────────────────────────────────────

CALL_56578FA2 = [
    ("agent", "Perfect. What’s the best email address for you?"),
    ("user", "Allstate estimation at Gmail dot com."),
    ("agent", "Let me confirm that — allstate dot estimation at gmail dot com. Is that correct?"),
    ("user", "dot between all and state."),
    ("agent", "Allstate dot state dot estimation at gmail dot com. Is that correct?"),
    ("user", "eight estimation at g mail dot com."),
    ("agent", "Sorry — could you repeat the email address please?"),
    ("user", "Allstate estimation at Gmail dot com."),
    ("agent", "Let me confirm — allstate dot estimation at gmail dot com. Is that correct?"),
    ("user", "Remove the dot."),
    ("agent", "Allstate estimation at gmail dot com — so allstateestimation at gmail dot com. Is that correct?"),
    ("user", "Yeah. Perfect."),
]


def _replay(turns):
    history: list[Message] = []
    state = CallState()
    for who, text in turns:
        if who == "agent":
            history.append(Message(role=MessageRole.ASSISTANT, content=text))
            state = update_state_from_agent_turn(state, text)
            continue
        history.append(Message(role=MessageRole.USER, content=text))
        pending, read_back = tr.email_on_the_table(state, text, history)
        gate = bool(
            pending.email and not pending.email_confirmed and read_back
            and not tr._is_email_correction(text, pending.email)
        )
        verdict = tr._classify_core_confirmation(text) if gate else None
        state = update_state_from_user_turn(
            pending, text, readback_issued=read_back, confirmation_verdict=verdict,
        )
    return state


def test_the_live_call_now_ends_with_the_email_confirmed():
    state = _replay(CALL_56578FA2)
    assert state.email == "allstateestimation@gmail.com"
    assert state.email_confirmed is True


def test_nothing_is_confirmed_before_the_callers_yes():
    state = _replay(CALL_56578FA2[:-1])
    assert state.email_confirmed is False


def test_the_short_path_one_question_then_yes():
    state = _replay([
        ("agent", "What's the best email address for you?"),
        ("user", "Allstate estimation at Gmail dot com."),
        ("agent", "Is that allstateestimation all one word, or allstate dot estimation, at gmail dot com?"),
        ("user", "One word."),
        ("agent", "So that's allstateestimation at gmail dot com. Is that right?"),
        ("user", "Yes."),
    ])
    assert (state.email, state.email_confirmed) == ("allstateestimation@gmail.com", True)


# ── the agent is told to ASK, not guess ───────────────────────────────────

def test_an_ambiguous_address_gets_a_plain_either_or_question():
    state = update_state_from_agent_turn(CallState(), "What is the best email address for you?")
    state = update_state_from_user_turn(state, "Allstate estimation at Gmail dot com.")
    prompt = state.email_capture.clarification_prompt
    assert "Do not guess" in prompt
    assert '"Is that allstateestimation all one word, or allstate dot estimation, at gmail dot com?"' in prompt
