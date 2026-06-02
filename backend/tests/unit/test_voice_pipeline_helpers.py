"""Unit tests for the voice_pipeline leaf helpers extracted from
VoicePipelineService (item 2 decomposition)."""
from __future__ import annotations

from app.domain.services.voice_pipeline import (
    find_sentence_end,
    is_terminal_period_boundary,
    is_repetitive_transcript,
)


# ── find_sentence_end ────────────────────────────────────────────────────────

def test_terminal_punctuation_without_trailing_space():
    assert find_sentence_end("Hello there.") == len("Hello there.") - 1
    assert find_sentence_end("Can you hear me?") == len("Can you hear me?") - 1
    assert find_sentence_end("Great!") == len("Great!") - 1


def test_period_followed_by_space_is_boundary():
    # boundary at the first sentence's period
    assert find_sentence_end("Hi there. More text") == len("Hi there")


def test_ellipsis_is_not_a_boundary():
    assert find_sentence_end("Wait... let me think") == -1


def test_terminal_abbreviation_not_split():
    assert find_sentence_end("Please ask Dr.") == -1
    assert find_sentence_end("Please ask A.") == -1   # single initial


def test_no_boundary_returns_minus_one():
    assert find_sentence_end("just some words with no end") == -1


def test_clause_boundary_only_when_long_and_allowed():
    # short text: clause flag has no effect
    assert find_sentence_end("yes, and no", allow_clause=True) == -1
    # long opener with a comma+conjunction past 40 chars and >=80 total,
    # and NO hard terminator yet (mid-stream) — clause flush should fire.
    text = (
        "I really appreciate you taking the time to chat today, "
        "and I wanted to walk you through the options we have"
    )
    idx = find_sentence_end(text, allow_clause=True)
    assert idx > 0 and text[idx] == ","
    # without allow_clause, the same comma is not a boundary (no terminator → -1)
    assert find_sentence_end(text, allow_clause=False) == -1


# ── is_terminal_period_boundary ──────────────────────────────────────────────

def test_terminal_period_boundary_rules():
    assert is_terminal_period_boundary("End here.", len("End here.") - 1) is True
    assert is_terminal_period_boundary("ask Dr.", len("ask Dr.") - 1) is False
    assert is_terminal_period_boundary("initial A.", len("initial A.") - 1) is False


# ── is_repetitive_transcript ─────────────────────────────────────────────────

def test_repetitive_transcript_detected():
    assert is_repetitive_transcript("yeah yeah yeah yeah yeah yeah") is True


def test_normal_speech_not_flagged():
    assert is_repetitive_transcript("I'd like to know about your pricing options") is False


def test_short_transcript_never_flagged():
    # under 6 words → never repetitive regardless of duplication
    assert is_repetitive_transcript("no no no no no") is False
