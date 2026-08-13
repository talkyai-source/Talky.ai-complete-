"""The agent's reply must survive the cleaning that happens after the LLM.

2026-08-13 production, one call, consecutive turns:

    turn 13  said='Alright, the weather today is actually quite nice.'
    turn 14  said='. The weather today is actually quite nice.'

and elsewhere on the same run, two turns transcribed as literally ``.``.

Root cause: ``^Sure thing[!,]?\\s*`` was the only filler pattern using ``\\s*``
rather than ``\\s+``. ``\\s*`` matches the empty string, so "Sure thing." lost
its filler and kept the full stop — leaving "." — and "Sure thing. The weather
today is actually quite nice." became ". The weather today is actually quite
nice.".

The bare "." was the damaging one. ``turn_streamer`` discarded any sentence
shorter than six characters, so that turn produced NO audio: the model answered
and the answer was deleted between the LLM and the wire. From the caller's side
that is dead air mid-conversation.

The length test was independently wrong. "Yes.", "Okay.", "Sure.", "Got it."
are all under six characters, and the answer-first rule shipped the same week
asks the model to reply plainly in one short sentence — precisely the shape it
deleted.
"""
from __future__ import annotations

import re

import pytest

from app.domain.services.llm_guardrails import LLMGuardrails


@pytest.fixture()
def clean():
    g = LLMGuardrails()
    return lambda t: g.clean_response(t)


# ── the guardrail ────────────────────────────────────────────────────────────

def test_sure_thing_alone_is_not_reduced_to_punctuation(clean):
    """THE BUG. This produced a spoken turn of "." — i.e. silence."""
    out = clean("Sure thing.")
    assert re.search(r"[A-Za-z]", out), f"reply became unspeakable: {out!r}"
    assert out == "Sure thing."


def test_sure_thing_prefix_does_not_orphan_the_full_stop(clean):
    """The exact turn-14 string from production."""
    out = clean("Sure thing. The weather today is actually quite nice.")
    assert not out.startswith("."), f"orphaned punctuation survived: {out!r}"
    assert out == "The weather today is actually quite nice."


@pytest.mark.parametrize("text", [
    "Sure thing! I can help with that.",
    "Sure thing, I can help with that.",
    "Sure thing I can help with that.",
])
def test_the_filler_is_still_stripped_when_it_should_be(clean, text):
    """Non-vacuity — the fix must not simply disable the strip."""
    out = clean(text)
    assert out.lower().startswith("i can help")


def test_sentence_case_is_restored_after_a_strip(clean):
    """"Sure, take your time." used to become "take your time." — which is what
    every persisted transcript and QA review then showed."""
    assert clean("Sure, take your time.") == "Take your time."
    assert clean("Of course, go ahead.") == "Go ahead."


def test_a_filler_only_reply_keeps_its_words(clean):
    """Speaking "Of course." is right. Speaking "." is not, and saying nothing
    is worse than either."""
    for text in ("Of course.", "Absolutely.", "Certainly!"):
        out = clean(text)
        assert re.search(r"[A-Za-z]", out), f"{text!r} -> {out!r}"


def test_replies_without_a_filler_are_untouched(clean):
    """The orphan-punctuation strip is gated on the text having changed, so a
    reply that legitimately opens with punctuation is not rewritten."""
    assert clean("...anyway, where were we?") == "...anyway, where were we?"


def test_the_legacy_dash_strip_still_applies(clean):
    assert clean("— I'm calling about your enquiry.") == "I'm calling about your enquiry."


# ── the downstream guard ─────────────────────────────────────────────────────
#
# turn_streamer decides whether a cleaned sentence is worth sending to TTS.
# Mirrored here as the predicate under test, since the surrounding coroutine
# needs a live LLM stream, a session and a websocket to exercise.

def _is_speakable(sentence: str) -> bool:
    """The production condition, inverted: `if not sentence or not any(
    c.isalnum() for c in sentence): continue`."""
    return bool(sentence) and any(c.isalnum() for c in sentence)


@pytest.mark.parametrize("reply", ["Yes.", "Okay.", "Sure.", "No.", "Got it.", "Mhm."])
def test_short_real_replies_reach_tts(reply):
    """All under the old six-character threshold; all things a person says."""
    assert _is_speakable(reply), f"{reply!r} would have been silently dropped"
    assert len(reply) < 8


@pytest.mark.parametrize("junk", [".", "  ", "", "...", " — ", "!?"])
def test_unspeakable_leftovers_are_still_dropped(junk):
    """Non-vacuity — the guard must still exist, or cleaning artefacts get
    handed to TTS."""
    assert not _is_speakable(junk)


def test_the_two_fixes_compose(clean):
    """End to end: the production input that produced silence now produces a
    sentence that reaches TTS."""
    cleaned = clean("Sure thing.")
    assert _is_speakable(cleaned)
