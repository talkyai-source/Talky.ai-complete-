"""The prewarm HOOK that kicks off opening-ladder generation
(telephony.prewarm._start_opening_ladder_generation).

WHAT THIS GUARDS
-----------------
This function is the only place opening_ladder.generate_opening_ladder is
ever invoked from a live call. It must:

  1. Never block the caller — it fires a background asyncio task and returns
     immediately, even against a provider that never responds. The whole
     point (see opening_ladder.py's module docstring) is that ring/warmup
     latency must never depend on this feature.
  2. Only run for caller-first ("user" first-speaker) calls — the opening
     ladder is only ever read on that path (see turn_director.OPENING_
     PHRASES' docstring); generating it for agent-first calls burns an LLM
     round trip nobody reads.
  3. Be a complete no-op (no task, no attribute, no exception) when the flag
     is off, when there's no llm_provider, or when call_session is missing.
  4. On success, stash the generated ladder on ``call_session._opening_ladder``
     — mirroring exactly how ``_presynth_greeting_audio`` /
     ``_presynth_greeting_text`` are stashed elsewhere in this module.
  5. On any failure inside the background task (provider error, validation
     rejection, exception), leave the attribute unset rather than raising —
     the call must proceed exactly as if this feature didn't exist.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.domain.services.telephony.opening_ladder import ENV_FLAG
from app.domain.services.telephony.prewarm import (
    _start_opening_ladder_generation,
)

GOOD_REPLY = "Hello?\nAnyone there?\nIs someone on the line?"


class FakeLLM:
    """Mirrors test_opening_ladder.py's FakeLLM — an async-generator
    ``stream_chat``, like every real provider."""

    def __init__(self, *replies: str, delay: float = 0.0, error: Exception | None = None):
        self.replies = list(replies)
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []

    async def stream_chat(self, messages, system_prompt=None, temperature=None, max_tokens=None, **kwargs):
        self.calls.append({"system_prompt": system_prompt})
        if self.error is not None:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if self.replies else ""
        for piece in reply.split(" "):
            yield piece + " "


def _make_session(llm_provider=None, call_id="test-call-0001", with_call_session=True):
    call_session = types.SimpleNamespace() if with_call_session else None
    return types.SimpleNamespace(
        call_session=call_session,
        llm_provider=llm_provider,
        call_id=call_id,
    )


async def _run_and_settle(pre_warm_session, first_speaker: str) -> set:
    """Invoke the hook, then await whatever background task(s) it spawned so
    the test can inspect the outcome. Returns the set of newly-created tasks
    (empty when the hook correctly declined to start one)."""
    before = asyncio.all_tasks()
    _start_opening_ladder_generation(pre_warm_session, first_speaker)
    after = asyncio.all_tasks()
    new_tasks = after - before
    if new_tasks:
        await asyncio.gather(*new_tasks, return_exceptions=True)
    return new_tasks


@pytest.fixture()
def flag_on(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "true")
    return True


# ===========================================================================
# 1. Non-blocking
# ===========================================================================

async def test_the_hook_returns_immediately_even_with_a_slow_provider(flag_on):
    """The defining property: a provider that would take seconds must not
    make the hook itself take seconds. Measured directly, not inferred."""
    llm = FakeLLM(GOOD_REPLY, delay=5.0)
    session = _make_session(llm_provider=llm)

    loop = asyncio.get_running_loop()
    started = loop.time()
    _start_opening_ladder_generation(session, "user")
    elapsed = loop.time() - started

    assert elapsed < 0.05, "the hook must not await the LLM call itself"
    # The background task is still running at this point — clean it up so it
    # doesn't leak past the test (it would eventually finish on its own via
    # opening_ladder's internal timeout, but there's no reason to wait here).
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


# ===========================================================================
# 2. Gating
# ===========================================================================

async def test_agent_first_calls_never_start_generation(flag_on):
    """The opening ladder is only ever read on caller-first calls — see
    audio_ingest.py's `_opening = _is_caller_first and _prev_user_turns == 0`.
    Firing this for "agent" first-speaker would just waste an LLM call."""
    llm = FakeLLM(GOOD_REPLY)
    session = _make_session(llm_provider=llm)

    new_tasks = await _run_and_settle(session, "agent")

    assert new_tasks == set()
    assert llm.calls == []
    assert not hasattr(session.call_session, "_opening_ladder")


async def test_flag_off_starts_nothing(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    llm = FakeLLM(GOOD_REPLY)
    session = _make_session(llm_provider=llm)

    new_tasks = await _run_and_settle(session, "user")

    assert new_tasks == set()
    assert llm.calls == []
    assert not hasattr(session.call_session, "_opening_ladder")


async def test_no_llm_provider_starts_nothing(flag_on):
    session = _make_session(llm_provider=None)

    new_tasks = await _run_and_settle(session, "user")

    assert new_tasks == set()
    assert not hasattr(session.call_session, "_opening_ladder")


async def test_missing_call_session_does_not_raise(flag_on):
    llm = FakeLLM(GOOD_REPLY)
    session = _make_session(llm_provider=llm, with_call_session=False)

    # Must not raise.
    _start_opening_ladder_generation(session, "user")
    await asyncio.sleep(0)


# ===========================================================================
# 3. Success — stashed exactly where the nudge path reads it
# ===========================================================================

async def test_a_successful_generation_is_stashed_on_call_session(flag_on):
    llm = FakeLLM(GOOD_REPLY)
    session = _make_session(llm_provider=llm)

    await _run_and_settle(session, "user")

    assert session.call_session._opening_ladder == [
        "Hello?", "Anyone there?", "Is someone on the line?",
    ]


# ===========================================================================
# 4. Fail-soft — every failure leaves the attribute unset, never raises
# ===========================================================================

async def test_a_provider_error_leaves_the_attribute_unset(flag_on):
    llm = FakeLLM(error=RuntimeError("provider exploded"))
    session = _make_session(llm_provider=llm)

    # Must not raise all the way out, even though the background task saw an
    # exception.
    await _run_and_settle(session, "user")

    assert not hasattr(session.call_session, "_opening_ladder")


async def test_garbage_output_leaves_the_attribute_unset(flag_on):
    # A ladder that fails validation (stacked greetings) must never be
    # stashed — the nudge path would otherwise read something that broke the
    # greeting-duplication invariant.
    stacked_greetings = "Hello?\nHi, can you hear me?\nIs someone there?"
    llm = FakeLLM(stacked_greetings, stacked_greetings)
    session = _make_session(llm_provider=llm)

    await _run_and_settle(session, "user")

    assert not hasattr(session.call_session, "_opening_ladder")


async def test_a_timeout_leaves_the_attribute_unset(flag_on, monkeypatch):
    from app.domain.services.telephony.opening_ladder import ENV_ATTEMPTS, ENV_TIMEOUT_S

    monkeypatch.setenv(ENV_TIMEOUT_S, "0.05")
    monkeypatch.setenv(ENV_ATTEMPTS, "1")
    llm = FakeLLM(GOOD_REPLY, delay=2.0)
    session = _make_session(llm_provider=llm)

    new_tasks = await _run_and_settle(session, "user")
    assert new_tasks, "a background task should have been started"

    assert not hasattr(session.call_session, "_opening_ladder")
