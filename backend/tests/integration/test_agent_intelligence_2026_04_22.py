"""Replay of the 2026-04-22 live call user turns.

Asserts:
  1. By turn 8 (caller gives email first time), composed prompt contains
     a canonical email.
  2. By turn 9 (caller says 'call me on Sunday'), composed prompt
     contains both email and follow-up.
  3. By turn 10 (caller repeats email), composed prompt STILL contains
     the email (sticky semantics, not wiped by the repeat).
  4. The 'do not re-ask' rule is present whenever an email is captured.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are Alex. Be brief."

_USER_TURNS = [
    "Yes. You can proceed.",                                              # 1
    "do you have something for the estimation?",                          # 2
    "Yes. I have multiple type of projects.",                             # 3
    "Currently, I don't have any priority at home, so I just want you "
    "to help me out.",                                                    # 4
    "So can you share your previous work documents?",                     # 5
    "Can you send me the non coded samples of your blood. I can give "
    "you my address.",                                                    # 6
    "It's Victor Street one seventy seven, apartment number one "
    "thirty eight.",                                                      # 7
    "Yeah. It's Cloud State estimation at g mail dot com.",               # 8
    "Next follow-up, you can call me on Sunday.",                         # 9
    "Yeah. It's all state estimation at the rate Gmail dot com.",         # 10
    "Perfect.",                                                           # 11
]


def _replay(n: int) -> CallState:
    st = CallState()
    for t in _USER_TURNS[:n]:
        st = update_state_from_user_turn(st, t)
    return st


def test_email_captured_by_turn_8():
    st = _replay(8)
    assert st.email is not None
    composed = compose_system_prompt(BASE, st)
    assert st.email in composed
    assert "do not ask" in composed.lower() or "do not re-ask" in composed.lower()


def test_follow_up_captured_by_turn_9():
    st = _replay(9)
    assert st.email is not None
    assert st.follow_up and "sunday" in st.follow_up.lower()
    composed = compose_system_prompt(BASE, st)
    assert "sunday" in composed.lower()


def test_email_sticky_across_repeat_at_turn_10():
    st_before = _replay(9)
    st_after = _replay(10)
    # Either unchanged or overwritten with an equivalent normalized form,
    # but NEVER wiped.
    assert st_after.email is not None
    assert "@gmail.com" in st_after.email
    # Follow-up survived the repeat.
    assert st_after.follow_up == st_before.follow_up


def test_bidding_captured_by_turn_3():
    st = _replay(3)
    assert st.bidding_active is True
