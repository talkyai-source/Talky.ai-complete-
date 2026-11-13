"""Tests for the per-turn LIVE STATE block (prompts/live_state.py).

The block reports neutral runtime facts (agent, company, direction and
introduction=delivered / not_delivered / interrupted, live_state.py:13-15). The
"do not repeat an introduction" and turn-priority rules live in the static
policies (policies/how_to_speak.md, policies/conversation_guide.md), not in this
block. It must never re-declare a specific role title (only the persona does that)."""
from __future__ import annotations

from app.services.scripts.prompts.live_state import build_live_state_block


def test_not_introduced_tells_agent_to_open():
    out = build_live_state_block(agent_name="Sarah", company_name="Dojo", has_introduced=False)
    assert "Sarah" in out and "Dojo" in out
    # live_state.py:13-15: the fact is introduction=not_delivered (no instruction line any more).
    assert "introduction=not_delivered" in out
    assert "introduction=delivered" not in out


def test_introduced_forbids_reintroduction():
    out = build_live_state_block(agent_name="Sarah", company_name="Dojo", has_introduced=True)
    assert "introduction=delivered" in out
    # "Do not repeat an introduction already delivered" now lives in the static policy
    # (policies/how_to_speak.md:12-13), a policy shared by every agent (how_to_speak.md:2).
    from app.services.scripts.prompts.policies import load_policy
    assert "Do not repeat an introduction already delivered" in " ".join(load_policy("how_to_speak").split())
    assert "representative" not in out and "consultant" not in out


def test_name_is_anchored_every_call():
    out = build_live_state_block(agent_name="Azian", company_name="Dojo", has_introduced=True)
    assert "Azian" in out
    # Anchored as a status line (live_state.py:14), not a second "You are …" declaration.
    assert "agent=Azian" in out
    assert "You are Azian" not in out


def test_empty_identity_returns_blank():
    assert build_live_state_block(agent_name="", company_name="", has_introduced=True) == ""
    assert build_live_state_block(agent_name="  ", company_name="  ", has_introduced=False) == ""


def test_name_only_still_renders():
    out = build_live_state_block(agent_name="Sarah", company_name="", has_introduced=False)
    assert "Sarah" in out


def test_turn_priority_puts_compliance_and_open_questions_before_qualification():
    out = build_live_state_block(agent_name="Sarah", company_name="Dojo", has_introduced=True)
    # b4d25876 moved turn priority out of the per-turn block; it must not return there
    # (tests/unit/test_agent_conversation_guide.py:43 pins "Turn priority:" absent).
    assert "Turn priority:" not in out and "discovery, qualification" not in out
    # The rules it used to carry are now static policy text: respect a stop request
    # (policies/conversation_guide.md:11) and answer what they asked first (policies/how_to_speak.md:7).
    from app.services.scripts.prompts.policies import load_policy
    assert "Respect a refusal or request to stop" in " ".join(load_policy("conversation_guide").split())
    assert "answer what they asked first" in " ".join(load_policy("how_to_speak").split())
