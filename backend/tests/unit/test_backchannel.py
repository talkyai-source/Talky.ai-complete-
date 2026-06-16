"""Unit tests for backchannel detection (turn-taking guard)."""
import pytest

from app.domain.services.voice_pipeline.backchannel import is_backchannel


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
