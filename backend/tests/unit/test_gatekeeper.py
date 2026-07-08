"""Unit tests for the gatekeeper / wrong-person pivot prompt block."""
from app.domain.services.voice_pipeline.gatekeeper import (
    GATEKEEPER_RULES,
    gatekeeper_rules,
)


def test_returns_constant():
    assert gatekeeper_rules() == GATEKEEPER_RULES


def test_no_unformatted_placeholders():
    # This block is appended verbatim by composer.py with no .format() pass —
    # any literal {curly_brace} placeholder would leak to the LLM unfilled.
    assert "{" not in GATEKEEPER_RULES
    assert "}" not in GATEKEEPER_RULES


def test_covers_wrong_person_pivot():
    assert "WRONG PERSON" in GATEKEEPER_RULES
    assert "do not" in GATEKEEPER_RULES.lower() or "never" in GATEKEEPER_RULES.lower()


def test_covers_hesitation_and_graceful_exit():
    assert "HESITATION" in GATEKEEPER_RULES
    assert "GRACEFUL EXIT" in GATEKEEPER_RULES


def test_is_compact():
    # Recency power decays with length — keep this a trailing, tight block.
    assert len(GATEKEEPER_RULES.splitlines()) <= 32
