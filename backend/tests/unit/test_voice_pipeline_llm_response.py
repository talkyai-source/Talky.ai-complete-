"""Unit tests for voice_pipeline.llm_response (item 2, slice 2)."""
from __future__ import annotations

import types

import pytest

from app.domain.services.voice_pipeline.llm_response import (
    response_max_sentences_for_turn,
    generate_llm_response,
)


# ── response_max_sentences_for_turn (pure) ───────────────────────────────────

def test_turn_id_shape():
    assert response_max_sentences_for_turn(0) == 2      # first turn capped at 2
    assert response_max_sentences_for_turn(3) is None   # later turns uncapped


def test_session_shape_default_limit():
    session = types.SimpleNamespace(agent_config=types.SimpleNamespace(response_max_sentences=2))
    assert response_max_sentences_for_turn(session, "hello there") == 2


def test_pricing_question_bumps_limit_with_custom_prompt():
    session = types.SimpleNamespace(agent_config=types.SimpleNamespace(response_max_sentences=2))
    # custom prompt + pricing intent → relax to >=4
    assert response_max_sentences_for_turn(session, "what is your pricing", has_custom_prompt=True) == 4
    # without custom prompt, no bump
    assert response_max_sentences_for_turn(session, "what is your pricing", has_custom_prompt=False) == 2


# ── generate_llm_response (with a CORRECT async-generator mock) ───────────────

class _FakeLLM:
    """stream_chat_with_timeout must be an async generator (NOT an AsyncMock
    returning a coroutine — that is the bug in the integration suite)."""
    def __init__(self, tokens):
        self._tokens = tokens

    async def stream_chat_with_timeout(self, messages, system_prompt=None, **kwargs):
        # **kwargs mirrors the real signature (temperature/max_tokens are now
        # passed per turn from the session's AI-Options config).
        for t in self._tokens:
            yield t


class _FakeLatency:
    def __init__(self):
        self.first_token_marked = False

    def mark_llm_first_token(self, call_id):
        self.first_token_marked = True


def _session():
    return types.SimpleNamespace(
        call_id="test-call-1",
        conversation_history=[],
        system_prompt="You are a helpful agent.",
        captured_slots=None,
        agent_config=types.SimpleNamespace(response_max_sentences=2),
    )


@pytest.mark.asyncio
async def test_generate_streams_and_marks_first_token():
    llm = _FakeLLM(["Hello", " there."])
    latency = _FakeLatency()
    out = await generate_llm_response(llm, latency, _session(), "hi")
    assert "Hello there." in out
    assert latency.first_token_marked is True


@pytest.mark.asyncio
async def test_generate_falls_back_on_error():
    class _BoomLLM:
        async def stream_chat_with_timeout(self, messages, system_prompt=None):
            raise RuntimeError("provider down")
            yield  # pragma: no cover - makes this an async generator

    out = await generate_llm_response(_BoomLLM(), _FakeLatency(), _session(), "hi")
    assert "I'm sorry" in out
