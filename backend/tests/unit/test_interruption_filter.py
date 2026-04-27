"""Unit tests for the backchannel filter.

Covers the examples documented in the filter's docstring plus a few
edge cases that surfaced during source-file review.
"""
from __future__ import annotations

import pytest

from app.services.scripts.interruption_filter import is_backchannel


@pytest.mark.parametrize(
    "text",
    [
        "hmm", "hm", "mm", "mmm", "mhm",
        "yeah", "yep", "yup", "okay", "ok",
        "right", "alright", "sure", "got it", "gotcha",
        "uh huh", "uh-huh",
        "oh", "ah", "i see",
        "yeah okay",
        "no", "nope", "nah",
        # Trailing punctuation must not break detection.
        "yeah.", "okay,", "hmm?",
    ],
)
def test_backchannels(text: str):
    assert is_backchannel(text), f"{text!r} should be flagged as a backchannel"


@pytest.mark.parametrize(
    "text",
    [
        "yeah but what about the price",
        "my email is john@gmail.com",
        "hmm I am not sure about that",
        "no I already have solar",
        "okay so when can you come",
        # Empty / whitespace — the caller said nothing real.
        "", "   ",
    ],
)
def test_not_backchannels(text: str):
    assert not is_backchannel(text), f"{text!r} should NOT be a backchannel"


def test_long_transcript_always_real():
    long_text = "yeah " * 10
    assert not is_backchannel(long_text)
