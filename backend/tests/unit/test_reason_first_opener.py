"""The call reason ("cutting your energy bill") must still reach the callee —
just not on the FIRST turn anymore.

WHY THIS FILE CHANGED (2026-08-11)
-----------------------------------
Until 2026-08-11 the spoken PRE-SYNTH opener packed identity + the honest
reason + a time-ask into turn 1 ("Hi, it's Sarah at Acme — cold call, about
cutting your energy bill. Thirty seconds?"), and this file pinned that
shape (word budget, "cold call" present, ends in "?", etc.) directly against
``_PERSONA_GREETINGS_WITH_REASON`` / ``_PERSONA_GREETINGS``.

The owner's direction was to stop opening with that monologue: the FIRST
thing the callee hears is now a bare pickup greeting ("Hi there." /
"Hello?") with no identity, no reason, and no time-ask at all — see
``telephony_session_config.build_telephony_greeting`` and the OPENER
REDESIGN note above ``_PERSONA_GREETINGS`` in that module. Identity + the
reason are NOT gone — they moved to the model's own first generated reply,
once the callee has actually spoken (LIVE STATE's ``has_introduced=False``
path in prompts/live_state.py, and the STAGE 1 / OPENING blocks in
prompts/personas/lead_gen.py, customer_support.py, receptionist.py, which
still carry this exact shape and the same evidence base below).

WHAT THIS FILE NOW COVERS
--------------------------
1. ``_call_reason_for`` extraction/caps — UNCHANGED, still exercised as
   before (the caps now bound what reaches ``agent_config.call_reason``,
   consumed by the flag-gated LLM-authored opener in telephony/llm_opener.py
   and available to the persona prompt via the campaign's own
   ``call_reason`` slot).
2. The RELOCATED turn: ``compose_prompt`` for lead_gen still puts the
   reason, the "own the cold call" structure, the "?" ending, and the
   bad-time ban into the system prompt — just framed as the model's first
   REAL turn (after the callee replies) rather than "you speak first".
3. A regression pin that the SPOKEN pickup greeting (turn 1) no longer
   states the reason, identity, or a time-ask at all — the direct inverse
   of what this file asserted before 2026-08-11, and the point of the
   change. (Bare-greeting shape itself is pinned more broadly in
   test_telephony_session_config.py; this file only checks that the
   reason specifically does not leak back into it.)

ORIGINAL EVIDENCE (kept for context — still governs the relocated turn)
-------------------------------------------------------------------------
Gong, 300M+ calls / 90,380-call study:

    "Did I catch you at a bad time?"            2.15%   <- worst
    "How's your day going?"                     7.6%
    context -> own the cold call -> permission  11.18%
    context-first                              11.24%   <- best
plus a 2.1x lift for stating the reason for calling, and the word budget
below (8s decision window - 4.0s recording notice = 4.0s / 2.8 words per
second = 12 words), derived from a real call that hung up on 2026-08-05
after 11.7s of unbroken agent monologue with ``interrupted=False`` — the
callee never tried to cut in, they waited it out and quit.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import (
    _MAX_SPOKEN_CALL_REASON_CHARS,
    _MAX_SPOKEN_CALL_REASON_WORDS,
    _call_reason_for,
    build_persona_greeting,
    build_telephony_greeting,
)
from app.services.scripts.prompts.composer import compose_prompt

REASON = "cutting your energy bill"

#: Still the budget for the RELOCATED identity+reason turn (see module
#: docstring) — kept here, not just in llm_opener.py, because
#: test_llm_opener.py cross-checks its own LLM-authored-opener budget
#: against this one (test_accepted_openers_satisfy_the_template_budget_rule):
#: the "template" side of that cross-check now means "the budget this file
#: pins", not a literal spoken template dict (retired 2026-08-11).
MAX_OPENER_WORDS = 12


def _spoken_words(text: str) -> list[str]:
    """Words a TTS engine actually voices.

    ``str.split()`` counts a standalone em dash as a word; it is a pause, not
    a word, and pausing is what we WANT an opener to do. Anything with no
    alphanumeric character is dropped.
    """
    return [w for w in text.split() if any(c.isalnum() for c in w)]

LEAD_GEN_SLOTS = {
    "industry": "construction estimating",
    "services_description": "estimating and takeoff services",
    "coverage_area": "the USA",
    "value_proposition": "save you time on bids",
    "call_reason": REASON,
    "qualification_questions": ["Are you bidding right now?"],
    "disqualifying_answers": ["not a contractor"],
    "calendar_booking_type": "a 15-minute discovery call",
}


def _flat(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", text)


# ---------------------------------------------------------------------
# 1. `_call_reason_for` extraction/caps — unchanged behaviour
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "slots,expected",
    [
        ({"call_reason": REASON}, REASON),
        ({"goal": REASON}, REASON),                       # falls back to goal
        ({"call_reason": "  spaced   out  reason "}, "spaced out reason"),
        ({"call_reason": "ends with punctuation."}, "ends with punctuation"),
        ({}, None),
        ({"call_reason": ""}, None),
        ({"call_reason": "x" * 500}, None),               # too long to speak
        # 24 chars — inside the char cap — but six words. Speaking time tracks
        # words, so the char cap alone would have let this overrun.
        ({"call_reason": "we can cut your bill now"}, None),
    ],
)
def test_call_reason_extraction(slots, expected):
    assert _call_reason_for({"campaign_slots": slots}) == expected


def test_overlong_reason_falls_back_rather_than_monologuing():
    long_reason = "because " * 60
    assert len(long_reason) > _MAX_SPOKEN_CALL_REASON_CHARS
    assert _call_reason_for({"campaign_slots": {"call_reason": long_reason}}) is None


def test_reason_caps_are_still_sane():
    """The caps used to be pinned against the exact arithmetic of a
    now-retired template (8 fixed words + 4 for the reason = 12). The
    dict is gone; the caps remain and still have to leave room for a
    short reason inside the same 12-word / ~4.0s budget the LLM-authored
    opener (telephony/llm_opener.py) uses for the relocated identity+
    reason turn."""
    assert _MAX_SPOKEN_CALL_REASON_WORDS == 4
    assert _MAX_SPOKEN_CALL_REASON_CHARS <= 30


# ---------------------------------------------------------------------
# 2. The reason still reaches the RELOCATED turn (the system prompt)
# ---------------------------------------------------------------------


def test_the_reason_still_reaches_the_system_prompt():
    """The reason is a REQUIRED lead_gen slot and is rendered directly into
    STAGE 1 of the composed system prompt — the model's own first real
    turn, after the callee replies to the bare pickup greeting."""
    flat = _flat(compose_prompt(
        "lead_gen", "Sarah", "All-state", LEAD_GEN_SLOTS, direction="outbound",
    ))
    assert REASON in flat
    assert "Sarah" in flat and "All-state" in flat


def test_relocated_turn_still_follows_the_measured_structure():
    """Structure is evidence-based, not taste (see module docstring)."""
    flat = _flat(compose_prompt(
        "lead_gen", "Sarah", "All-state", LEAD_GEN_SLOTS, direction="outbound",
    )).lower()
    assert "cold call" in flat, "must still own the cold call"
    assert "thirty seconds" in flat, "must still hand the floor back with the ask"


def test_relocated_turn_still_bans_the_bad_time_question():
    """The 2.15% pattern, in any of its phrasings, must not reappear now
    that the shape lives in the persona prompt instead of a template."""
    flat = _flat(compose_prompt(
        "lead_gen", "Sarah", "All-state", LEAD_GEN_SLOTS, direction="outbound",
    )).lower()
    for banned in ("bad time", "bad moment", "get lost", "buzz off"):
        assert banned not in flat, (
            f"{banned!r} is back in the composed prompt — that is the "
            f"worst-converting opener family in the data"
        )


def test_stage_one_now_says_the_pickup_greeting_already_played():
    """Regression pin on the SEQUENCING fix: the persona prompt must no
    longer claim the model speaks first — a bare pickup greeting already
    played before this prompt runs, and STAGE 1 is what happens on the
    model's first REAL turn, after the callee replies."""
    flat = _flat(compose_prompt(
        "lead_gen", "Sarah", "All-state", LEAD_GEN_SLOTS, direction="outbound",
    ))
    assert "you speak first, the moment they pick up" not in flat.lower()
    assert "already played" in flat.lower() or "already spoke" in flat.lower()


def test_inbound_never_states_the_reason():
    """They rang us — telling them why we're calling is nonsense. Direction
    is unaffected by the opener redesign (caller-first already waited)."""
    out = build_persona_greeting(
        persona_type="lead_gen",
        agent_name="Sarah",
        company_name="All-state",
        direction="inbound",
        call_reason=REASON,
    )
    assert REASON not in out


# ---------------------------------------------------------------------
# 3. The SPOKEN pickup greeting must NOT carry the reason (or identity,
#    or a time-ask) — the direct inverse of this file's pre-2026-08-11
#    assertions, and the point of the change.
# ---------------------------------------------------------------------


def test_the_spoken_opener_no_longer_states_the_reason():
    for _ in range(30):
        out = build_persona_greeting(
            persona_type="lead_gen",
            agent_name="Sarah",
            company_name="All-state",
            direction="outbound",
            call_reason=REASON,
        )
        assert REASON not in out
        assert "Sarah" not in out and "All-state" not in out


def test_the_spoken_opener_is_persona_and_reason_independent():
    """A campaign WITH a call_reason and one WITHOUT must now produce
    identical pickup-greeting behaviour — the reason no longer changes
    what turn 1 sounds like at all."""
    import random

    random.seed(1234)
    with_reason = build_persona_greeting(
        persona_type="lead_gen",
        agent_name="Sarah",
        company_name="All-state",
        direction="outbound",
        call_reason=REASON,
    )
    random.seed(1234)
    without_reason = build_persona_greeting(
        persona_type="lead_gen",
        agent_name="Sarah",
        company_name="All-state",
        direction="outbound",
        call_reason=None,
    )
    # Re-seed before the third call too. The greeting is chosen with
    # random.choice, so a call made WITHOUT resetting the seed advances the
    # RNG and picks a different (equally valid) variant — the original form
    # of this assertion failed on 'Hey there.' != 'Hi there.', which was the
    # test consuming randomness, not the code varying by persona/reason.
    random.seed(1234)
    assert with_reason == without_reason == build_telephony_greeting(
        "Sarah", "All-state",
    )
