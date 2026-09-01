"""The per-turn sentence cap must not cut the turn's question off.

The prompt tells the model "three is a hard ceiling" and asks it to end with
ONE question. When the model writes three statements and then the question,
the streaming cap (turn_streamer) used to drop the fourth sentence — the
caller heard three statements and silence, the exact dead-end turn the
prompt is built to avoid. The cap stays; it may not fall between a statement
and the question that immediately follows it.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.sentence_cap import (
    cap_allows_another,
    truncate_to_cap,
)


def test_under_the_cap_always_allows_more():
    assert cap_allows_another(0, 3, "Anything.", grace_used=False) is True
    assert cap_allows_another(2, 3, "", grace_used=False) is True


def test_no_cap_means_unlimited():
    assert cap_allows_another(99, None, "More?", grace_used=False) is True
    assert cap_allows_another(99, 0, "More?", grace_used=False) is True


def test_at_the_cap_a_following_question_gets_through_once():
    # Three sentences already spoken; the buffer holds the question.
    assert cap_allows_another(3, 3, "What would fix it for you?", grace_used=False) is True
    # ...but only once.
    assert cap_allows_another(4, 3, "And another?", grace_used=True) is False


def test_at_the_cap_a_following_statement_is_cut():
    assert cap_allows_another(3, 3, "We also do same-day payouts.", grace_used=False) is False


def test_at_the_cap_an_incomplete_buffer_is_not_guessed():
    """Mid-stream the next sentence may not have its terminator yet; without a
    '?' in the buffer we must not speculate that a question is coming."""
    assert cap_allows_another(3, 3, "What would", grace_used=False) is False


def test_truncate_keeps_the_question_immediately_after_the_cap():
    text = "One. Two. Three. What would fix it for you?"
    assert truncate_to_cap(text, 3) == "One. Two. Three. What would fix it for you?"


def test_truncate_still_cuts_a_fourth_statement():
    assert truncate_to_cap("One. Two. Three. Four.", 3) == "One. Two. Three."


def test_truncate_does_not_reach_past_one_extra_sentence():
    assert truncate_to_cap("One. Two. Three. Four. Five?", 3) == "One. Two. Three."


def test_truncate_without_cap_is_identity():
    assert truncate_to_cap("One. Two.", None) == "One. Two."
