"""The LLM-authored opening silence ladder: it must vary, escalate, and stay
silent-line-only — or the static ladder is used instead.

WHY THIS FEATURE EXISTS
------------------------
``turn_director.OPENING_PHRASES`` is a fixed 3-line script spoken byte-for-
byte on every single call. A real person re-checking a silent pickup does not
recite a script — they say something short, then escalate a little more each
time, in their own words. This module authors that ladder once per call
(during pre-warm, never on the nudge path) and validates it before it is ever
allowed anywhere near a live call.

WHAT THESE TESTS GUARD
------------------------
  * OFF BY DEFAULT — no LLM call happens until an owner opts in.
  * The greeting-duplication invariant this feature must never reintroduce:
    exactly ONE rung may open with a greeting word, and it must be rung 1
    (see tests/unit/test_greeting_duplication.py, which pins the identical
    rule on the static ladder this feature can fall back to).
  * 1-6 words per rung, no pitch, no self-introduction, no business question.
  * The ladder must actually escalate (never shrink rung to rung) and vary
    (no duplicate rungs).
  * Every failure path — timeout, provider error, garbage output, flag off —
    returns None, never raises, and never blocks.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.telephony.opening_ladder import (
    ENV_ATTEMPTS,
    ENV_FLAG,
    ENV_TIMEOUT_S,
    MAX_RUNG_CHARS,
    MAX_RUNG_WORDS,
    LadderRejected,
    build_ladder_prompt,
    generate_opening_ladder,
    opening_ladder_enabled,
    rung_count,
    validate_ladder,
)

# A ladder that satisfies every rule: 1-6 words, only rung 1 greets, strictly
# escalating character length, no banned content, no duplicates.
GOOD_LADDER = ["Hello?", "Anyone there?", "Is someone on the line?"]


def _rejects(ladder: list[str], expected_count: int = 3) -> str:
    with pytest.raises(LadderRejected) as excinfo:
        validate_ladder(ladder, expected_count=expected_count)
    return str(excinfo.value)


# ===========================================================================
# 1. OFF BY DEFAULT
# ===========================================================================

def test_the_feature_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert opening_ladder_enabled() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe", "TRUEish"])
def test_only_a_real_truthy_value_enables_it(monkeypatch, value):
    monkeypatch.setenv(ENV_FLAG, value)
    assert opening_ladder_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_recognised_truthy_values(monkeypatch, value):
    monkeypatch.setenv(ENV_FLAG, value)
    assert opening_ladder_enabled() is True


async def test_with_the_flag_off_the_llm_is_never_called(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    llm = FakeLLM("Hello?\nAnyone there?\nIs someone on the line?")
    result = await generate_opening_ladder(llm_provider=llm, call_id="c1")
    assert result is None
    assert llm.calls == [], "the flag must be checked before any inference"


async def test_no_provider_returns_none_without_crashing(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "true")
    assert await generate_opening_ladder(llm_provider=None, call_id="c1") is None


# ===========================================================================
# 2. rung_count() matches VOICE_OPENING_MAX_NUDGES
# ===========================================================================

def test_rung_count_defaults_to_three(monkeypatch):
    monkeypatch.delenv("VOICE_OPENING_MAX_NUDGES", raising=False)
    assert rung_count() == 3


def test_rung_count_follows_the_nudge_cap_env_var(monkeypatch):
    # Same env var audio_ingest.py's silence monitor reads for its nudge cap
    # — an operator who widens the cap gets a matching ladder for free.
    monkeypatch.setenv("VOICE_OPENING_MAX_NUDGES", "5")
    assert rung_count() == 5


def test_rung_count_falls_back_on_garbage_value(monkeypatch):
    monkeypatch.setenv("VOICE_OPENING_MAX_NUDGES", "not-a-number")
    assert rung_count() == 3


def test_rung_count_falls_back_on_non_positive_value(monkeypatch):
    monkeypatch.setenv("VOICE_OPENING_MAX_NUDGES", "0")
    assert rung_count() == 3


# ===========================================================================
# 3. validate_ladder — the whole safety story
# ===========================================================================

def test_a_good_ladder_is_accepted():
    assert validate_ladder(GOOD_LADDER, expected_count=3) == GOOD_LADDER


def test_wrong_rung_count_is_refused():
    reason = _rejects(["Hello?", "Anyone there?"], expected_count=3)
    assert "2 rungs" in reason


def test_too_many_rungs_is_refused():
    reason = _rejects(GOOD_LADDER + ["One more?"], expected_count=3)
    assert "4 rungs" in reason


def test_a_rung_over_the_word_budget_is_refused():
    assert MAX_RUNG_WORDS == 6
    # Exactly 40 chars (at MAX_RUNG_CHARS, so the length check does not fire
    # first) and 7 words (one over budget) — isolates the word-count check.
    over_budget = "Is there really truly someone there now?"
    assert len(over_budget) == 40
    reason = _rejects(["Hello?", "Anyone there?", over_budget])
    assert "7 words" in reason


def test_a_rung_with_no_words_is_refused():
    reason = _rejects(["Hello?", "??", "Is someone on the line?"])
    assert "words" in reason or "empty" in reason


def test_a_pathologically_long_rung_is_refused():
    absurd = "x" * (MAX_RUNG_CHARS + 1) + "?"
    reason = _rejects(["Hello?", "Anyone there?", absurd])
    assert "chars" in reason


@pytest.mark.parametrize("bad_char", ["*", "_", "#", "`", "{", "}", "<", ">", "|"])
def test_non_speakable_characters_are_refused(bad_char):
    reason = _rejects(["Hello?", "Anyone there?", f"Is{bad_char}someone there?"])
    assert "non-speakable" in reason


# ── pitch / introduction / business-question bans ──────────────────────────

@pytest.mark.parametrize(
    "pitchy_rung",
    [
        "This is Sarah calling?",
        "My name is James?",
        "I'm calling about your account?",
        "How can I help you today?",
        "Is this a good time?",
        "Cold call, got a second?",
        "Interested in a better deal?",
        "This is an automated call?",
    ],
)
def test_pitch_and_introduction_language_is_refused(pitchy_rung):
    """A silence check must never become an introduction or a pitch — this is
    the entire reason opening_ladder.py never gives the model any tenant/
    campaign context to begin with (see the module docstring)."""
    reason = _rejects(["Hello?", pitchy_rung, "Is someone on the line?"])
    assert "language" in reason or "question" in reason or "introduction" in reason


# ── the greeting-duplication invariant (test_greeting_duplication.py's rule) ─

def test_only_rung_one_may_greet():
    assert validate_ladder(GOOD_LADDER, expected_count=3)[0] == "Hello?"


def test_control_a_second_greeting_rung_is_refused():
    """POSITIVE CONTROL — mirrors the exact production bug
    test_greeting_duplication.py pins on the static ladder: a second greeting
    word starting a later rung must be caught here too."""
    stacked = ["Hello?", "Hi, can you hear me?", "Is someone on the line?"]
    reason = _rejects(stacked)
    assert "greeting rungs at [0, 1]" in reason


def test_control_a_third_rung_greeting_is_also_refused():
    stacked = ["Hello?", "Anyone there?", "Hey, is someone there?"]
    reason = _rejects(stacked)
    assert "greeting rungs at [0, 2]" in reason


def test_a_first_rung_that_is_not_a_greeting_is_refused():
    """Requirement 4: rung 1 must be a plain greeting. A ladder that opens
    with something else is rejected — greeting_idxs comes back empty, not
    [0]."""
    no_greeting = ["Still on the line?", "Anyone there?", "Is someone there really?"]
    reason = _rejects(no_greeting)
    assert "greeting rungs at []" in reason


# ── variety + escalation ────────────────────────────────────────────────────

def test_duplicate_rungs_are_refused():
    dup = ["Hello?", "Anyone there?", "Anyone there?"]
    reason = _rejects(dup)
    assert "duplicate" in reason


def test_duplicate_rungs_are_refused_case_insensitively():
    # Same text, different case — a model quirk that would otherwise slip
    # past a naive exact-string check.
    reason = _rejects(["Hello?", "Anyone there?", "anyone there?"])
    assert "duplicate" in reason


def test_a_shrinking_ladder_is_refused():
    # Monotonically decreasing character length: 33 -> 7 -> 4. Chosen so
    # rung 1 IS the (only) greeting rung and no rung's word count exceeds the
    # budget — isolating the escalation check from the other checks.
    shrinking = ["Hello, is anyone actually there?", "Anyone?", "You?"]
    reason = _rejects(shrinking)
    assert "shorter" in reason


def test_a_flat_ladder_that_never_escalates_is_refused():
    # Every rung is exactly 6 characters — passes the "never shrinks"
    # rung-to-rung check (nothing gets shorter) but fails the "last rung
    # must be more insistent than the first" check.
    flat = ["Hello?", "Still?", "There?"]
    reason = _rejects(flat)
    assert "no more insistent" in reason


def test_the_worked_example_in_the_docstring_is_valid():
    """The exact 'shape' example this feature is built around — length grows
    monotonically at every step, only rung 1 greets."""
    example = ["Hello?", "Anyone there?", "Is someone on the line?"]
    assert validate_ladder(example, expected_count=3) == example


def test_control_the_literal_task_example_middle_rung_would_be_refused():
    """POSITIVE CONTROL: the illustrative 'Hello? -> Hello?? -> Helloooo...'
    example given as motivation for this feature has a middle rung that
    re-greets ('Hello??' starts with the literal word 'Hello' followed
    immediately by punctuation, which the greeting regex catches) — this
    documents why the in-prompt example was changed to a self-consistent one
    that never repeats a greeting word after rung 1 (see the module
    docstring's NOTE)."""
    literal_example = ["Hello?", "Hello??", "Helloooo, are you there?"]
    reason = _rejects(literal_example)
    assert "greeting rungs at [0, 1]" in reason


# ===========================================================================
# 4. build_ladder_prompt — no tenant context, ever
# ===========================================================================

def test_prompt_never_mentions_a_company_or_agent_placeholder():
    system, user = build_ladder_prompt(3)
    # This prompt is deliberately context-free (see the module docstring) —
    # unlike llm_opener.py it never receives agent_name/company_name/
    # campaign_slots, so there is nothing for it to leak.
    assert "{agent_name}" not in system
    assert "{company_name}" not in system
    assert "EXACTLY 3" in system
    assert user == "Write the ladder now."


def test_prompt_example_is_internally_consistent_with_its_own_hard_rule():
    """The in-prompt worked example must not contradict the 'only line 1 may
    greet' rule it also states — see the module docstring's NOTE explaining
    why the illustrative feature-motivation example was not used verbatim."""
    system, _ = build_ladder_prompt(3)
    # crude but sufficient: the example block's 2nd/3rd lines must not open
    # with a bare greeting word.
    import re

    example_lines = re.findall(r'^\s{4}"(.+?)"\s*$', system, re.MULTILINE)
    assert len(example_lines) == 3
    from app.domain.services.telephony.opening_ladder import _GREETING_RUNG

    assert _GREETING_RUNG.match(example_lines[0])
    assert not _GREETING_RUNG.match(example_lines[1])
    assert not _GREETING_RUNG.match(example_lines[2])


def test_retry_prompt_carries_the_rejection_reason():
    system, _ = build_ladder_prompt(3, rejection="rung 2 has 9 words")
    assert "REJECTED" in system
    assert "rung 2 has 9 words" in system


# ===========================================================================
# 5. generate_opening_ladder — end to end with a fake provider
# ===========================================================================

class FakeLLM:
    """Stands in for ``llm_provider``. ``stream_chat`` is an async GENERATOR,
    like every real provider, so the ``aclose()`` cleanup path is exercised
    for real rather than mocked away. Mirrors test_llm_opener.py's FakeLLM."""

    def __init__(self, *replies: str, delay: float = 0.0, error: Exception | None = None):
        self.replies = list(replies)
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []

    async def stream_chat(self, messages, system_prompt=None, temperature=None, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if self.replies else ""
        for piece in reply.split(" "):
            yield piece + " "


GOOD_REPLY = "Hello?\nAnyone there?\nIs someone on the line?"


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "true")
    return True


async def test_a_good_reply_is_returned_and_validated(flag_on):
    llm = FakeLLM(GOOD_REPLY)
    result = await generate_opening_ladder(llm_provider=llm, call_id="test-call-1")
    assert result == ["Hello?", "Anyone there?", "Is someone on the line?"]
    assert len(llm.calls) == 1


async def test_a_provider_exception_falls_back_to_none(flag_on):
    llm = FakeLLM(error=RuntimeError("provider down"))
    assert await generate_opening_ladder(llm_provider=llm, call_id="c") is None


async def test_a_slow_provider_times_out_and_falls_back_to_none(flag_on, monkeypatch):
    monkeypatch.setenv(ENV_TIMEOUT_S, "0.1")
    monkeypatch.setenv(ENV_ATTEMPTS, "1")
    llm = FakeLLM(GOOD_REPLY, delay=1.0)
    result = await generate_opening_ladder(llm_provider=llm, call_id="c")
    assert result is None


async def test_garbage_output_is_rejected_not_spoken(flag_on, monkeypatch):
    """A ladder that fails validation on every attempt must fall back to None
    — never a partially-validated or repaired ladder."""
    monkeypatch.setenv(ENV_ATTEMPTS, "2")
    stacked_greetings = "Hello?\nHi, can you hear me?\nIs someone there?"
    llm = FakeLLM(stacked_greetings, stacked_greetings)
    result = await generate_opening_ladder(llm_provider=llm, call_id="c")
    assert result is None
    assert len(llm.calls) == 2, "should retry up to the attempts budget"


async def test_a_retry_recovers_after_one_bad_attempt(flag_on, monkeypatch):
    monkeypatch.setenv(ENV_ATTEMPTS, "2")
    stacked_greetings = "Hello?\nHi, can you hear me?\nIs someone there?"
    llm = FakeLLM(stacked_greetings, GOOD_REPLY)
    result = await generate_opening_ladder(llm_provider=llm, call_id="c")
    assert result == ["Hello?", "Anyone there?", "Is someone on the line?"]
    assert len(llm.calls) == 2
    # The retry prompt must carry the specific rejection reason forward.
    assert "REJECTED" in llm.calls[1]["system_prompt"]


async def test_empty_reply_falls_back_to_none(flag_on):
    llm = FakeLLM("")
    assert await generate_opening_ladder(llm_provider=llm, call_id="c") is None


async def test_rungs_override_matches_a_non_default_nudge_cap(flag_on):
    # Character length strictly increases at every step (6 -> 13 -> 17 -> 22
    # -> 35), only rung 1 greets, and every rung stays inside the 1-6 word
    # budget — a valid 5-rung ladder for a widened VOICE_OPENING_MAX_NUDGES.
    five_rung_reply = (
        "Hello?\nAnyone there?\nIs anyone there?\nIs someone listening?\n"
        "Is anyone actually there right now?"
    )
    llm = FakeLLM(five_rung_reply)
    result = await generate_opening_ladder(llm_provider=llm, rungs=5, call_id="c")
    assert result is not None
    assert len(result) == 5


async def test_cancellation_propagates_instead_of_being_swallowed(flag_on):
    """A background task hosting this call (see prewarm.py) must remain
    cancellable — swallowing CancelledError here would leave a task that
    can't be torn down."""
    llm = FakeLLM(GOOD_REPLY, delay=5.0)

    task = asyncio.ensure_future(generate_opening_ladder(llm_provider=llm, call_id="c"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
