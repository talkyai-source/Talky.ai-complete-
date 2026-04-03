"""
Unit tests for LLM Guardrails — clean_response filler stripping.

These tests verify that the filler-start patterns strip ONLY true conversational
filler words (e.g. "Sure! I can help") and NOT substantive phrases that happen
to start with the same word (e.g. "Sure thing! Our Basic plan").
"""
import pytest
from app.domain.services.llm_guardrails import LLMGuardrails


@pytest.fixture
def guardrails():
    return LLMGuardrails()


# ── Bug 1 regression: Sure!? was too greedy ──────────────────────────────────

def test_sure_thing_is_not_stripped(guardrails):
    """'Sure thing' is a substantive phrase, not a filler — must not be stripped."""
    result = guardrails.clean_response("Sure thing! Our Basic plan costs $29/month.")
    assert result.startswith("Sure thing"), (
        f"'Sure thing' must not be stripped, got: '{result}'"
    )


def test_sure_exclamation_filler_is_stripped(guardrails):
    """'Sure! ' followed by real content IS a filler and should be stripped."""
    result = guardrails.clean_response("Sure! I can help you with that.")
    assert result == "I can help you with that."


def test_sure_comma_filler_is_stripped(guardrails):
    """'Sure, ' followed by real content IS a filler and should be stripped."""
    result = guardrails.clean_response("Sure, let me check that for you.")
    assert result == "let me check that for you."


# ── Other filler patterns must still work ────────────────────────────────────

def test_well_filler_stripped(guardrails):
    result = guardrails.clean_response("Well, that sounds great.")
    assert result == "that sounds great."


def test_okay_filler_stripped(guardrails):
    result = guardrails.clean_response("Okay, let me look that up.")
    assert result == "let me look that up."


def test_of_course_filler_stripped(guardrails):
    result = guardrails.clean_response("Of course! Happy to help.")
    assert result == "Happy to help."


def test_no_filler_unchanged(guardrails):
    result = guardrails.clean_response("Our pricing starts at $29 per month.")
    assert result == "Our pricing starts at $29 per month."


def test_empty_response_unchanged(guardrails):
    result = guardrails.clean_response("")
    assert result == ""


def test_none_response_unchanged(guardrails):
    result = guardrails.clean_response(None)
    assert result is None
