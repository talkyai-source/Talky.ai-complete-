"""Tests for GeminiLLMProvider.stream_chat_with_tools — native function-calling
2-round orchestration (parity with Groq's tool path).

The test env stubs `google.genai.types` with a minimal SimpleNamespace (see
test_gemini_llm). These tests install an extended stub (function-calling types)
on the package for the duration of the test, and a fake streaming transport, so
no SDK/network is needed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.domain.services.voice_pipeline.knowledge_tool import KNOWLEDGE_TOOL_SPEC


# ── fake streaming transport ────────────────────────────────────────────────
class _FakeChunk:
    def __init__(self, text=None, function_calls=None):
        self.candidates = (
            [SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))]
            if text else None
        )
        self.function_calls = function_calls or []


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _provider_with_streams(streams):
    p = GeminiLLMProvider()
    aio = SimpleNamespace(models=SimpleNamespace())

    async def _gen(*a, **k):
        return streams.pop(0)

    aio.models.generate_content_stream = _gen
    p._client = SimpleNamespace(aio=aio)
    return p


def _run(agen):
    async def _collect():
        return [tok async for tok in agen]
    return asyncio.run(_collect())


class _StubPart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call

    @staticmethod
    def from_function_response(name=None, response=None):
        return _StubPart()


@pytest.fixture
def rich_genai(monkeypatch):
    """Install function-calling types on the stubbed google.genai package."""
    import google.genai as genai_pkg
    rich = SimpleNamespace(
        Content=lambda role=None, parts=None: SimpleNamespace(role=role, parts=parts),
        Part=_StubPart,
        Tool=lambda function_declarations=None: SimpleNamespace(
            function_declarations=function_declarations),
        FunctionDeclaration=lambda **kw: SimpleNamespace(**kw),
        FunctionCall=lambda name=None, args=None: SimpleNamespace(name=name, args=args),
        GenerateContentConfig=lambda **kw: SimpleNamespace(**kw),
        ThinkingConfig=lambda **kw: SimpleNamespace(**kw),
    )
    monkeypatch.setattr(genai_pkg, "types", rich, raising=False)
    return rich


# ── tests ───────────────────────────────────────────────────────────────────
def test_no_tools_delegates_to_normal_stream():
    p = GeminiLLMProvider()

    async def fake_sct(messages, **kw):
        for t in ["Hi ", "there."]:
            yield t

    p.stream_chat_with_timeout = fake_sct
    out = _run(p.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="hello")], tools=None, tool_runner=None))
    assert out == ["Hi ", "there."]


def test_direct_answer_skips_tool(rich_genai):
    # Round 0 returns text and no function_call → answered directly, tool unused.
    p = _provider_with_streams([_FakeStream([_FakeChunk(text="We're open till five.")])])
    called = {"n": 0}

    async def runner(name, args):
        called["n"] += 1
        return "nope"

    out = _run(p.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="what time do you close")],
        system_prompt="sys", tools=[KNOWLEDGE_TOOL_SPEC], tool_runner=runner))
    assert "".join(out) == "We're open till five."
    assert called["n"] == 0


def test_tool_call_then_grounded_answer(rich_genai):
    fc = rich_genai.FunctionCall(name="lookup_company_knowledge", args={"query": "price"})
    # Round 0: function_call only (no text). Round 1: grounded text answer.
    p = _provider_with_streams([
        _FakeStream([_FakeChunk(function_calls=[fc])]),
        _FakeStream([_FakeChunk(text="It's forty nine a month.")]),
    ])
    seen = {}

    async def runner(name, args):
        seen["name"] = name
        seen["query"] = args.get("query")
        return "Premium plan is $49/month."

    out = _run(p.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="how much is the premium plan")],
        system_prompt="sys", tools=[KNOWLEDGE_TOOL_SPEC], tool_runner=runner))
    assert "".join(out) == "It's forty nine a month."
    assert seen["name"] == "lookup_company_knowledge"
    assert seen["query"] == "price"
