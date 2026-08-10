"""Unit tests for the pure turn-taking phrase/suppression logic.

Covers the two production bugs turn_director.py was split out to fix
(2026-07-08): an agent-first call's first line being a needy mid-nudge
before the caller ever spoke, and "Sorry, did I lose you?" looping instead
of escalating through a neutral ladder.
"""
import pytest

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
    # 2026-08-11: escalates by calling the SAME word again, louder — which is
    # what a person on a silent line actually does. The previous rung here was
    # "Can you hear me okay?", flagged by the owner as stilted: nobody asks a
    # formal audio-quality question when nobody has picked up.
    assert choose_silence_phrase(is_opening=True, nudge_index=1) == "Hello??"


def test_opening_third_nudge_escalates_again_without_a_FRESH_greeting():
    # Three rungs to match _OPENING_MAX_NUDGES=3 (a human re-checks 2-3 times,
    # not once). Repeating rung 0's word is fine and intended; what must never
    # appear is a DIFFERENT greeting word, which reads as starting the
    # conversation over — see test_greeting_duplication.py's refined
    # _fresh_greeting_rung_indexes rule.
    assert choose_silence_phrase(is_opening=True, nudge_index=2) == "Helloooo — are you there?"


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


# ── choose_silence_phrase: the optional per-call `ladder` (2026-08-11) ─────
#
# telephony.opening_ladder can stash an LLM-generated, per-call ladder on the
# session (see prewarm._start_opening_ladder_generation); audio_ingest.py's
# silence monitor reads it with getattr(session, "_opening_ladder", None) and
# hands it straight to this function. Every test above this section already
# proves the DEFAULT (no `ladder` argument) is byte-for-byte unchanged; these
# tests cover the new argument itself.

_GENERATED = ["Hello?", "Anyone there?", "Is someone on the line?"]


def test_ladder_argument_is_used_when_provided_for_opening():
    assert choose_silence_phrase(
        is_opening=True, nudge_index=0, ladder=_GENERATED,
    ) == "Hello?"
    assert choose_silence_phrase(
        is_opening=True, nudge_index=1, ladder=_GENERATED,
    ) == "Anyone there?"
    assert choose_silence_phrase(
        is_opening=True, nudge_index=2, ladder=_GENERATED,
    ) == "Is someone on the line?"


def test_ladder_argument_clamps_to_its_own_last_rung():
    # A 2-rung generated ladder (a shorter VOICE_OPENING_MAX_NUDGES) must
    # clamp against ITS length, not the static ladder's.
    short = ["Hello?", "Anyone there?"]
    assert choose_silence_phrase(
        is_opening=True, nudge_index=5, ladder=short,
    ) == "Anyone there?"


@pytest.mark.parametrize("bad_ladder", [None, []])
def test_none_or_empty_ladder_falls_back_to_the_static_ladder(bad_ladder):
    # None (generation never ran / hasn't landed yet) and [] (defensive) must
    # both degrade to OPENING_PHRASES, never raise and never return "".
    assert choose_silence_phrase(
        is_opening=True, nudge_index=0, ladder=bad_ladder,
    ) == OPENING_PHRASES[0]
    assert choose_silence_phrase(
        is_opening=True, nudge_index=1, ladder=bad_ladder,
    ) == OPENING_PHRASES[1]


def test_ladder_argument_is_ignored_on_the_mid_tier():
    # This feature only ever touches the OPENING ladder (see turn_director's
    # module docstring) — a `ladder` passed alongside is_opening=False must
    # be ignored, not accidentally applied to MID nudges.
    assert choose_silence_phrase(
        is_opening=False, nudge_index=0, ladder=_GENERATED,
    ) == MID_PHRASES[0]


def test_a_non_iterable_ladder_falls_back_to_the_static_ladder():
    # Defense in depth: choose_silence_phrase must never raise because of a
    # bad `ladder` value — by the time a nudge fires the caller is already
    # sitting in silence, so a generation-time problem must never cost one.
    # In practice `ladder` is always None or the validated list[str]
    # opening_ladder.generate_opening_ladder returns, so this can't happen
    # through the real call path — it's covered anyway.
    assert choose_silence_phrase(
        is_opening=True, nudge_index=0, ladder=12345,
    ) == OPENING_PHRASES[0]


def test_default_call_sites_are_completely_unaffected():
    """Regression pin: every pre-existing call site (audio_ingest.py's normal
    path when no generated ladder is stashed, and this module's own tests
    above) calls choose_silence_phrase with no `ladder` kwarg at all. Adding
    the parameter must not change that call shape's behaviour one bit."""
    assert choose_silence_phrase(is_opening=True, nudge_index=0) == "Hello?"
    assert choose_silence_phrase(is_opening=False, nudge_index=0) == "Still there?"
