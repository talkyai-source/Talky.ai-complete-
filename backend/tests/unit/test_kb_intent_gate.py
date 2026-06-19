"""Tests for the knowledge-retrieval intent gate.

The gate removes the per-turn DB lookup on content-free backchannels (which
would otherwise add latency before time-to-first-token), while never starving a
real question of knowledge.
"""
from __future__ import annotations

import pytest

from app.domain.services.voice_pipeline.kb_budget import should_retrieve_knowledge


@pytest.mark.parametrize(
    "utterance",
    [
        "okay", "ok", "yeah", "yep", "sure", "right", "got it", "gotcha",
        "makes sense", "sounds good", "mhm", "uh huh", "perfect", "cool",
        "thanks", "thank you", "no", "nope", "alright", "yeah yeah", "okay then",
    ],
)
def test_backchannels_skip_retrieval(utterance):
    assert should_retrieve_knowledge(utterance) is False
    # Trailing punctuation / casing must not change the verdict.
    assert should_retrieve_knowledge(utterance.upper() + "...") is False


@pytest.mark.parametrize(
    "utterance",
    [
        "how much is the premium plan?",
        "what are your hours",
        "do you offer refunds",
        "can I book an appointment",
        "what's the price of the basic package",
        "is there a free trial",
        "tell me about your warranty",
        "got it, and what does enterprise cost?",   # ack + real question
        "okay but how do I cancel",
    ],
)
def test_questions_retrieve(utterance):
    assert should_retrieve_knowledge(utterance) is True


def test_substantive_non_question_retrieves():
    # Longer than a backchannel and not all ack-words → retrieve (safer default).
    assert should_retrieve_knowledge("I want to upgrade my account today") is True


def test_empty_skips():
    assert should_retrieve_knowledge("") is False
    assert should_retrieve_knowledge("   ") is False
    assert should_retrieve_knowledge("...") is False


def test_toggle_off_always_retrieves(monkeypatch):
    import importlib
    import app.domain.services.voice_pipeline.kb_budget as kb
    monkeypatch.setenv("VOICE_KB_SKIP_BACKCHANNELS", "0")
    importlib.reload(kb)
    try:
        assert kb.should_retrieve_knowledge("okay") is True
    finally:
        monkeypatch.delenv("VOICE_KB_SKIP_BACKCHANNELS", raising=False)
        importlib.reload(kb)
