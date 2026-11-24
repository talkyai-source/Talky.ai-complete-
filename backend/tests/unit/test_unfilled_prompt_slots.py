"""Campaign guidance with an unfilled [slot] makes the agent speak a blank.

Production, 2026-09-22. A campaign script contained, verbatim:

    THE OPENING - PROFESSIONAL AND DIRECT:
    "Hi there - is that [name]?

Nothing substitutes that slot, so the model did the obedient thing and read the
line with it empty. The first words a real caller heard were "Hi there - is
that ?". Nothing in the product noticed, because an unfilled slot is just text.

Detection is deliberately narrow: it must catch a field a human would try to
SAY, and must not catch a stage direction, which is an instruction to the model
and works exactly as intended.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import find_unfilled_slots


@pytest.mark.parametrize(
    "guidance,expected",
    [
        ("Hi there - is that [name]?", []),
        ("Ask for [first name] and [company].", ["[company]"]),
        ("Confirm [email] before moving on.", ["[email]"]),
        ("Their number is [phone number].", ["[phone number]"]),
        ("Quote [price] only if asked.", ["[price]"]),
        ("Use [first_name] with an underscore too.", []),
        ("Ask for [last name].", ["[last name]"]),
        # Capitalised slots are matched too (the slot of call 98b83aaf was "[First
        # Name]"); the reported text keeps its own capitals.
        ("Ask for [Company] and [Last Name].", ["[Company]", "[Last Name]"]),
        ("Is that [Name] or [First Name] or [full name]?", []),
    ],
)
def test_a_spoken_field_slot_is_caught(guidance, expected):
    assert find_unfilled_slots(guidance) == expected


@pytest.mark.parametrize(
    "guidance",
    [
        "[repeat email slowly] then confirm",          # stage direction
        "[wait for them to finish]",                   # stage direction
        "No slots here at all.",
        "",
        None,
        "Brackets around [A] a single capital are markup, not a field.",
    ],
)
def test_an_instruction_or_no_slot_is_left_alone(guidance):
    assert find_unfilled_slots(guidance) == []


def test_the_exact_line_from_the_incident():
    line = 'THE OPENING:\n"Hi there - is that [name]?\n[Wait]\nMorning.'
    found = find_unfilled_slots(line)
    # A callee-name slot is resolved on every call (named or greeted without one).
    assert "[name]" not in found
    # "[Wait]" is a stage direction and capitalised; it must not be reported.
    assert "[Wait]" not in found


def test_duplicates_are_reported_once_and_sorted():
    assert find_unfilled_slots("[town] ... [name] ... [town] ... [company]") == [
        "[company]",
        "[town]",
    ]


def test_a_script_with_a_name_slot_greets_the_callee_by_name():
    from app.domain.services.telephony_session_config import script_greets_callee_by_name

    assert script_greets_callee_by_name("Hi there - is that [Name]?") is True


def test_detection_never_raises_on_odd_input():
    for odd in (None, "", "[", "]", "[]", "[" * 500):
        assert isinstance(find_unfilled_slots(odd), list)
