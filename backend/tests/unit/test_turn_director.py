"""Unit tests for the pure turn-taking phrase/suppression logic.

Covers the two production bugs turn_director.py was split out to fix
(2026-07-08): an agent-first call's first line being a needy mid-nudge
before the caller ever spoke, and "Sorry, did I lose you?" looping instead
of escalating through a neutral ladder.
"""
from app.domain.services.voice_pipeline.turn_director import (
    MID_PHRASES,
    OPENING_PHRASES,
    choose_silence_phrase,
    should_suppress_mid_nudge,
)


# ── choose_silence_phrase: opening ladder ───────────────────────────────
def test_opening_first_nudge_is_hello():
    assert choose_silence_phrase(is_opening=True, nudge_index=0) == "Hello?"


def test_opening_second_nudge_escalates():
    assert choose_silence_phrase(is_opening=True, nudge_index=1) == "Hi, can you hear me okay?"


def test_opening_index_clamps_to_last_rung():
    # A hypothetical higher _MAX_NUDGES must not raise or wrap around.
    assert choose_silence_phrase(is_opening=True, nudge_index=99) == OPENING_PHRASES[-1]


def test_opening_negative_index_clamps_to_first_rung():
    assert choose_silence_phrase(is_opening=True, nudge_index=-1) == OPENING_PHRASES[0]


# ── choose_silence_phrase: mid ladder ───────────────────────────────────
def test_mid_first_nudge_is_neutral_still_there():
    assert choose_silence_phrase(is_opening=False, nudge_index=0) == "Still there?"


def test_mid_second_nudge_is_warm_reoffer():
    assert (
        choose_silence_phrase(is_opening=False, nudge_index=1)
        == "No rush — I'm still on the line whenever you're ready."
    )


def test_mid_index_clamps_to_last_rung():
    assert choose_silence_phrase(is_opening=False, nudge_index=99) == MID_PHRASES[-1]


def test_never_uses_did_i_lose_you_phrasing():
    # The needy/accusatory phrase that caused the production nag loop must
    # never appear on either ladder, at any tier.
    all_phrases = OPENING_PHRASES + MID_PHRASES
    assert not any("lose you" in p.lower() for p in all_phrases)


def test_mid_ladder_never_repeats_i_am_still_here_as_first_line():
    # First mid rung must not be the needy "I'm still here..." line — that
    # was the exact bug: it landing as the caller's very first line.
    assert MID_PHRASES[0] != "I'm still here whenever you're ready."


# ── should_suppress_mid_nudge ───────────────────────────────────────────
def test_suppresses_mid_nudge_on_agent_first_call_caller_never_spoke():
    assert should_suppress_mid_nudge(
        is_caller_first=False, caller_has_ever_spoken=False
    ) is True


def test_does_not_suppress_once_caller_has_spoken():
    assert should_suppress_mid_nudge(
        is_caller_first=False, caller_has_ever_spoken=True
    ) is False


def test_does_not_suppress_on_caller_first_calls():
    # Caller-first calls use the OPENING ladder, not MID; this helper should
    # never suppress them (that's not its job — it's opening-vs-mid routing
    # in audio_ingest.py that keeps caller-first calls off this path).
    assert should_suppress_mid_nudge(
        is_caller_first=True, caller_has_ever_spoken=False
    ) is False
