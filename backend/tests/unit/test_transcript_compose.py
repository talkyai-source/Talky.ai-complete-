"""
TKT-008 — the trailing-fragment truncation bug.

`turn_text()` returned finalized segments *or* the interim tail, never both, so a
fragment still interim at end-of-turn was discarded. On an email address that is
the difference between `john@example.com` and `john@example` — a worthless lead.

These tests are written against the composition contract, not the socket.
"""

from __future__ import annotations

import pytest

from app.infrastructure.stt.transcript_compose import compose_turn_text


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------

def test_trailing_interim_fragment_survives():
    """Test case 1 — the whole reason this ticket exists."""
    assert compose_turn_text(["john at example"], "dot com") == "john at example dot com"


def test_email_fragment_not_truncated():
    finals = ["my email is john dot smith at example"]
    assert compose_turn_text(finals, "dot co dot uk") == (
        "my email is john dot smith at example dot co dot uk"
    )


def test_phone_number_split_across_final_and_tail():
    """Test case 4 — a half-captured phone number is unusable."""
    assert compose_turn_text(["oh seven seven double oh"], "nine hundred one two three") == (
        "oh seven seven double oh nine hundred one two three"
    )


# --------------------------------------------------------------------------
# The trap: overlap must not double up
# --------------------------------------------------------------------------

def test_overlapping_tail_is_deduplicated():
    """Test case 2 — naive concatenation gives 'example.com com'."""
    assert compose_turn_text(["john at example.com"], "example.com") == "john at example.com"


def test_word_level_overlap_merges_once():
    assert compose_turn_text(["john at example dot"], "dot com") == "john at example dot com"


def test_interim_restating_whole_turn_wins():
    """Some interims re-emit the full utterance; prefer the longer, not the sum."""
    assert compose_turn_text(["john at"], "john at example dot com") == "john at example dot com"


def test_stale_interim_identical_to_final_is_dropped():
    assert compose_turn_text(["hello there"], "hello there") == "hello there"


def test_overlap_match_is_case_insensitive():
    assert compose_turn_text(["Call me at Example"], "example dot com") == "Call me at Example dot com"


# --------------------------------------------------------------------------
# Degenerate inputs — must not regress existing behaviour
# --------------------------------------------------------------------------

def test_empty_tail_unchanged():
    """Test case 3."""
    assert compose_turn_text(["hello there"], "") == "hello there"
    assert compose_turn_text(["hello there"], None) == "hello there"


def test_no_finals_returns_interim():
    assert compose_turn_text([], "just an interim") == "just an interim"


def test_both_empty():
    assert compose_turn_text([], "") == ""
    assert compose_turn_text([], None) == ""


def test_multiple_finals_joined_in_order():
    assert compose_turn_text(["one", "two", "three"], "") == "one two three"


def test_blank_and_whitespace_segments_ignored():
    assert compose_turn_text(["one", "   ", "", "two"], "  ") == "one two"


def test_whitespace_is_normalised_at_the_seam():
    assert compose_turn_text(["  hello  "], "  world  ") == "hello world"


# --------------------------------------------------------------------------
# Real-world shapes the ticket calls out explicitly
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "finals,interim,expected",
    [
        (["john dot smith at example dot co dot"], "uk", "john dot smith at example dot co dot uk"),
        (["plus four four seven seven"], "double oh nine hundred", "plus four four seven seven double oh nine hundred"),
        (["it's j o h n at"], "example dot com", "it's j o h n at example dot com"),
        (["sales at acme"], "dot io", "sales at acme dot io"),
    ],
)
def test_real_world_shapes(finals, interim, expected):
    assert compose_turn_text(finals, interim) == expected
