"""bughunt knowledge-prompts-7: the agent-name rewrite must only touch identity
phrases, never company names or ordinary words, and detection must agree."""
from __future__ import annotations

import logging

import pytest

from app.services.scripts.prompts.agent_name_rotator import (
    name_is_referenced_in,
    rename_agent_in_script,
)


def test_identity_phrases_are_renamed():
    out = rename_agent_in_script(
        "You are Daniel. Your name is Daniel. If asked, I'm Daniel from the team. "
        "Say: this is Daniel speaking.",
        "Daniel", "James",
    )
    assert "Daniel" not in out
    assert out.count("James") == 4


def test_company_name_and_ordinary_words_are_left_alone():
    script = (
        "You are Daniel, the booking agent for Daniel Bakery. Mention the 30-day "
        "Daniel period only if asked. Ask for Marcus's Garage by name."
    )
    out = rename_agent_in_script(script, "Daniel", "James", company_name="Daniel Bakery")
    assert out.startswith("You are James, the booking agent")
    assert "Daniel Bakery" in out
    assert "30-day Daniel period" in out
    assert "Marcus's Garage" in out


def test_company_exactly_after_an_identity_phrase_is_protected():
    out = rename_agent_in_script("This is Grace Cafe calling.", "Grace", "James",
                                 company_name="Grace Cafe")
    assert out == "This is Grace Cafe calling."


def test_the_grace_period_is_not_rewritten():
    out = rename_agent_in_script("There is a grace period of 30 days.", "Grace", "James")
    assert out == "There is a grace period of 30 days."


def test_detection_and_rewrite_agree():
    pool = ["Grace"]
    prose_only = "There is a grace period of 30 days."
    assert name_is_referenced_in(prose_only, pool) is False
    assert rename_agent_in_script(prose_only, "Grace", "James") == prose_only
    identity = "You are Grace, a friendly agent."
    assert name_is_referenced_in(identity, pool) is True
    assert "James" in rename_agent_in_script(identity, "Grace", "James")


def test_changed_spans_are_logged(caplog):
    with caplog.at_level(logging.INFO):
        rename_agent_in_script("You are Daniel.", "Daniel", "James")
    assert any("agent_name_rename_spans" in r.getMessage() and "Daniel" in r.getMessage()
               for r in caplog.records)
