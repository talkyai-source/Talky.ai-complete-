"""The evidence envelope identifies real assembled input without storing its content."""

import hashlib
import json
import logging
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.llm.request_profile import record_traditional_request


def test_prompt_history_and_schema_changes_affect_hash_without_leaking_content(caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")
    prompt = "Synthetic private campaign policy"
    request = {
        "model": "gpt-6-luna", "temperature": 0.6, "max_completion_tokens": 90,
        "reasoning_effort": "none",
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": "synthetic-person@example.invalid"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {
            "type": "object", "properties": {"query": {"type": "string"}},
        }}}],
    }

    def record(value, instructions=prompt):
        return record_traditional_request(provider="openai", request=value, instructions=instructions,
                                          configured_temperature=0.6, configured_max_tokens=90)

    initial = record(request)
    assert initial["instructions_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert initial["tool_names"] == ["lookup"]
    assert initial["request_state"] == "dispatch_attempt"
    assert initial == record(deepcopy(request))
    changed_prompt = deepcopy(request)
    changed_prompt["messages"][0]["content"] += " with dynamic state"
    assert record(changed_prompt, changed_prompt["messages"][0]["content"])["instructions_sha256"] != initial["instructions_sha256"]
    assert record(changed_prompt)["request_envelope_sha256"] != initial["request_envelope_sha256"]
    continuation = deepcopy(request)
    continuation["messages"].append({"role": "tool", "tool_call_id": "fixture", "content": "Synthetic private lookup result"})
    assert record(continuation)["request_envelope_sha256"] != initial["request_envelope_sha256"]
    changed_schema = deepcopy(request)
    changed_schema["tools"][0]["function"]["parameters"]["required"] = ["query"]
    assert record(changed_schema)["request_envelope_sha256"] != initial["request_envelope_sha256"]
    for raw in (prompt, "synthetic-person@example.invalid", "Synthetic private lookup result", "messages", "parameters"):
        assert raw not in caplog.text
        assert raw not in json.dumps(initial)


def test_gemini_sdk_shape_hashes_signed_parts_and_exposes_only_effective_controls():
    # Actual installed SDK representation, without any provider connection.
    from google.genai.types import Content, GenerateContentConfig, Part, ThinkingConfig, Tool, FunctionDeclaration

    config = GenerateContentConfig(
        temperature=0.6, max_output_tokens=1114,
        thinking_config=ThinkingConfig(thinking_level="low"),
        system_instruction="Synthetic instructions",
        tools=[Tool(function_declarations=[FunctionDeclaration(name="end_call", parameters_json_schema={"type": "object"})])],
    )
    contents = [Content(role="model", parts=[Part(text="Synthetic reply", thought_signature=b"\x00\xffsignature")])]
    profile = record_traditional_request(
        provider="gemini", request={"model": "gemini-3.8-flash", "contents": contents, "config": config},
        instructions="Synthetic instructions", configured_temperature=0.6, configured_max_tokens=90,
    )
    assert profile["thinking_level"] == "LOW"
    assert profile["wire_token_ceiling"] == 1114
    assert profile["configured_visible_token_target"] == 90
    assert profile["tool_names"] == ["end_call"]
    assert "signature" not in json.dumps(profile)


@pytest.mark.asyncio
async def test_unknown_sdk_detail_cannot_block_dispatch_or_leak_in_fallback_log(caplog):
    from app.domain.models.conversation import Message, MessageRole
    from app.infrastructure.llm.cerebras import CerebrasLLMProvider

    class NewSDKValue:
        def __repr__(self):
            return "synthetic-private-provider-value"

    async def stream():
        yield NS(choices=[NS(delta=NS(content="Ready."), finish_reason="stop")])

    create = AsyncMock(side_effect=lambda **_: stream())
    provider = CerebrasLLMProvider()
    provider._model = "gpt-oss-120b"
    provider._client = NS(chat=NS(completions=NS(create=create)))
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")
    result = [token async for token in provider.stream_chat(
        [Message(role=MessageRole.USER, content="Synthetic private prompt")],
        extra_messages=[{"role": "tool", "content": NewSDKValue()}],
    )]
    assert result == ["Ready."] and create.await_count == 1
    profile, = [json.loads(record.args[0]) for record in caplog.records
                if record.name == "app.infrastructure.llm.request_profile"]
    assert profile["digest_status"] == "unavailable"
    assert profile["request_envelope_sha256"] is None
    assert "synthetic-private-provider-value" not in caplog.text
    assert "Synthetic private prompt" not in caplog.text


@pytest.mark.asyncio
async def test_actual_gemini_sdk_types_accept_nullable_function_and_low_thinking():
    from google.genai import types
    from app.domain.models.conversation import Message, MessageRole
    from app.infrastructure.llm.gemini import GeminiLLMProvider

    async def first():
        yield types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                name="end_call", args={"reason": None},
            ))]), finish_reason=types.FinishReason.STOP,
        )])

    async def second():
        yield types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text="Goodbye.")]),
            finish_reason=types.FinishReason.STOP,
        )])

    create = AsyncMock(side_effect=[first(), second()])
    provider = GeminiLLMProvider()
    provider._model = "gemini-3.8-flash"
    provider._client = NS(aio=NS(models=NS(generate_content_stream=create)))
    runner = AsyncMock(return_value="accepted")
    tool = {"type": "function", "function": {"name": "end_call", "parameters": {
        "type": "object", "properties": {"reason": {"type": ["string", "null"]}},
        "required": ["reason"], "additionalProperties": False,
    }}}
    output = [token async for token in provider.stream_chat_with_tools(
        [Message(role=MessageRole.USER, content="Goodbye.")], tools=[tool], tool_runner=runner,
        temperature=0.6, max_tokens=90,
    )]
    assert output == ["Goodbye."]
    runner.assert_awaited_once_with("end_call", {"reason": None})
    config = create.await_args_list[0].kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert config.max_output_tokens == 1114
    assert config.tools[0].function_declarations[0].parameters_json_schema == tool["function"]["parameters"]
    followup = create.await_args_list[1].kwargs
    assert followup["config"].tools is None
    assert followup["contents"][-1].parts[0].function_response.response == {"result": "accepted"}
