"""Turn 2 — the relocated identity + permission-ask + reason turn.

WHY THIS FILE EXISTS (2026-08-11)
----------------------------------
Turn 1 is now a bare human-pickup greeting ("Hi there." / "Hello?") with no
name, no company, no reason — see telephony_session_config.build_telephony_
greeting and the "2026-08-11" notes above each persona's OPENING/STAGE 1
block. Identity now belongs on turn 2, spoken by the model itself once the
callee has replied.

The owner's ask for that turn, verbatim: "once client spoken start it like
my name is this if you don't mind do you have a minute than based on that
and that and that like natural conversation" — name, then a light permission
ask, then let the reply shape what follows.

The evidence (Gong, 300M+ recorded cold calls) says permission-based openers
("have you got a minute?") convert near the BEST measured (~11.18%, inside
the context -> own-the-cold-call -> permission structure), while asking
whether now happens to be an inconvenient moment for THEM is the WORST family
measured (0.9%-2.15%). Those are different sentences, not two names for the
same idea, and this file pins that the persona prompts keep them apart —
without ever writing the bad-time phrasing into the prompt text itself
(a banned phrase in a prompt primes the model toward it even when the
sentence around it is a prohibition — the two prior incidents this codebase
already had with that mistake are why guardrails.py and the spoken-greeting
templates never quote the phrase they ban, and this rewrite follows the same
rule).

WHAT IS PINNED HERE
--------------------
1. Turn 2 (the quoted shape) carries name, a permission ask, and the reason
   for the call, in that order — for lead_gen (both directions) and for the
   customer_support / receptionist outbound callback openings.
2. Turn 2 stays inside the one-breath / under-twenty-word budget every one
   of these blocks states in prose.
3. The bad-time family ("bad time", "bad moment", "get lost", "buzz off")
   never appears in the composed prompt for any of the three personas.
4. The permission ask itself is the SAME phrase across all three personas —
   one shape, not three competing ones.
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


#: The permission phrase every persona's turn-2 shape uses. Pinned as a
#: literal because "the three personas stay consistent" is a testable claim
#: only if they all say the same thing.
_PERMISSION_PHRASE = "got a minute?"

_BAD_TIME_FAMILY = ("bad time", "bad moment", "get lost", "buzz off")


def _shape_quote(block: str) -> str:
    """Pull the ONE quoted turn-2 shape out of an OPENING/STAGE-1 block —
    the quoted span that names both {agent_name} and {company_name}. Mirrors
    the parsing in test_prompt_brevity_and_narration.test_lead_gen_opener_
    shape_fits_the_decision_window, which already proved this is the safe
    way to find it (a length-filtered pattern silently breaks quote parity)."""
    quoted = re.findall(r'"([^"]*)"', _flat(block))
    shapes = [q for q in quoted if "{agent_name}" in q and "{company_name}" in q]
    assert len(shapes) == 1, f"expected exactly one turn-2 shape, found {shapes!r}"
    return shapes[0]


# ---------------------------------------------------------------------------
# 1. Ordering: name -> permission ask -> reason, in the same breath.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,block,reason_marker",
    [
        ("lead_gen outbound", LEAD_GEN_OPENINGS["outbound"], "{call_reason}"),
        ("lead_gen inbound", LEAD_GEN_OPENINGS["inbound"], "{call_reason}"),
        (
            "customer_support outbound",
            CUSTOMER_SUPPORT_OPENINGS["outbound"],
            "calling about your recent inquiry",
        ),
        (
            "receptionist outbound",
            RECEPTIONIST_OPENINGS["outbound"],
            "following up on your inquiry",
        ),
    ],
)
def test_turn2_shape_is_name_then_permission_then_reason(name, block, reason_marker):
    shape = _shape_quote(block)
    name_idx = shape.index("{agent_name}")
    permission_idx = shape.lower().index(_PERMISSION_PHRASE)
    reason_idx = shape.index(reason_marker)
    assert name_idx < permission_idx < reason_idx, (
        f"{name}: expected name -> permission ask -> reason, got shape {shape!r} "
        f"(name={name_idx}, permission={permission_idx}, reason={reason_idx})"
    )


def test_all_four_turn2_shapes_use_the_same_permission_phrase():
    """The three personas stay consistent — one shape, not three."""
    shapes = {
        "lead_gen outbound": _shape_quote(LEAD_GEN_OPENINGS["outbound"]),
        "lead_gen inbound": _shape_quote(LEAD_GEN_OPENINGS["inbound"]),
        "customer_support outbound": _shape_quote(CUSTOMER_SUPPORT_OPENINGS["outbound"]),
        "receptionist outbound": _shape_quote(RECEPTIONIST_OPENINGS["outbound"]),
    }
    for name, shape in shapes.items():
        assert _PERMISSION_PHRASE in shape.lower(), f"{name} lost the permission ask: {shape!r}"


# ---------------------------------------------------------------------------
# 2. Length: the shape itself stays inside the stated one-breath budget.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,block,fmt_kwargs",
    [
        (
            "lead_gen outbound",
            LEAD_GEN_OPENINGS["outbound"],
            dict(agent_name="Sarah", company_name="Allstate", call_reason=LEAD_GEN_SLOTS["call_reason"]),
        ),
        (
            "lead_gen inbound",
            LEAD_GEN_OPENINGS["inbound"],
            dict(agent_name="Sarah", company_name="Allstate", call_reason=LEAD_GEN_SLOTS["call_reason"]),
        ),
        (
            "customer_support outbound",
            CUSTOMER_SUPPORT_OPENINGS["outbound"],
            dict(agent_name="Sam", company_name="Acme"),
        ),
        (
            "receptionist outbound",
            RECEPTIONIST_OPENINGS["outbound"],
            dict(agent_name="Maya", company_name="Acme"),
        ),
    ],
)
def test_turn2_shape_stays_under_twenty_words(name, block, fmt_kwargs):
    shape = _shape_quote(block)
    rendered = shape.format(**fmt_kwargs)
    words = [w for w in rendered.split() if any(c.isalnum() for c in w)]
    assert len(words) <= 20, (
        f"{name} turn-2 shape is {len(words)} spoken words (budget is under "
        f"twenty): {rendered!r}"
    )


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
# 4. Permission-ask framing is distinguished from the bad-time family in the
#    surrounding prose, positively — never by naming the bad phrase.
# ---------------------------------------------------------------------------


def test_lead_gen_outbound_explains_the_permission_ask_is_not_the_bad_time_ask():
    """The 2026-08-06 pass banned ALL availability-based openers, conflating
    the permission-to-proceed ask with the bad-time question. This pass
    un-conflates them — the prose has to say so, or a future edit re-merges
    them by accident."""
    low = LEAD_GEN_OPENINGS["outbound"].lower()
    assert "different move" in low
    # The distinguishing description uses a paraphrase, never the banned
    # phrase itself.
    assert "inconvenient moment" in low
    for banned in _BAD_TIME_FAMILY:
        assert banned not in low
