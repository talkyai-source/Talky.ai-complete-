"""Snapshot test for the estimation system prompt — pins key invariants.

Not a full text snapshot (too brittle). Instead asserts the structural
properties that the Groq-optimized rewrite must preserve."""
from __future__ import annotations

from app.domain.services.telephony_session_config import (
    TELEPHONY_ESTIMATION_SYSTEM_PROMPT,
)


def _rendered() -> str:
    return TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
        agent_name="Alex",
        company_name="All States Estimation",
    )


def test_prompt_leads_with_hard_rules():
    """Per Groq 2026: critical instructions first — model weighs early
    tokens most heavily."""
    rendered = _rendered()
    first_500 = rendered[:500]
    assert "HARD RULES" in first_500 or "RULES" in first_500


def test_prompt_has_off_topic_redirect():
    rendered = _rendered().lower()
    assert "off-topic" in rendered or "off topic" in rendered or "redirect" in rendered


def test_prompt_has_length_constraint():
    rendered = _rendered()
    assert (
        "1-2 sentence" in rendered.lower()
        or "one or two sentences" in rendered.lower()
        or "1 to 2 sentences" in rendered.lower()
    )


def test_prompt_has_already_captured_rule():
    rendered = _rendered().lower()
    assert "captured" in rendered
    assert "do not re-ask" in rendered or "do not ask again" in rendered


def test_prompt_has_readback_for_email():
    rendered = _rendered().lower()
    assert "read" in rendered and "back" in rendered and "email" in rendered


def test_prompt_has_identity_denial_line():
    rendered = _rendered()
    assert "robot" in rendered.lower() or "AI" in rendered


def test_prompt_preserves_company_website():
    """User explicit constraint: www.allstateestimation.com must not change."""
    rendered = _rendered()
    assert "www.allstateestimation.com" in rendered


def test_prompt_preserves_company_name_placeholder():
    """Keep the existing {company_name} + {agent_name} format slots."""
    assert "{company_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT
    assert "{agent_name}" in TELEPHONY_ESTIMATION_SYSTEM_PROMPT


def test_prompt_stays_under_9500_chars():
    # Budget: HARD RULES + EMAIL HANDLING + GREETING RESPONSE (consent-first
    # opener, 2026-04-22) + full original estimation prompt preserved verbatim.
    # Current size ~9280; 9500 ceiling leaves room for small additions before
    # the next review.
    assert len(_rendered()) < 9500
