"""GPT-6 Luna through the OpenAI chat provider (owner request 2026-10-01).

The request rules come from probing gpt-6-luna on the production key: no
temperature other than the default, max_completion_tokens (not max_tokens),
reasoning_effort "none" (the only setting that allows function tools on chat
completions), and prompt_cache_key accepted.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.openai_chat import OpenAIChatLLMProvider, _OpenAIChatClient


def _sse(*chunks):
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


async def _provider(handler):
    p = OpenAIChatLLMProvider()
    await p.initialize({"api_key": "sk-test", "model": "gpt-6-luna", "max_tokens": 120})
    client = _OpenAIChatClient("sk-test", timeout=5)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.chat.completions._http = client._http
    p._client = client
    return p


@pytest.mark.asyncio
async def test_the_request_follows_gpt_6_luna_rules_and_the_stream_is_read():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Hi, "}}]},
            {"choices": [{"delta": {"content": "how can I help?"}}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ))

    p = await _provider(handler)
    out = [t async for t in p.stream_chat(
        [Message(role=MessageRole.USER, content="Hello")],
        system_prompt="Be brief.",
        temperature=0.6,
        campaign_id="camp-1",
    )]
    body = seen["body"]
    assert "".join(out) == "Hi, how can I help?"
    assert seen["auth"] == "Bearer sk-test"
    assert body["model"] == "gpt-6-luna"
    assert "temperature" not in body
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
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"prices\"}"}}]}}]},
        ))

    p = await _provider(handler)
    sink = {}
    _ = [t async for t in p.stream_chat(
        [Message(role=MessageRole.USER, content="prices?")],
        tools=[{"type": "function", "function": {"name": "knowledge_lookup", "parameters": {}}}],
        tool_calls_sink=sink,
    )]
    call = next(iter(sink.values()))
    assert call["name"] == "knowledge_lookup"
    assert json.loads(call["arguments"]) == {"query": "prices"}


@pytest.mark.asyncio
async def test_an_http_error_is_raised_before_any_token():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "bad"}})

    p = await _provider(handler)
    with pytest.raises(RuntimeError, match="OpenAI chat HTTP 400"):
        _ = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="x")])]
