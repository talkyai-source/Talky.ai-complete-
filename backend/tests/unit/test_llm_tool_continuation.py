"""Provider contract regressions: executable requests, receipts and no replay."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.resilient_llm import ResilientLLMProvider
from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.llm.openai import OpenAILLMProvider
from app.infrastructure.llm.streaming import execute_tool_call, LLMStreamStalled
from app.infrastructure.llm.gemini import GeminiLLMProvider


MESSAGES = [Message(role=MessageRole.USER, content="Please check the price")]
TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {
    "type": "object", "properties": {"query": {"type": "string"}},
    "required": ["query"], "additionalProperties": False,
}}}]
CALL = {"id": "call-1", "name": "lookup", "arguments": {"query": "price"},
        "arguments_raw": '{"query":"price"}', "arguments_valid": True}


@pytest.mark.parametrize("provider_cls", [GroqLLMProvider, CerebrasLLMProvider, OpenAILLMProvider])
@pytest.mark.parametrize("strict", [False, True])
async def test_preamble_does_not_discard_tool_and_second_round_has_receipt(provider_cls, strict):
    provider = provider_cls()
    rounds = []

    async def stream(messages, **kwargs):
        rounds.append(kwargs)
        if len(rounds) == 1:
            yield "Let me check. "
            kwargs["tool_calls_sink"].append(dict(CALL))
        else:
            yield "The confirmed price is £12."

    provider.stream_chat = stream
    runner = AsyncMock(return_value={"success": True, "price": "£12"})
    result = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=TOOLS, tool_runner=runner, require_tool_result_before_content=strict,
    )]
    runner.assert_awaited_once_with("lookup", {"query": "price"})
    assert "confirmed price" in "".join(result)
    assert ("Let me check" not in "".join(result)) == strict
    assert len(rounds) == 2 and "tools" not in rounds[1]
    receipt = rounds[1]["extra_messages"][-1]
    assert receipt["role"] == "tool" and receipt["tool_call_id"] == "call-1"
    assert json.loads(receipt["content"])["success"] is True


@pytest.mark.parametrize("call,status", [
    ({**CALL, "name": "delete_all"}, "unknown_tool"),
    ({**CALL, "arguments": {}, "arguments_valid": False}, "invalid_arguments"),
    ({**CALL, "arguments": {"query": 123}}, "invalid_arguments"),
    ({**CALL, "arguments": {}}, "invalid_arguments"),
])
async def test_invalid_tool_request_never_executes(call, status):
    runner = AsyncMock()
    result = json.loads(await execute_tool_call(call, TOOLS, runner))
    assert result["status"] == status and not result["confirmation_allowed"]
    runner.assert_not_awaited()


@pytest.mark.parametrize("schema,value,valid", [
    ({"type": ["string", "null"]}, None, True),
    ({"type": ["string", "null"]}, "hello", True),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, None, True),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, 9, False),
    ({"type": ["number", "null"]}, True, False),
    ({"type": "integer"}, True, False),
    ({"type": "number"}, 1.5, True),
    ({"type": "array", "items": {"type": "string"}}, ["ok", 1], False),
])
async def test_tool_argument_nullable_and_structural_schemas(schema, value, valid):
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {
        "type": "object", "properties": {"query": schema}, "additionalProperties": False,
    }}}]
    runner = AsyncMock(return_value={"success": True})
    result = json.loads(await execute_tool_call({**CALL, "arguments": {"query": value}}, tools, runner))
    assert result["success"] is valid
    assert runner.await_count == int(valid)


async def test_unknown_additional_tool_argument_cannot_reach_runner():
    runner = AsyncMock()
    result = json.loads(await execute_tool_call({**CALL, "arguments": {"query": "price", "confirm": True}}, TOOLS, runner))
    assert result["status"] == "invalid_arguments"
    runner.assert_not_awaited()


async def test_execution_timeout_is_unknown_not_success_or_retry():
    async def slow(*_):
        await asyncio.sleep(1)
    runner = AsyncMock(side_effect=slow)
    result = json.loads(await execute_tool_call(CALL, TOOLS, runner, timeout_seconds=.01))
    assert result["status"] == "outcome_unknown"
    assert not result["success"] and not result["confirmation_allowed"]
    assert runner.await_count == 1


async def test_cerebras_wire_reassembles_fragmented_calls_and_continues():
    def chunk(content=None, fragments=None, reason=None):
        return NS(choices=[NS(delta=NS(content=content, tool_calls=fragments), finish_reason=reason)])
    async def first():
        yield chunk("Checking. ")
        yield chunk(fragments=[NS(index=0, id="call-1", function=NS(name="lookup", arguments='{"query":'))])
        yield chunk(fragments=[NS(index=0, id=None, function=NS(name=None, arguments='"price"}'))])
        yield chunk(reason="tool_calls")
    async def second():
        yield chunk("£12 confirmed.")
        yield chunk(reason="stop")
    provider = CerebrasLLMProvider()
    create = AsyncMock(side_effect=[first(), second()])
    provider._client = NS(chat=NS(completions=NS(create=create)))
    runner = AsyncMock(return_value="£12 confirmed")
    result = [t async for t in provider.stream_chat_with_tools(MESSAGES, tools=TOOLS, tool_runner=runner)]
    assert "£12 confirmed" in "".join(result)
    runner.assert_awaited_once_with("lookup", {"query": "price"})
    assert create.await_args_list[0].kwargs["tools"] == TOOLS
    second_request = create.await_args_list[1].kwargs
    assert "tools" not in second_request
    assert second_request["messages"][-1]["role"] == "tool"


@pytest.mark.parametrize("mode,expected_secondary,expected_execution", [
    ("startup", 1, 1), ("executed", 0, 1), ("spoken", 0, 0), ("cancelled", 0, 0),
])
async def test_failover_only_before_speech_or_tool_execution(mode, expected_secondary, expected_execution):
    primary, secondary = CerebrasLLMProvider(), GroqLLMProvider()
    fallback_calls = []
    async def primary_stream(*args, **kwargs):
        if mode == "executed":
            await kwargs["tool_runner"]("lookup", {"query": "price"})
        if mode == "spoken":
            yield "Checking."
        if mode == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError("provider disconnected")
    async def fallback(*args, **kwargs):
        fallback_calls.append(True)
        await kwargs["tool_runner"]("lookup", {"query": "price"})
        yield "Done"
    primary.stream_chat_with_tools = primary_stream
    secondary.stream_chat_with_tools = fallback
    wrapper = ResilientLLMProvider(primary, secondary)
    assert wrapper.supports_tools is True
    runner = AsyncMock(return_value="result")
    try:
        result = [t async for t in wrapper.stream_chat_with_tools(MESSAGES, tools=TOOLS, tool_runner=runner)]
        assert mode == "startup" and result == ["Done"]
    except (RuntimeError, asyncio.CancelledError):
        assert mode != "startup"
    assert len(fallback_calls) == expected_secondary
    assert runner.await_count == expected_execution


def _sse(delta, finish=None):
    return 'data: ' + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + '\n\n'


async def test_luna_request_stream_and_tool_followup_use_compatible_parameters():
    requests = []
    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            content = _sse({"content": "Checking. "}) + _sse({"tool_calls": [
                {"index": 0, "id": "call-1", "function": {"name": "lookup", "arguments": '{"query":"price"}'}}
            ]}, "tool_calls")
        else:
            content = _sse({"content": "£12 confirmed."}, "stop")
        return httpx.Response(200, text=content+'data: [DONE]\n\n')
    provider = OpenAILLMProvider()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="https://example.invalid/")
    runner = AsyncMock(return_value="£12")
    try:
        result = [t async for t in provider.stream_chat_with_tools(
            MESSAGES, tools=TOOLS, tool_runner=runner, temperature=.2, max_tokens=80,
            require_tool_result_before_content=True,
        )]
    finally:
        await provider.cleanup()
    assert result == ["£12 confirmed."]
    assert len(requests) == 2
    for body in requests:
        assert body["model"] == "gpt-6-luna"
        assert body["reasoning_effort"] == "none"
        assert body["temperature"] == .2 and body["max_completion_tokens"] == 80
        assert "max_tokens" not in body and "thinking_budget" not in body
    assert requests[0]["parallel_tool_calls"] is False
    assert "tools" not in requests[1]
    runner.assert_awaited_once()


@pytest.mark.parametrize("ending", ["", _sse({}, "length")])
async def test_luna_truncated_stream_is_not_clean_success(ending):
    provider = OpenAILLMProvider()
    provider._client = httpx.AsyncClient(base_url="https://example.invalid/", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=_sse({"content": "Partial "})+ending)))
    try:
        with pytest.raises(LLMStreamStalled):
            _ = [t async for t in provider.stream_chat_with_timeout(MESSAGES)]
    finally:
        await provider.cleanup()


@pytest.mark.parametrize("provider_cls", [GroqLLMProvider, CerebrasLLMProvider, OpenAILLMProvider])
async def test_barge_in_closes_stream_without_waiting_for_gc(provider_cls):
    provider = provider_cls()
    closed = []
    async def stream(*args, **kwargs):
        try:
            yield "Hello"
            await asyncio.sleep(10)
        finally:
            closed.append(True)
    provider.stream_chat = stream
    gen = provider.stream_chat_with_tools(MESSAGES, tools=TOOLS, tool_runner=AsyncMock())
    assert await anext(gen) == "Hello"
    await gen.aclose()
    assert closed == [True]


@pytest.mark.parametrize("strict", [False, True])
async def test_gemini_mixed_tools_preserve_signature_and_low_thinking(monkeypatch, strict):
    import sys
    class Part(NS):
        @classmethod
        def from_function_response(cls, **kwargs):
            return cls(function_response=kwargs)
    types = NS(Content=NS, Part=Part, FunctionDeclaration=NS, Tool=NS,
               GenerateContentConfig=NS, ThinkingConfig=NS)
    monkeypatch.setitem(sys.modules, "google.genai", NS(types=types))
    monkeypatch.setitem(sys.modules, "google.genai.types", types)
    import google
    monkeypatch.setattr(google, "genai", NS(types=types), raising=False)
    call = NS(name="lookup", args={"query": "price"})
    signed = Part(function_call=call, thought_signature=b"opaque-signature")
    def chunk(parts, calls=None):
        return NS(candidates=[NS(content=NS(parts=parts), finish_reason="STOP")], function_calls=calls or [])
    requests = []
    closed = []
    async def create(**kwargs):
        requests.append({**kwargs, "contents": list(kwargs["contents"])})
        async def chunks():
            try:
                if len(requests) == 1:
                    yield chunk([Part(text="Checking. "), signed], [call])
                else:
                    yield chunk([Part(text="£12 confirmed.")])
            finally:
                closed.append(True)
        return chunks()
    provider = GeminiLLMProvider()
    provider._model = "gemini-3.8-flash"
    provider._client = NS(aio=NS(models=NS(generate_content_stream=create)))
    runner = AsyncMock(return_value="£12")
    result = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=TOOLS, tool_runner=runner, require_tool_result_before_content=strict,
    )]
    assert "£12 confirmed" in "".join(result)
    assert ("Checking" not in "".join(result)) == strict
    runner.assert_awaited_once_with("lookup", {"query": "price"})
    assert len(requests) == 2 and len(closed) == 2
    assert requests[0]["config"].thinking_config.thinking_level == "low"
    model_reply = requests[1]["contents"][-2]
    assert model_reply.parts.count(signed) == 1
    assert model_reply.parts[1].thought_signature == b"opaque-signature"
    assert not hasattr(requests[1]["config"], "tools")


def test_luna_factory_and_benchmark_use_openai_without_changing_default(monkeypatch):
    from app.domain.models.ai_config import AIProviderConfig
    from app.infrastructure.llm.factory import LLMFactory
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = AIProviderConfig(llm_provider="openai", llm_model="gpt-6-luna")
    assert AIProviderConfig().llm_provider == "cerebras"
    assert isinstance(LLMFactory.create(config.llm_provider, {}), OpenAILLMProvider)
    provider, key = _select_benchmark_llm(config)
    assert isinstance(provider, OpenAILLMProvider) and key == "test-key"
