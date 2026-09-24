"""The agent must not ask a question and hang up in the same turn.

The [[END_CALL]] sentinel path honoured a model hangup unconditionally (only a
wrong-person turn was exempt), while the JSON end-session path already required
the caller to have finished. In the 30 days to 2026-09-23 it hung up straight
after asking the caller something.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.services.end_session_action import agent_left_a_question_open


@pytest.mark.parametrize(
    "agent_line",
    [
        "Mike at example dot com — right?",                                    # 35d3fd2f
        "Right — pulling a report at day-end. Does that process ever hold you up?",  # 3aae86c6
        "When's a good time to call back?",                                    # 77531765
        "Got it. We'll send the sample. Anything else I can help with?",
        "Could I confirm the best phone number to reach you? [[END_CALL]]",
        'She said "is that right?"',
    ],
)
def test_production_hangups_after_a_question_are_recognised(agent_line):
    assert agent_left_a_question_open(agent_line)


@pytest.mark.parametrize(
    "agent_line",
    [
        "Got it. Thanks for your time—have a good day.",   # 06fbbda7, caller: "wasting my time"
        "Fair enough. Thanks for your time — have a good day.",  # 1c3a0c47
        "Thanks for your time. Goodbye.",
        "No problem, I'll take you off our list. Goodbye. [[END_CALL]]",
        "Is that okay? Great, take care then.",             # question mid-turn, closes on a statement
        "",
        None,
    ],
)
def test_legitimate_closes_are_left_alone(agent_line):
    assert not agent_left_a_question_open(agent_line)


def test_the_sentinel_gate_uses_it_and_dnc_and_goodbye_still_win():
    src = (
        Path(__file__).resolve().parents[2]
        / "app" / "domain" / "services" / "voice_pipeline" / "turn_ender.py"
    ).read_text(encoding="utf-8")
    gate = src[src.index("agent_left_a_question_open(response_text)") - 200 :]
    gate = gate[: gate.index("end_call_stripped_question_open")]
    assert "not contains_dnc(full_transcript)" in gate
    assert "not contains_explicit_goodbye(full_transcript)" in gate
    # the new branch sits BEFORE the branch that actually hangs up
    assert src.index("end_call_stripped_question_open") < src.index(
        '"agent_end_call call_id=%s — model requested hangup"'
    )


# --- a contact detail part-way through capture -----------------------------

def _state_after(*turns):
    from app.services.scripts.call_state_tracker import (
        CallState,
        update_state_from_agent_turn,
        update_state_from_user_turn,
    )

    s = CallState()
    for who, text in turns:
        s = update_state_from_agent_turn(s, text) if who == "agent" else update_state_from_user_turn(s, text)
    return s


def test_an_email_being_clarified_is_an_open_capture():
    """2427af7e: caller mid-correction; the agent said 'got it' and hung up."""
    from app.domain.services.end_session_action import contact_capture_open

    state = _state_after(
        ("agent", "Could you share the email address to send the sample to?"),
        ("user", "Yeah. It is, uh, john co at g mail dot com."),
    )
    assert contact_capture_open(state)


def test_an_email_read_back_but_unconfirmed_is_an_open_capture():
    from app.domain.services.end_session_action import contact_capture_open

    state = _state_after(("user", "my email is bob at gmail dot com"))
    assert contact_capture_open(state)


def test_no_capture_and_a_confirmed_capture_are_both_closed():
    from app.domain.services.end_session_action import contact_capture_open
    from app.services.scripts.call_state_tracker import CallState

    assert not contact_capture_open(CallState())
    assert not contact_capture_open(None)
    state = _state_after(("user", "my email is bob at gmail dot com"))
    from dataclasses import replace

    from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

    confirmed = replace(
        state,
        email_capture=replace(state.email_capture, status=CaptureStatus.CONFIRMED),
    )
    assert not contact_capture_open(confirmed)


def test_the_gate_holds_the_call_while_a_capture_is_open():
    src = (
        Path(__file__).resolve().parents[2]
        / "app" / "domain" / "services" / "voice_pipeline" / "turn_ender.py"
    ).read_text(encoding="utf-8")
    gate = src[src.index("agent_left_a_question_open(response_text)") - 120 :]
    gate = gate[: gate.index("end_call_stripped_question_open")]
    assert "contact_capture_open(" in gate
    assert "not contains_dnc(full_transcript)" in gate


# --- the agent's first reply is never a close --------------------------------

def _turn_ender_src():
    return (
        Path(__file__).resolve().parents[2]
        / "app" / "domain" / "services" / "voice_pipeline" / "turn_ender.py"
    ).read_text(encoding="utf-8")


def test_a_first_reply_hangup_is_held():
    """7a690f74, the 10-second drop: caller "Who's this?" -> agent "Sarah here
    from Dojo." -> hangup on turn 0. Both turn-0 model hangups in 30 days were
    wrong; every legitimate close was turn 3 or later."""
    src = _turn_ender_src()
    gate = src[src.index("agent_left_a_question_open(response_text)") - 120 :]
    gate = gate[: gate.index("end_call_stripped_question_open")]
    assert 'getattr(session, "turn_id", None) == 0' in gate


def test_a_machine_still_ends_on_the_first_reply():
    src = _turn_ender_src()
    gate = src[src.index('getattr(session, "turn_id", None) == 0') :][:400]
    assert '"_amd_voicemail"' in gate
    assert '"_machine_screening"' in gate


def test_turn_id_is_read_before_it_is_incremented():
    """The first reply must still be turn 0 when the gate runs."""
    src = _turn_ender_src()
    assert src.index('getattr(session, "turn_id", None) == 0') < src.index(
        "session.increment_turn()"
    )


@pytest.mark.parametrize(
    "agent_line",
    [
        # Live call d1121622 (2026-09-24 11:12:13): hung up on this.
        "Got it. Any recent changes — like new locations, more online orders, "
        "or a different EPOS?...I’m sorry — could you repeat that?...",
        "Is now a good time?…",
        "Could you repeat that? ...",
    ],
)
def test_a_question_followed_by_an_ellipsis_is_still_open(agent_line):
    from app.domain.services.end_session_action import agent_left_a_question_open

    assert agent_left_a_question_open(agent_line)


def test_a_statement_ending_in_an_ellipsis_is_not_a_question():
    from app.domain.services.end_session_action import agent_left_a_question_open

    assert not agent_left_a_question_open("Thanks for your time, take care...")
