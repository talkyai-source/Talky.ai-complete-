"""Hybrid email-confirmation LLM fallback (the ambiguous tail). Fail-closed."""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.voice_pipeline.confirm_llm import llm_confirmation_verdict


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
        for ch in self._text:
            yield ch


@pytest.mark.asyncio
async def test_llm_yes_is_affirm():
    v = await llm_confirmation_verdict(_FakeLLM("yes"), "no problem that's correct", "bob@acme.com")
    assert v == "affirm"


@pytest.mark.asyncio
async def test_llm_no_is_reject():
    v = await llm_confirmation_verdict(_FakeLLM("no, the domain is off"), "close but wrong", "bob@acme.com")
    assert v == "reject"


@pytest.mark.asyncio
async def test_llm_unclear_stays_unclear():
    v = await llm_confirmation_verdict(_FakeLLM("unclear"), "hmm", "bob@acme.com")
    assert v == "unclear"


@pytest.mark.asyncio
async def test_missing_provider_fails_closed():
    assert await llm_confirmation_verdict(None, "yes", "bob@acme.com") == "unclear"


@pytest.mark.asyncio
async def test_empty_utterance_fails_closed():
    assert await llm_confirmation_verdict(_FakeLLM("yes"), "", "bob@acme.com") == "unclear"


@pytest.mark.asyncio
async def test_timeout_fails_closed(monkeypatch):
    import app.domain.services.voice_pipeline.confirm_llm as m
    monkeypatch.setattr(m, "_TIMEOUT_S", 0.05)

    class _SlowLLM:
        async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
            await asyncio.sleep(1.0)
            yield "yes"

    assert await llm_confirmation_verdict(_SlowLLM(), "let me see", "bob@acme.com") == "unclear"


@pytest.mark.asyncio
async def test_provider_error_fails_closed():
    class _BoomLLM:
        async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    assert await llm_confirmation_verdict(_BoomLLM(), "sure thing", "bob@acme.com") == "unclear"
