"""Each persona's opening block is an OPENING CONTEXT: it says who is speaking and
why the call is made, and hands the floor back. It scripts no quoted line and no
permission question; the wording of the first reply is the model's own
(b4d25876; personas/lead_gen.py:10-11, customer_support.py:8, receptionist.py:8).
The bad-time phrasing family must still never reach the prompt.
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts.composer import compose_prompt
from app.services.scripts.prompts.personas.customer_support import (
    CUSTOMER_SUPPORT_OPENINGS,
)
from app.services.scripts.prompts.personas.lead_gen import LEAD_GEN_OPENINGS
from app.services.scripts.prompts.personas.receptionist import (
    RECEPTIONIST_OPENINGS,
)

LEAD_GEN_SLOTS = {
    "industry": "construction estimating",
    "services_description": "estimating and takeoff services",
    "coverage_area": "the USA",
    "value_proposition": "save you time on bids",
    "call_reason": "cutting your energy bill",
    "qualification_questions": ["Are you bidding right now?"],
    "disqualifying_answers": ["not a contractor"],
    "calendar_booking_type": "a 15-minute discovery call",
}

SUPPORT_SLOTS = {
    "business_hours": "Mon-Fri 9-5",
    "website": "example.com",
    "support_email": "support@example.com",
    "refund_policy": "14-day refund",
    "cancellation_policy": "anytime",
    "complaint_policy": "escalate to manager",
    "support_topics": ["billing", "access"],
    "common_issues": [{"issue": "login", "solution": "reset password"}],
    "escalate_triggers": ["legal threat"],
    "escalate_to": "the manager",
    "escalation_wait_time": "one business day",
}

RECEPTIONIST_SLOTS = {
    "business_type": "dental practice",
    "business_address": "123 Main St",
    "business_phone": "555-0100",
    "business_email": "hi@example.com",
    "website": "example.com",
    "opening_hours": "Mon-Fri 9-5",
    "services": ["cleaning", "checkup"],
    "emergency_protocol": "Direct emergencies to 911.",
    "new_patient_info_needed": ["full name", "date of birth"],
}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


_BAD_TIME_FAMILY = ("bad time", "bad moment", "get lost", "buzz off")


# ---------------------------------------------------------------------------
# 1. Identity and reason are given as context, without a scripted line.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,block,must_contain",
    [
        ("lead_gen outbound", LEAD_GEN_OPENINGS["outbound"],
         ("{agent_name}", "{company_name}", "{call_reason}", "then listen")),
        ("lead_gen inbound", LEAD_GEN_OPENINGS["inbound"], ("{call_reason}", "recipient speaks first")),
        ("customer_support outbound", CUSTOMER_SUPPORT_OPENINGS["outbound"],
         ("{company_name}", "approved reason")),
        ("receptionist outbound", RECEPTIONIST_OPENINGS["outbound"],
         ("{company_name}", "approved purpose", "listen")),
    ],
)
def test_opening_context_gives_identity_and_reason_without_a_scripted_line(name, block, must_contain):
    # b4d25876: OPENING CONTEXT says who is speaking and why; the wording is the model's own.
    assert block.startswith("OPENING CONTEXT\n"), name
    for part in must_contain:
        assert part in block, (name, part)
    assert '"' not in block, f"{name}: a quoted scripted line is back in the opening context"


# ---------------------------------------------------------------------------
# 2. Length: the opening context stays short.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,block", [
    ("lead_gen outbound", LEAD_GEN_OPENINGS["outbound"]),
    ("lead_gen inbound", LEAD_GEN_OPENINGS["inbound"]),
    ("customer_support outbound", CUSTOMER_SUPPORT_OPENINGS["outbound"]),
    ("receptionist outbound", RECEPTIONIST_OPENINGS["outbound"]),
])
def test_opening_context_stays_short(name, block):
    # Size guard, not a product rule: the four blocks are 14-19 words today (personas/*.py),
    # so 30 leaves headroom while still catching a script growing back into the context.
    words = [w for w in block.split() if any(ch.isalnum() for ch in w)]
    assert len(words) <= 30, f"{name} opening context is {len(words)} words"


# ---------------------------------------------------------------------------
# 3. The bad-time family never reaches the composed prompt, for any persona.
# ---------------------------------------------------------------------------


def test_lead_gen_composed_prompt_has_no_bad_time_family():
    for direction in ("outbound", "inbound"):
        flat = _flat(compose_prompt(
            "lead_gen", "Sarah", "Allstate", LEAD_GEN_SLOTS, direction=direction,
        )).lower()
        for banned in _BAD_TIME_FAMILY:
            assert banned not in flat, f"lead_gen {direction}: {banned!r} leaked into the prompt"


def test_customer_support_composed_prompt_has_no_bad_time_family():
    flat = _flat(compose_prompt(
        "customer_support", "Sam", "Acme", SUPPORT_SLOTS, direction="outbound",
    )).lower()
    for banned in _BAD_TIME_FAMILY:
        assert banned not in flat, f"customer_support outbound: {banned!r} leaked into the prompt"


def test_receptionist_composed_prompt_has_no_bad_time_family():
    flat = _flat(compose_prompt(
        "receptionist", "Maya", "Acme", RECEPTIONIST_SLOTS, direction="outbound",
    )).lower()
    for banned in _BAD_TIME_FAMILY:
        assert banned not in flat, f"receptionist outbound: {banned!r} leaked into the prompt"


@pytest.mark.parametrize(
    "name,value",
    [
        ("lead_gen outbound", LEAD_GEN_OPENINGS["outbound"]),
        ("lead_gen inbound", LEAD_GEN_OPENINGS["inbound"]),
        ("customer_support outbound", CUSTOMER_SUPPORT_OPENINGS["outbound"]),
        ("receptionist outbound", RECEPTIONIST_OPENINGS["outbound"]),
    ],
)
def test_the_opening_blocks_themselves_never_quote_the_bad_time_phrasing(name, value):
    """Pinned directly on the dict VALUES (the actual prompt text), separate
    from the whole-composed-prompt check above, and separate from the Python
    `#` comments above these dicts (which discuss the banned family by name
    for documentation — comments never reach the model, only string literals
    do). POSITIVE FRAMING rule: a banned phrase must not appear even inside a
    prohibition, because the model reads the prompt, not the intent behind
    it."""
    low = value.lower()
    for banned in _BAD_TIME_FAMILY:
        assert banned not in low, f"{name}: {banned!r} was quoted directly in the prompt text"


# ---------------------------------------------------------------------------
# 4. The opening hands the floor back, framed positively — never by naming the
#    bad-time phrase.
# ---------------------------------------------------------------------------


def test_lead_gen_outbound_introduces_naturally_and_then_listens():
    """The opening context hands the floor back; it scripts no permission question."""
    low = LEAD_GEN_OPENINGS["outbound"].lower()
    assert "introduce {agent_name} and {company_name} naturally if needed, then listen." in low
    for banned in _BAD_TIME_FAMILY:
        assert banned not in low
