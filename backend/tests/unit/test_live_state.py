"""Tests for the per-turn LIVE STATE block (prompts/live_state.py).

The block is the anti-re-introduction anchor: once `has_introduced` flips, the
agent must be told NOT to introduce itself again. It must never re-declare a
specific role title (only the persona does that)."""
from __future__ import annotations

from app.services.scripts.prompts.live_state import build_live_state_block


def test_not_introduced_tells_agent_to_open():
    out = build_live_state_block(agent_name="Sarah", company_name="Dojo", has_introduced=False)
    assert "Sarah" in out and "Dojo" in out
    assert "have not introduced yourself" in out
    assert "ALREADY introduced" not in out


def test_introduced_forbids_reintroduction():
    out = build_live_state_block(agent_name="Sarah", company_name="Dojo", has_introduced=True)
    assert "ALREADY introduced" in out
    assert "Do NOT introduce yourself again" in out
    # Anti-drift: forbids switching name/title, but does NOT declare a role title
    # (the persona is the single source of the role).
    assert "never switch to a different name or job title" in out
    assert "representative" not in out and "consultant" not in out


def test_name_is_anchored_every_call():
    out = build_live_state_block(agent_name="Azian", company_name="Dojo", has_introduced=True)
    assert "Azian" in out
    # Anchored as a status line, not a second "You are …" declaration.
    assert "on this call as Azian" in out
    assert "You are Azian" not in out


def test_empty_identity_returns_blank():
    assert build_live_state_block(agent_name="", company_name="", has_introduced=True) == ""
    assert build_live_state_block(agent_name="  ", company_name="  ", has_introduced=False) == ""


def test_name_only_still_renders():
    out = build_live_state_block(agent_name="Sarah", company_name="", has_introduced=False)
    assert "Sarah" in out
