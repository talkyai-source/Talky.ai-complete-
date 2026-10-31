"""GPT-6 Luna through the OpenAI chat provider (owner request 2026-10-01).

The canonical adapter uses max_completion_tokens, reasoning_effort="none",
saved temperature (accepted by the live none-reasoning probe), and campaign
prompt_cache_key. A complete stream must include a terminal finish reason.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.openai import OpenAILLMProvider


def _sse(*chunks):
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


async def _provider(handler):
    p = OpenAILLMProvider()
    p._max_tokens = 120
    p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com/v1/", headers={"Authorization": "Bearer sk-test"})
    return p


@pytest.mark.asyncio
async def test_the_request_follows_gpt_6_luna_rules_and_the_stream_is_read():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Hi, "}}]},
            {"choices": [{"delta": {"content": "how can I help?"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ))

    p = await _provider(handler)
    try:
        out = [t async for t in p.stream_chat(
            [Message(role=MessageRole.USER, content="Hello")],
            system_prompt="Be brief.", temperature=0.6, campaign_id="camp-1",
        )]
    finally:
        await p.cleanup()
    body = seen["body"]
    assert "".join(out) == "Hi, how can I help?"
    assert seen["auth"] == "Bearer sk-test"
    assert body["model"] == "gpt-6-luna"
    assert body["temperature"] == 0.6
    assert "max_tokens" not in body and body["max_completion_tokens"] == 120
    assert body["reasoning_effort"] == "none"
    assert body["prompt_cache_key"] == "camp-1"
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}


@pytest.mark.asyncio
async def test_tool_calls_are_reassembled_for_the_knowledge_lookup():
    def handler(request):
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                     "function": {"name": "knowledge_lookup", "arguments": "{\"qu"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"prices\"}"}}]}, "finish_reason": "tool_calls"}]},
        ))

    p = await _provider(handler)
    sink = []
    try:
        _ = [t async for t in p.stream_chat(
            [Message(role=MessageRole.USER, content="prices?")],
            tools=[{"type": "function", "function": {"name": "knowledge_lookup", "parameters": {}}}],
            tool_calls_sink=sink,
        )]
    finally:
        await p.cleanup()
    call = sink[0]
    assert call["name"] == "knowledge_lookup"
    assert call["arguments"] == {"query": "prices"}
    assert call["arguments_valid"] is True


@pytest.mark.asyncio
async def test_an_http_error_is_raised_before_any_token():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "bad"}})

    p = await _provider(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as error:
            _ = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="x")])]
        assert error.value.response.status_code == 400
    finally:
        await p.cleanup()
