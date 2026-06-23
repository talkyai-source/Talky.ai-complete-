"""Tests for the first-token deadline + failover on ResilientLLMProvider.

These cover the voice path (``stream_chat_with_timeout``) — distinct from the
original ``stream_chat`` handshake-failover tests in test_resilient_llm.py.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.groq import LLMTimeoutError
from app.domain.services import resilient_llm as rl
from app.domain.services.resilient_llm import (
    LLMFailoverPolicy,
    ResilientLLMProvider,
)


class _TimeoutStub:
    """Stub exposing ``stream_chat_with_timeout`` with controllable timing."""

    def __init__(
        self,
        name: str,
        *,
        tokens: Optional[list[str]] = None,
        first_token_delay: float = 0.0,
        raise_before_first: Optional[Exception] = None,
        raise_after: int = -1,
    ):
        self.name = name
        self.supports_streaming = True
        self._tokens = tokens or []
        self._first_token_delay = first_token_delay
        self._raise_before_first = raise_before_first
        self._raise_after = raise_after
        self.calls = 0  # incremented once the body actually runs (first __anext__)

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def stream_chat_with_timeout(
        self, messages: List[Message], timeout_seconds: float = 10.0, **kwargs
    ) -> AsyncIterator[str]:
        self.calls += 1
        if self._raise_before_first is not None:
            raise self._raise_before_first
        for i, tok in enumerate(self._tokens):
            if i == 0 and self._first_token_delay:
                await asyncio.sleep(self._first_token_delay)
            if self._raise_after >= 0 and i == self._raise_after:
                raise RuntimeError(f"{self.name}: mid-stream drop")
            yield tok


def _msgs():
    return [Message(role=MessageRole.USER, content="hi")]


def _fast_policy(**over):
    # Tight deadline so a "stall" test doesn't actually sleep long.
    base = dict(first_token_deadline_seconds=0.05, failure_threshold=2)
    base.update(over)
    return LLMFailoverPolicy(**base)


async def _drain(provider) -> list[str]:
    out: list[str] = []
    async for tok in provider.stream_chat_with_timeout(_msgs()):
        out.append(tok)
    return out


@pytest.mark.asyncio
async def test_primary_first_token_in_time_uses_primary_only():
    primary = _TimeoutStub("groq", tokens=["a", "b", "c"])
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    assert await _drain(r) == ["a", "b", "c"]
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_stalled_first_token_fails_over():
    # Primary takes 0.5s for the first token; deadline is 0.05s → fail over.
    primary = _TimeoutStub("groq", tokens=["slow"], first_token_delay=0.5)
    secondary = _TimeoutStub("backup", tokens=["x", "y"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    out = await _drain(r)
    assert out == ["x", "y"]          # secondary's stream, not the primary's
    assert "slow" not in out          # no primary token leaked


@pytest.mark.asyncio
async def test_primary_error_before_first_token_fails_over():
    primary = _TimeoutStub("groq", raise_before_first=RuntimeError("429"))
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    assert await _drain(r) == ["x"]


@pytest.mark.asyncio
async def test_commit_on_first_token_no_failover_on_mid_stream_error():
    # Primary yields one token fast, then drops. Must NOT splice in secondary.
    primary = _TimeoutStub("groq", tokens=["a", "b", "c"], raise_after=2)
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    got: list[str] = []
    with pytest.raises(RuntimeError, match="mid-stream"):
        async for tok in r.stream_chat_with_timeout(_msgs()):
            got.append(tok)
    assert got == ["a", "b"]
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_both_miss_raises_llm_timeout():
    primary = _TimeoutStub("groq", tokens=["a"], first_token_delay=0.5)
    secondary = _TimeoutStub("backup", tokens=["b"], first_token_delay=0.5)
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    with pytest.raises(LLMTimeoutError):
        await _drain(r)


@pytest.mark.asyncio
async def test_no_secondary_passes_through_to_primary():
    # Fail-soft: no secondary → no tightened deadline, behave like the primary.
    primary = _TimeoutStub("groq", tokens=["a", "b"])
    r = ResilientLLMProvider(primary, None, policy=_fast_policy())
    assert await _drain(r) == ["a", "b"]


@pytest.mark.asyncio
async def test_clean_zero_token_completion_does_not_failover():
    primary = _TimeoutStub("groq", tokens=[])          # responds fine, says nothing
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    assert await _drain(r) == []
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_breaker_opens_and_routes_straight_to_secondary():
    # failure_threshold=2 → after 2 primary misses the breaker is OPEN and the
    # primary is skipped entirely (no deadline tax) on the next turn.
    primary = _TimeoutStub("groq", tokens=["slow"], first_token_delay=0.5)
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy(failure_threshold=2))
    for _ in range(2):
        assert await _drain(r) == ["x"]
    calls_before = primary.calls
    # Third turn: breaker open → primary body never runs.
    assert await _drain(r) == ["x"]
    assert primary.calls == calls_before


@pytest.mark.asyncio
async def test_failover_outcomes_are_recorded(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(rl, "record_llm_failover", lambda outcome: seen.append(outcome))
    primary = _TimeoutStub("groq", raise_before_first=RuntimeError("boom"))
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy())
    await _drain(r)
    assert "primary_missed" in seen


@pytest.mark.asyncio
async def test_primary_llm_timeout_opens_breaker():
    # audit #7: a primary that raises LLMTimeoutError (its own wall-clock stall —
    # the common real-world degradation) MUST count toward the breaker. Before the
    # fix LLMTimeoutError sat in the breaker's excluded set, so the breaker never
    # opened and every turn kept paying the full first-token deadline before
    # failing over. failure_threshold=2 → after 2 such misses the breaker is OPEN
    # and the primary body is skipped entirely on the next turn.
    primary = _TimeoutStub("groq", raise_before_first=LLMTimeoutError("primary stalled"))
    secondary = _TimeoutStub("backup", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary, policy=_fast_policy(failure_threshold=2))
    for _ in range(2):
        assert await _drain(r) == ["x"]
    calls_before = primary.calls
    assert await _drain(r) == ["x"]       # 3rd turn served by secondary
    assert primary.calls == calls_before  # breaker open → primary body never ran
