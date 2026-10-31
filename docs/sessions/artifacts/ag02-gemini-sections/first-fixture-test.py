"""Tests for GeminiLLMProvider.stream_chat_with_tools â€” native function-calling
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
from app.domain.services.voice_pipeline.action_tools import ACTION_SEND_EMAIL


# â”€â”€ fake streaming transport â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class _FakeChunk:
    def __init__(self, text=None, function_calls=None):
        self.candidates = (
            [SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))]
            if text else None
        )
        self.function_calls = function_calls or []


class _FakeStream:
    def __init__(self, chunks):
        # These successful SDK fixtures end with the provider's decision receipt.
        terminal = SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")])
        self._chunks = [*chunks, terminal]

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
    p._test_requests = []

    async def _gen(*a, **k):
        p._test_requests.append({**k, "contents": tuple(k["contents"])})
        return streams.pop(0)

    aio.models.generate_content_stream = _gen
    p._client = SimpleNamespace(aio=aio)
    return p


def _run(agen):
    async def _collect():
        return [tok async for tok in agen]
    return asyncio.run(_collect())


class _StubPart(SimpleNamespace):
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call

    @staticmethod
    def from_function_response(name=None, response=None):
        part = _StubPart()
        part.function_response = SimpleNamespace(name=name, response=response)
        return part


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


# â”€â”€ tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    # Round 0 returns text and no function_call â†’ answered directly, tool unused.
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
        seen["section_ids"] = args.get("section_ids")
        return "Premium plan is $49/month."

    out = _run(p.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="how much is the premium plan")],
        system_prompt="sys", tools=[KNOWLEDGE_TOOL_SPEC], tool_runner=runner))
    assert "".join(out) == "It's forty nine a month."
    assert seen["name"] == "lookup_company_knowledge"
    assert seen["query"] == "price"


def test_strict_action_turn_discards_premature_round_zero_claim(rich_genai):
    fc = rich_genai.FunctionCall(name=ACTION_SEND_EMAIL, args={})
    p = _provider_with_streams([
        _FakeStream([
            _FakeChunk(
                text="I've sent that already.",
                function_calls=[fc],
            )
        ]),
        _FakeStream([
            _FakeChunk(text="I can't send an email from this call.")
        ]),
    ])
    action_spec = {
        "type": "function",
        "function": {
            "name": ACTION_SEND_EMAIL,
            "description": "Send an email.",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    async def runner(name, args):
        assert name == ACTION_SEND_EMAIL
        return '{"success":false,"status":"unavailable"}'

    out = _run(p.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="email it")],
        system_prompt="sys",
        tools=[action_spec],
        tool_runner=runner,
        require_tool_result_before_content=True,
    ))

    assert out == ["I can't send an email from this call."]
    assert "sent that already" not in "".join(out).lower()


def _signed_call(rich_genai, name, args, signature):
    fc = rich_genai.FunctionCall(name=name, args=args)
    part = SimpleNamespace(function_call=fc, thought_signature=signature, text=None)
    chunk = _FakeChunk(function_calls=[fc])
    chunk.candidates = [SimpleNamespace(content=SimpleNamespace(parts=[part]))]
    return chunk, part


def test_catalog_then_read_preserves_each_rounds_native_signed_parts(rich_genai):
    from app.domain.services.voice_pipeline.knowledge_tool import run_knowledge_lookup
    from app.services.scripts.knowledge.sections import build_section_catalog
    context = build_section_catalog([
        {"id": "refund", "source_id": "handbook", "source_version": 1, "version": "v1",
         "heading": "Refunds", "content": "Refunds take five working days."},
    ], tenant_id="t1", campaign_id="c1", source_policy="call_snapshot")
    session = SimpleNamespace(tenant_id="t1", campaign_id="c1", _knowledge_catalog=context)
    ref = context.nodes[0]["section_id"]
    first, first_part = _signed_call(rich_genai, "lookup_company_knowledge", {"catalog_offset": 0}, b"catalog-signature")
    second, second_part = _signed_call(rich_genai, "lookup_company_knowledge", {"section_ids": [ref]}, b"read-signature")
    provider = _provider_with_streams([
        _FakeStream([first]), _FakeStream([second]),
        _FakeStream([_FakeChunk(text="Refunds take five working days.")]),
    ])
    async def runner(name, args):
        return await run_knowledge_lookup(session, args)
    result = _run(provider.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="When will my money come back?")],
        tools=[KNOWLEDGE_TOOL_SPEC], tool_runner=runner, max_tool_rounds=3,
        read_only_tools=("lookup_company_knowledge",)))
    assert result == ["Refunds take five working days."]
    assert session._knowledge_evidence["status"] == "available"
    assert len(provider._test_requests) == 3
    final = provider._test_requests[2]["contents"]
    assert final[1].parts == [first_part] and final[3].parts == [second_part]
    assert final[1].parts[0] is first_part and final[3].parts[0] is second_part
    assert "catalog" in final[2].parts[0].function_response.response["result"]
    assert "available" in final[4].parts[0].function_response.response["result"]


def test_repeated_write_runs_once_across_rounds_and_stops_at_budget(rich_genai):
    spec = {"type": "function", "function": {"name": ACTION_SEND_EMAIL,
        "parameters": {"type": "object", "properties": {}}}}
    chunks = [_signed_call(rich_genai, ACTION_SEND_EMAIL, {}, bytes([i]))[0] for i in range(3)]
    provider = _provider_with_streams([*[_FakeStream([c]) for c in chunks],
        _FakeStream([_FakeChunk(text="The request was accepted.")])])
    calls = []
    async def runner(name, args):
        calls.append((name, args))
        return '{"success":true,"status":"accepted"}'
    result = _run(provider.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="Send it.")], tools=[spec], tool_runner=runner,
        max_tool_rounds=3))
    assert result == ["The request was accepted."] and calls == [(ACTION_SEND_EMAIL, {})]
    assert len(provider._test_requests) == 4
    assert not hasattr(provider._test_requests[-1]["config"], "tools")
    assert len(provider._test_requests[-1]["contents"]) == 7


def test_repeated_read_refreshes_evidence_after_catalog(rich_genai):
    calls = []
    args = [{"section_ids": ["a"]}, {"catalog_offset": 0}, {"section_ids": ["a"]}]
    chunks = [_signed_call(rich_genai, "lookup_company_knowledge", arg, b"signature")[0] for arg in args]
    provider = _provider_with_streams([*[_FakeStream([c]) for c in chunks],
        _FakeStream([_FakeChunk(text="Current source answer.")])])
    async def runner(name, arguments):
        calls.append(arguments)
        return "available" if "section_ids" in arguments else "catalog"
    _run(provider.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="Please check.")], tools=[KNOWLEDGE_TOOL_SPEC],
        tool_runner=runner, max_tool_rounds=3, read_only_tools=("lookup_company_knowledge",)))
    assert calls == args


@pytest.mark.parametrize("budget", [True, 0, 4, -1, 1.5])
def test_tool_round_budget_is_strict_even_without_tools(budget):
    provider = GeminiLLMProvider()
    with pytest.raises(ValueError, match="max_tool_rounds"):
        _run(provider.stream_chat_with_tools([], max_tool_rounds=budget))
