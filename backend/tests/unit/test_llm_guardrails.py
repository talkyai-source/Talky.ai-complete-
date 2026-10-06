"""Formatting and privacy cleanup preserves ordinary model speech."""
import pytest
from app.domain.services.llm_guardrails import LLMGuardrails, get_guardrails


class TestResponseCleaning:
    """Test response cleaning of LLM artifacts"""

    def test_clean_filler_words(self):
        guardrails = LLMGuardrails()
        for text in ("Sure! I'll do that.", "Sure thing! On it.", "Of course, take your time.", "Well, let's see.", "Okay, go ahead."):
            assert guardrails.clean_response(text) == text

    def test_clean_whitespace(self):
        """Test cleanup of excessive whitespace"""
        guardrails = LLMGuardrails()

        messy = "This   has    too   many   spaces."
        cleaned = guardrails.clean_response(messy)

        assert "  " not in cleaned

    def test_clean_preserves_parenthetical_qualifiers(self):
        # A generic parenthesis matcher cannot distinguish asides from factual qualifiers.
        guardrails = LLMGuardrails()
        for text in ("Starter costs £19 (excluding VAT).", "Open weekdays (except public holidays)."):
            assert guardrails.clean_response(text) == text

    def test_clean_keeps_numeric_parentheses(self):
        """A phone area code in parens has no 3+ letter word, so it survives."""
        guardrails = LLMGuardrails()
        out = guardrails.clean_response("Call me on (077) 894 231.")
        assert "077" in out

    def test_clean_reasoning_and_markdown_artifacts(self):
        """Raw reasoning and markdown should not survive into spoken/display text."""
        guardrails = LLMGuardrails()

        raw = """
<think>I should outline the pricing plan first.</think>
### Plans
1. **Basic** is $29/month.
2. **Professional** is $79/month.
"""

        cleaned = guardrails.clean_response(raw)

        assert "outline the pricing plan first" not in cleaned
        assert "<think>" not in cleaned
        assert "**" not in cleaned
        assert "#" not in cleaned
        assert "1." not in cleaned
        assert "2." not in cleaned
        assert cleaned.startswith("Plans")


class TestGuardrailsConfig:
    """Test guardrails configuration"""


    def test_singleton_pattern(self):
        """Test get_guardrails returns singleton"""
        guardrails1 = get_guardrails()
        guardrails2 = get_guardrails()

        assert guardrails1 is guardrails2


# ── Bug 1 regression: Sure!? was too greedy ──────────────────────────────────

@pytest.fixture
def guardrails():
    return LLMGuardrails()


def test_sure_thing_is_preserved(guardrails):
    text = "Sure thing! Our Basic plan costs $29/month."
    assert guardrails.clean_response(text) == text


def test_sure_exclamation_is_preserved(guardrails):
    text = "Sure! I can help you with that."
    assert guardrails.clean_response(text) == text


def test_sure_comma_is_preserved(guardrails):
    text = "Sure, let me check that for you."
    assert guardrails.clean_response(text) == text


# ── Other filler patterns must still work ────────────────────────────────────

def test_natural_discourse_markers_preserved(guardrails):
    """Natural human openers ("Well,", "So,", "Okay,", "Alright,", "Actually,")
    are KEPT now — they're the conversational fillers the persona asks for, and
    stripping them was deleting the very naturalness we want before TTS."""
    assert guardrails.clean_response("Well, that sounds great.") == "Well, that sounds great."
    assert guardrails.clean_response("Okay, let me look that up.") == "Okay, let me look that up."
    assert guardrails.clean_response("So, here's the thing.") == "So, here's the thing."
    assert guardrails.clean_response("Hmm, good question.") == "Hmm, good question."


def test_of_course_is_preserved(guardrails):
    text = "Of course! Happy to help."
    assert guardrails.clean_response(text) == text


def test_no_filler_unchanged(guardrails):
    result = guardrails.clean_response("Our pricing starts at $29 per month.")
    assert result == "Our pricing starts at $29 per month."


def test_empty_response_unchanged(guardrails):
    result = guardrails.clean_response("")
    assert result == ""


def test_none_response_unchanged(guardrails):
    result = guardrails.clean_response(None)
    assert result is None
