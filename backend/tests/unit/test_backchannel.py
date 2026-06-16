"""Unit tests for backchannel detection (turn-taking guard)."""
import pytest

from app.domain.services.voice_pipeline.backchannel import (
    is_backchannel,
    is_hard_interrupt,
)


@pytest.mark.parametrize("text", [
    "yeah", "Yeah,", "yep", "ok", "okay", "Okay.", "mm", "mhm", "mm-hm",
    "uh huh", "uh-huh", "right", "sure", "got it", "gotcha", "i see",
    "oh", "ah", "hmm", "cool", "alright", "makes sense", "exactly",
    "of course", "oh ok", "oh right", "yeah yeah", "yeah ok sure",
])
def test_backchannels_detected(text):
    assert is_backchannel(text) is True


@pytest.mark.parametrize("text", [
    # real disagreement / interrupts — must NOT be treated as backchannel
    "no", "nope", "stop", "wait", "hold on",
    # real content / questions
    "yeah but I have a question", "what is the price", "can you explain that",
    "no that's wrong", "actually I disagree", "tell me more about sidekick",
    "yeah so what makes you different from the others",
    "", "   ", "...",
])
def test_non_backchannels(text):
    assert is_backchannel(text) is False


def test_none_safe():
    assert is_backchannel(None) is False  # type: ignore[arg-type]


def test_word_cap():
    # 3 words max for the all-tokens path
    assert is_backchannel("yeah ok sure") is True
    assert is_backchannel("yeah ok sure right") is False


@pytest.mark.parametrize("text", [
    "stop", "Stop.", "wait", "Wait!", "no", "nope", "nah",
    "hold on", "hang on", "no no", "stop stop", "hey", "hello",
    "excuse me", "no thanks", "stop please",
])
def test_hard_interrupts_detected(text):
    # These must always cut in, bypassing the min-words guard.
    assert is_hard_interrupt(text) is True


@pytest.mark.parametrize("text", [
    "yeah", "ok", "mhm", "sure", "right",          # backchannels, not hard interrupts
    "what is the price", "tell me more",            # real turns (handled by min-words/EndOfTurn)
    "", "   ",
])
def test_non_hard_interrupts(text):
    assert is_hard_interrupt(text) is False


def test_hard_interrupt_disjoint_from_backchannel():
    # A word can't be both a backchannel AND a hard interrupt.
    for w in ["stop", "wait", "no", "nope"]:
        assert is_hard_interrupt(w) is True
        assert is_backchannel(w) is False
