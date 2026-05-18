"""Tests for app.domain.services.resilient_llm."""
from __future__ import annotations

from typing import AsyncIterator, List, Optional
from unittest.mock import AsyncMock

import pytest

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.resilient_llm import (
    LLMFailoverPolicy,
    ResilientLLMProvider,
)


class _StubLLM(LLMProvider):
    def __init__(self, name: str, *, tokens: list[str] | None = None,
                 raise_on_start: Exception | None = None,
                 raise_after: int = -1):
        self._name = name
        self._tokens = tokens or []
        self._raise_on_start = raise_on_start
        self._raise_after = raise_after
        self.initialized = False
        self.cleaned = False

    async def initialize(self, config: dict) -> None:
        self.initialized = True

    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 150,
        **kwargs,
    ) -> AsyncIterator[str]:
        if self._raise_on_start is not None:
            raise self._raise_on_start
        for i, t in enumerate(self._tokens):
            if self._raise_after >= 0 and i == self._raise_after:
                raise RuntimeError(f"{self._name}: mid-stream drop")
            yield t

    async def cleanup(self) -> None:
        self.cleaned = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def supports_streaming(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_primary_success_does_not_use_secondary():
    primary = _StubLLM("groq", tokens=["a", "b"])
    secondary = _StubLLM("openai", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary)
    out = []
    async for t in r.stream_chat([Message(role=MessageRole.USER, content="hi")]):
        out.append(t)
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_primary_handshake_failure_falls_over_to_secondary():
    primary = _StubLLM("groq", raise_on_start=RuntimeError("auth dead"))
    secondary = _StubLLM("openai", tokens=["x", "y"])
    r = ResilientLLMProvider(primary, secondary)
    out = []
    async for t in r.stream_chat([Message(role=MessageRole.USER, content="hi")]):
        out.append(t)
    assert out == ["x", "y"]


@pytest.mark.asyncio
async def test_mid_stream_failure_does_not_failover():
    primary = _StubLLM("groq", tokens=["a", "b", "c"], raise_after=2)
    secondary = _StubLLM("openai", tokens=["x"])
    r = ResilientLLMProvider(primary, secondary)
    with pytest.raises(RuntimeError, match="mid-stream"):
        async for _ in r.stream_chat([Message(role=MessageRole.USER, content="hi")]):
            pass  # consume; will partially yield then raise


@pytest.mark.asyncio
async def test_handshake_failure_no_secondary_reraises():
    primary = _StubLLM("groq", raise_on_start=RuntimeError("dead"))
    r = ResilientLLMProvider(primary, None)
    with pytest.raises(RuntimeError, match="dead"):
        async for _ in r.stream_chat([Message(role=MessageRole.USER, content="hi")]):
            pass


@pytest.mark.asyncio
async def test_initialize_propagates_to_both():
    primary = _StubLLM("groq")
    secondary = _StubLLM("openai")
    r = ResilientLLMProvider(primary, secondary)
    await r.initialize({})
    assert primary.initialized and secondary.initialized


@pytest.mark.asyncio
async def test_secondary_init_failure_does_not_kill_primary():
    primary = _StubLLM("groq")
    secondary = _StubLLM("openai")
    secondary.initialize = AsyncMock(side_effect=RuntimeError("openai unreachable"))
    r = ResilientLLMProvider(primary, secondary)
    # Must not raise; primary still works alone.
    await r.initialize({})
    assert primary.initialized
    # Secondary was disowned.
    out = []
    async for t in r.stream_chat([Message(role=MessageRole.USER, content="x")]):
        out.append(t)
    assert out == []  # primary has no tokens; secondary disowned
