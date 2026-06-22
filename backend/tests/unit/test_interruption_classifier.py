"""Tests for the interruption classifier (gap #2)."""
from __future__ import annotations

import pytest

from app.services.scripts.interruption_classifier import (
    InterruptionType,
    classify_interruption,
    is_false_interruption,
)


@pytest.mark.parametrize("digit", ["1", "0", "#", "*"])
def test_dtmf_wins_regardless_of_text(digit):
    assert classify_interruption("anything at all", dtmf=digit) is InterruptionType.DTMF
    assert classify_interruption("", dtmf=digit) is InterruptionType.DTMF


@pytest.mark.parametrize(
    "text",
    ["yeah", "uh huh", "mm hmm", "okay", "right", "got it", "sure", "yep"],
)
def test_backchannels(text):
    assert classify_interruption(text) is InterruptionType.BACKCHANNEL


@pytest.mark.parametrize(
    "text",
    [
        "I want to speak to a real person",
        "give me a representative",
        "let me talk to your manager",
        "can you transfer me to a human",   # has '?'-less human request
        "please take me off your list",
        "stop calling me",
        "do not call this number again",
        "just leave me alone",
    ],
)
def test_escalations(text):
    assert classify_interruption(text) is InterruptionType.ESCALATION


@pytest.mark.parametrize(
    "text",
    [
        "no, that's not right",
        "actually I meant Friday",
        "wait, go back",
        "I said Tuesday not Thursday",
        "that is wrong",
        "hold on let me finish",
    ],
)
def test_corrections(text):
    assert classify_interruption(text) is InterruptionType.CORRECTION


@pytest.mark.parametrize(
    "text",
    [
        "how much is it?",
        "what's the price",
        "can you repeat that",
        "is there a discount?",
        "would that include installation",
        "do you have weekend slots",
    ],
)
def test_questions(text):
    assert classify_interruption(text) is InterruptionType.QUESTION


@pytest.mark.parametrize("text", ["", "   ", "x", "?!", ".."])
def test_noise(text):
    assert classify_interruption(text) is InterruptionType.NOISE


@pytest.mark.parametrize(
    "text",
    [
        "I already have solar panels installed",
        "we're good for now thanks",
        "my budget is around two thousand dollars",
    ],
)
def test_statements(text):
    assert classify_interruption(text) is InterruptionType.STATEMENT


def test_false_interruption_set():
    assert is_false_interruption(InterruptionType.BACKCHANNEL) is True
    assert is_false_interruption(InterruptionType.NOISE) is True
    for t in (
        InterruptionType.CORRECTION,
        InterruptionType.QUESTION,
        InterruptionType.ESCALATION,
        InterruptionType.DTMF,
        InterruptionType.STATEMENT,
    ):
        assert is_false_interruption(t) is False


def test_record_interruption_is_safe():
    from app.infrastructure.metrics.voice_metrics import record_interruption

    # Valid + unknown type must both record without raising.
    record_interruption("backchannel", false_interrupt=True)
    record_interruption("statement", false_interrupt=False)
    record_interruption("bogus-type", false_interrupt=False)  # coerced to "other"
