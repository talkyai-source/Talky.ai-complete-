"""Opening context stays consistent without inventing delivery receipts.

These assembly checks preserve direction, shared persona context and clean
prompt text. They do not impose a spoken script or prove model behavior.
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


def test_kd_agent_first_prompt_supplies_context_without_claiming_playback():
    p = _kd("agent_first")
    assert INBOUND_DIRECTIVE_SENTINEL not in p
    assert "already played" not in p
    assert "OPENING CONTEXT" in p and "This is an outbound call" in p


def test_kd_body_reuses_the_shared_openings_not_a_private_copy():
    """Both composition paths share the same opening context."""
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
