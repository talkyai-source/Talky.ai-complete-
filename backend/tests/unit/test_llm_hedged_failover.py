"""Hedged LLM start (stability audit 2026-10-02).

The sequential failover waited the full first-token deadline in silence before
the secondary started. With a hedge, the secondary starts beside a primary that
is still silent, and whichever speaks first wins.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.resilient_llm import LLMFailoverPolicy, ResilientLLMProvider
from app.infrastructure.llm.groq import LLMTimeoutError


class _Fake:
    def __init__(self, name, delay, tokens=("hi", " there"), error=None):
        self.name = name
        self.delay = delay
        self.tokens = tokens
        self.error = error
        self.started = 0
        self.closed = 0

    supports_streaming = True

    async def stream_chat_with_timeout(self, messages, timeout_seconds=10, **kwargs):
        self.started += 1
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            for t in self.tokens:
                yield t
        finally:
            self.closed += 1


def _wrap(primary, secondary, hedge=0.1, deadline=1.0):
    return ResilientLLMProvider(
        primary, secondary,
        LLMFailoverPolicy(first_token_deadline_seconds=deadline, hedge_after_seconds=hedge),
    )


async def _collect(provider):
    return [t async for t in provider.stream_chat_with_timeout([], timeout_seconds=5)]


@pytest.mark.asyncio
async def test_a_fast_primary_never_starts_the_secondary():
    p, s = _Fake("p", 0.01, ("a", "b")), _Fake("s", 0.01, ("x",))
    assert await _collect(_wrap(p, s)) == ["a", "b"]
    assert s.started == 0


@pytest.mark.asyncio
async def test_a_slow_primary_loses_to_the_secondary_without_a_breaker_failure():
    p, s = _Fake("p", 0.6, ("late",)), _Fake("s", 0.02, ("x", "y"))
    w = _wrap(p, s)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    out = await _collect(w)
    elapsed = loop.time() - t0
    assert out == ["x", "y"]
    assert elapsed < 0.5          # hedge (0.1) + secondary (0.02), not the 1.0 deadline
    assert p.closed == 1          # the loser was stopped
    assert w._breaker._failure_count == 0


@pytest.mark.asyncio
async def test_a_failing_primary_starts_the_secondary_at_once_and_counts_a_failure():
    p, s = _Fake("p", 0.0, error=RuntimeError("boom")), _Fake("s", 0.01, ("x",))
    w = _wrap(p, s, hedge=0.5)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await _collect(w) == ["x"]
    assert loop.time() - t0 < 0.3  # did not wait for the 0.5 hedge
    assert w._breaker._failure_count == 1


@pytest.mark.asyncio
async def test_the_primary_still_wins_if_it_speaks_first_after_the_hedge():
    p, s = _Fake("p", 0.15, ("a",)), _Fake("s", 0.6, ("x",))
    assert await _collect(_wrap(p, s, hedge=0.1)) == ["a"]
    assert s.started == 1 and s.closed == 1  # started, then stopped


@pytest.mark.asyncio
async def test_both_too_slow_raises_the_fallback_signal():
    p, s = _Fake("p", 2.0), _Fake("s", 2.0)
    with pytest.raises(LLMTimeoutError):
        await _collect(_wrap(p, s, hedge=0.05, deadline=0.2))
    assert p.closed == 1 and s.closed == 1


@pytest.mark.asyncio
async def test_without_a_hedge_the_sequential_path_is_unchanged():
    p, s = _Fake("p", 0.3, ("late",)), _Fake("s", 0.01, ("x",))
    w = ResilientLLMProvider(p, s, LLMFailoverPolicy(first_token_deadline_seconds=0.1))
    assert await _collect(w) == ["x"]
    assert w._breaker._failure_count == 1
