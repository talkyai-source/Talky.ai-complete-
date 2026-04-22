"""Tests for the CallState tracker — slots: email, follow_up, project_type,
bidding_active, declined_count.

Regression-anchored on the 2026-04-22 live call where the agent failed to
notice a captured email and looped asking for it."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)


def test_empty_state_is_empty():
    st = CallState()
    assert st.email is None
    assert st.follow_up is None
    assert st.bidding_active is None
    assert st.declined_count == 0


def test_captures_email_once():
    st = CallState()
    st = update_state_from_user_turn(st, "my email is john@gmail.com")
    assert st.email == "john@gmail.com"


def test_captures_spoken_email():
    st = CallState()
    st = update_state_from_user_turn(
        st, "all state estimation at the rate gmail dot com"
    )
    assert st.email == "allstateestimation@gmail.com"


def test_does_not_overwrite_previously_captured_email_with_garbage():
    """Once we have a valid email, later noise does NOT clear it — this is
    what broke the 2026-04-22 call when the caller said a street address."""
    st = CallState(email="known@example.com")
    st = update_state_from_user_turn(st, "Victor Street 177, apartment 138")
    assert st.email == "known@example.com"


def test_captures_follow_up_day():
    st = CallState()
    st = update_state_from_user_turn(st, "you can call me on Sunday")
    assert st.follow_up and "sunday" in st.follow_up.lower()


def test_captures_bidding_yes():
    st = CallState()
    st = update_state_from_user_turn(
        st, "yes I have multiple projects I'm bidding on"
    )
    assert st.bidding_active is True


def test_captures_bidding_no():
    st = CallState()
    st = update_state_from_user_turn(st, "no not bidding on anything right now")
    assert st.bidding_active is False


def test_decline_increments():
    st = CallState()
    st = update_state_from_user_turn(st, "not interested")
    assert st.declined_count == 1
    st = update_state_from_user_turn(st, "I don't want this")
    assert st.declined_count == 2


def test_2026_04_22_regression_sequence():
    """Replays the real call's user turns in order; email must be captured
    by turn 4 and NEVER lost."""
    turns = [
        "Yes. You can proceed.",
        "do you have something for the estimation?",
        "Yes. I have multiple type of projects.",
        "Currently, I don't have any priority at home, so I just want you to help me out.",
        "So can you share your previous work documents?",
        "Can you send me the non coded samples of your blood. I can give you my address.",
        "It's Victor Street one seventy seven, apartment number one thirty eight.",
        "Yeah. It's Cloud State estimation at g mail dot com.",
        "Next follow-up, you can call me on Sunday.",
        "Yeah. It's all state estimation at the rate Gmail dot com.",
        "Perfect.",
    ]
    st = CallState()
    for utterance in turns:
        st = update_state_from_user_turn(st, utterance)
    assert st.email is not None
    assert st.email.endswith("@gmail.com")
    assert st.follow_up and "sunday" in st.follow_up.lower()
    assert st.bidding_active is True
