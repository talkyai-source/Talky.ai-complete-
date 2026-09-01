"""The composed prompt must not argue with itself.

Audit 2026-09-02 of a real composed prompt (28,640 chars, ~7,160 tokens, for a
campaign with 80 chars of guidance) found four places where one section told
the model to do X and another told it to do not-X, plus a dated engineering
changelog embedded in STAGE 1 that the model reads on every turn. On a 20B/120B
model at reasoning_effort=low these are not subtleties: they are the coin-flips
callers hear. These tests pin the prompt to a single answer per question.
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts.composer import compose_prompt
from app.services.scripts.prompts.direction import INBOUND_DIRECTIVE_SENTINEL


def _kd(opening_mode: str) -> str:
    return compose_prompt(
        "lead_gen", "Sarah", "Dojo", {},
        additional_instructions="Call UK retailers about card terminals.",
        direction="outbound", opening_mode=opening_mode, knowledge_driven=True,
    )


# ── the knowledge-driven body must honour the opening mode ──────────────────

def test_kd_callee_first_prompt_does_not_claim_a_greeting_already_played():
    """callee-first: the agent waits for 'hello', nothing has played. The KD
    body used to hard-code the agent-first STAGE 1 ('A bare pickup greeting
    already played'), contradicting the directive at position 0."""
    p = _kd("callee_first")
    assert p.startswith(INBOUND_DIRECTIVE_SENTINEL)
    assert "already played" not in p
    assert "they speak first" in p or "CALLEE SPEAKS FIRST" in p


def test_kd_agent_first_prompt_keeps_the_pickup_greeting_stage():
    p = _kd("agent_first")
    assert INBOUND_DIRECTIVE_SENTINEL not in p
    assert "already played" in p


def test_kd_body_reuses_the_shared_openings_not_a_private_copy():
    """One STAGE 1 text per opening mode, shared by the slot-based and the
    knowledge-driven bodies — a private copy is how the two drifted."""
    from app.services.scripts.prompts.personas import lead_gen

    for key in ("outbound", "inbound"):
        body = lead_gen.lead_gen_kd_body(key)
        assert body.startswith(lead_gen.LEAD_GEN_OPENINGS[key])
        assert body.endswith(lead_gen.LEAD_GEN_PLAYBOOK)
    assert lead_gen.LEAD_GEN_KD_BODY == lead_gen.lead_gen_kd_body("outbound")


# ── no engineering changelog inside the prompt ──────────────────────────────

@pytest.mark.parametrize("opening_mode", ["agent_first", "callee_first"])
def test_prompt_carries_no_dated_changelog_notes(opening_mode):
    p = _kd(opening_mode)
    dated = re.findall(r"20\d\d-\d\d-\d\d", p)
    assert dated == [], f"engineering dates in the prompt: {dated}"
    assert "per the owner's own phrasing" not in p
    assert "worst-converting family measured" not in p


# ── one answer per question ─────────────────────────────────────────────────

def test_voicemail_has_one_instruction_end_the_call():
    """Code hangs up on voicemail (AMD) and ENDING THE CALL says END_CALL alone;
    LIVE-CALL REALISM used to say 'leave a short, warm message'."""
    p = _kd("agent_first")
    assert "leave a short" not in p.lower()
    assert re.search(r"VOICEMAIL.*(don.t (talk|leave)|end the call|END_CALL)", p, re.S)


def test_wrong_person_is_a_pivot_everywhere_not_a_hangup():
    """WRONG PERSON / GATEKEEPER says pivot; ENDING THE CALL says wrong person
    is NOT an end; WHEN THE CALL SHOULD STOP used to say wrong person → close."""
    p = _kd("agent_first")
    assert "WRONG PERSON / WRONG NUMBER" not in p
    assert "WRONG NUMBER / WRONG BUSINESS" in p


def test_silence_is_owned_by_the_silence_monitor_not_the_prompt():
    """The silence monitor speaks the nudges ('Hello?' ladder, 60s close). The
    prompt used to ALSO tell the model to say 'Take your time' / 'Still there?'
    and close on the third — two voices on one silence."""
    p = _kd("agent_first")
    assert "Still there?" not in p
    assert "Take your time" not in p
    assert "close on the third" not in p
