"""A provider must finish its decision before tools can run; EOF is not consent."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import json
import logging

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.infrastructure.llm.gemini import GeminiLLMProvider
from app.infrastructure.llm.streaming import LLMStreamStalled
from tests.unit.test_gemini_tools import rich_genai  # noqa: F401

pytestmark = pytest.mark.usefixtures("rich_genai")

MESSAGES = [Message(role=MessageRole.USER, content="Goodbye, please end the call.")]
TOOL = {"type": "function", "function": {
    "name": "end_call", "parameters": {
        "type": "object", "properties": {"reason": {"type": ["string", "null"]}},
        "required": ["reason"], "additionalProperties": False,
    },
}}


def _chunk(provider_name, *, text=None, reason=None, tool=False):
    if provider_name == "cerebras":
        calls = [NS(index=0, id="call_fixture", function=NS(
            name="end_call", arguments='{"reason":null}',
        ))] if tool else None
        return NS(choices=[NS(delta=NS(content=text, tool_calls=calls), finish_reason=reason)])
    call = NS(name="end_call", args={"reason": None}) if tool else None
    parts = ([NS(text=text)] if text else []) + ([NS(function_call=call)] if call else [])
    return NS(text=text, candidates=[NS(content=NS(parts=parts), finish_reason=reason)],
              function_calls=[call] if call else [])


def _provider(name, rounds):
    closed = []

    async def stream(chunks):
        try:
            for chunk in chunks:
                yield chunk
        finally:
            closed.append(True)

    create = AsyncMock(side_effect=lambda **_: stream(rounds.pop(0)))
    if name == "cerebras":
        provider = CerebrasLLMProvider()
        provider._model = "gpt-oss-120b"
        provider._client = NS(chat=NS(completions=NS(create=create)))
    else:
        provider = GeminiLLMProvider()
        provider._model = "gemini-3.8-flash"
        provider._client = NS(aio=NS(models=NS(generate_content_stream=create)))
    return provider, create, closed


@pytest.mark.parametrize("name,reason", [
    ("cerebras", "length"), ("cerebras", "content_filter"), ("cerebras", None),
    ("gemini", "MAX_TOKENS"), ("gemini", "SAFETY"), ("gemini", None),
])
@pytest.mark.parametrize("text", [None, "This answer is unfinished"])
async def test_incomplete_plain_turn_keeps_typed_error_and_never_retries(name, reason, text):
    provider, create, closed = _provider(name, [[
        _chunk(name, text=text), _chunk(name, reason=reason),
    ]])
    with pytest.raises(LLMStreamStalled):
        _ = [token async for token in provider.stream_chat(MESSAGES)]
    assert create.await_count == 1
    assert closed == [True]


@pytest.mark.parametrize("name,reason", [
    ("cerebras", "length"), ("cerebras", None),
    ("gemini", "MAX_TOKENS"), ("gemini", None),
])
async def test_incomplete_tool_decision_never_executes_or_continues(name, reason):
    provider, create, closed = _provider(name, [[
        _chunk(name, text="I will check.", tool=True), _chunk(name, reason=reason),
    ]])
    runner = AsyncMock(return_value="done")
    with pytest.raises(LLMStreamStalled):
        _ = [token async for token in provider.stream_chat_with_tools(
            MESSAGES, tools=[TOOL], tool_runner=runner,
        )]
    runner.assert_not_awaited()
    assert create.await_count == 1
    assert closed == [True]


@pytest.mark.parametrize("name,reason", [("cerebras", "length"), ("gemini", "MAX_TOKENS")])
async def test_incomplete_terminal_frame_does_not_publish_its_text(name, reason):
    provider, create, _ = _provider(name, [[_chunk(name, text="Unverified final text", reason=reason)]])
    output = []
    with pytest.raises(LLMStreamStalled):
        async for token in provider.stream_chat(MESSAGES):
            output.append(token)
    assert output == []
    assert create.await_count == 1


@pytest.mark.parametrize("name,reason", [("cerebras", "tool_calls"), ("gemini", "STOP")])
async def test_valid_nullable_end_tool_with_preamble_executes_once_then_continues(name, reason, caplog):
    caplog.set_level(logging.INFO, logger="app.infrastructure.llm.request_profile")
    stop = "stop" if name == "cerebras" else "STOP"
    provider, create, closed = _provider(name, [
        [_chunk(name, text="I will finish.", tool=True), _chunk(name, reason=reason)],
        [_chunk(name, text="Goodbye."), _chunk(name, reason=stop)],
    ])
    runner = AsyncMock(return_value="accepted")
    output = [token async for token in provider.stream_chat_with_tools(
        MESSAGES, tools=[TOOL], tool_runner=runner,
    )]
    runner.assert_awaited_once_with("end_call", {"reason": None})
    assert output == ["I will finish.", "Goodbye."]
    assert create.await_count == 2
    assert closed == [True, True]
    continuation = create.await_args_list[1].kwargs
    # The answer round cannot call tools: Cerebras keeps the definitions with
    # tool_choice "none" (the history holds the calls); Gemini drops them.
    if name == "cerebras":
        assert continuation.get("tool_choice") == "none"
    else:
        assert not getattr(continuation["config"], "tools", None)
    profiles = [json.loads(record.args[0]) for record in caplog.records
                if record.name == "app.infrastructure.llm.request_profile"]
    assert len(profiles) == 2
    assert profiles[0]["instructions_sha256"] == profiles[1]["instructions_sha256"]
    assert profiles[0]["request_envelope_sha256"] != profiles[1]["request_envelope_sha256"]
    # Cerebras's answer round still lists the tool (tool_choice "none" above).
    assert profiles[0]["tool_names"] == ["end_call"]
    assert profiles[1]["tool_names"] == (["end_call"] if name == "cerebras" else [])


@pytest.mark.parametrize("name", ["cerebras", "gemini"])
async def test_consumer_close_is_not_unexpected_provider_eof(name):
    provider, create, closed = _provider(name, [[_chunk(name, text="Hello."), _chunk(name, text="More.")]])
    stream = provider.stream_chat(MESSAGES)
    assert await anext(stream) == "Hello."
    await stream.aclose()
    assert create.await_count == 1
    assert closed == [True]
