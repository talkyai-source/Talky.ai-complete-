"""Phone capture must arm on the way the agent actually asks.

Live test 2026-09-23, call 51450718. The agent asked "And a phone number where
we can reach you?"; phone mode never armed, the caller's digits were never
captured, and the model wrote them out itself - "312-207-504-96" for "three one
two zero seven five zero four nine six" (an extra 2) - then confirmed it on the
caller's behalf. The platform's own parser had the right digits all along.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)


@pytest.mark.parametrize(
    "agent_line",
    [
        "And a phone number where we can reach you?",            # 51450718
        "Could I confirm the best phone number to reach you?",   # bf6a092c
        "Could you share a contact phone number, please?",
        "May I have your name and a contact number or email to pass on to the team?",
        "And a phone number or email to follow up?",
        "What is your phone number?",                            # already armed
    ],
)
def test_natural_phone_asks_arm_phone_mode(agent_line):
    assert update_state_from_agent_turn(CallState(), agent_line).active_contact_kind == "phone"


@pytest.mark.parametrize(
    "agent_line",
    [
        "Would you like us to remove your number from any further calls?",
        "How many tenders do you submit each month?",
        "We will call the phone number on file.",   # a statement, not an ask
    ],
)
def test_other_mentions_of_a_number_do_not_arm(agent_line):
    assert update_state_from_agent_turn(CallState(), agent_line).active_contact_kind is None


def _after_the_live_ask(caller_line, region):
    state = update_state_from_agent_turn(
        CallState(), "And a phone number where we can reach you?"
    )
    return update_state_from_user_turn(state, caller_line, phone_region=region)


def test_a_valid_number_is_captured_exactly_for_the_readback():
    state = _after_the_live_ask(
        "Yeah. It's oh seven nine one one one two three four five six.", "GB"
    )
    capture = state.phone_capture
    assert capture is not None
    assert capture.status is CaptureStatus.AWAITING_CONFIRMATION
    # 07700 900xxx is Ofcom's range reserved for fiction and correctly
    # rejected, so a real-format mobile is used here.
    assert capture.normalized_value == "+447911123456"


def test_the_number_from_the_live_call_is_questioned_not_guessed():
    """312 075 0496 is not a valid number in North America (the middle group
    cannot start with 0) or the UK. Before: nothing captured, the model invented
    an 11-digit read-back. Now: the capture exists and asks for a repeat."""
    state = _after_the_live_ask(
        "Yeah. It's three one two zero seven five zero four nine six.", "US"
    )
    capture = state.phone_capture
    assert capture is not None
    assert capture.status is CaptureStatus.INVALID
    assert capture.normalized_value is None
    assert "repeat" in (capture.clarification_prompt or "").lower()


def test_without_a_region_it_asks_for_the_country_code_rather_than_guessing():
    state = _after_the_live_ask(
        "Yeah. It's three one two zero seven five zero four nine six.", None
    )
    assert state.phone_capture.status is CaptureStatus.NEEDS_CLARIFICATION
    assert "country code" in state.phone_capture.clarification_prompt
