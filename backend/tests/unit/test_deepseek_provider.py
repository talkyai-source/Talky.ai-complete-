"""DeepSeek V4.1 Flash through the DeepSeek chat provider (owner request 2026-10-06).

The request rules come from probing the production key from the prod host:
thinking is ON by default and must be disabled (left on, the first 60 tokens
were all hidden reasoning and nothing was spoken); temperature, tools and
max_completion_tokens are accepted. With thinking off, first text arrived in
446-613 ms on a short prompt and 526-771 ms on an 11k-token prompt.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.llm.deepseek import DeepSeekLLMProvider
from app.infrastructure.llm.openai_chat import _OpenAIChatClient


def _sse(*chunks):
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


async def _provider(handler):
    p = DeepSeekLLMProvider()
    await p.initialize({"api_key": "sk-test", "model": "deepseek-flash", "max_tokens": 120})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p._client._http = http
    p._client.chat.completions._http = http
    return p


@pytest.mark.asyncio
async def test_the_request_disables_thinking_and_goes_to_deepseek():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "The fare is "}}]},
            {"choices": [{"delta": {"content": "2,800 rupees."}}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "prompt_cache_hit_tokens": 8}},
        ))

    p = await _provider(handler)
    out = [t async for t in p.stream_chat(
        [Message(role=MessageRole.USER, content="Lahore to Islamabad?")],
        system_prompt="Be brief.",
        temperature=0.6,
        campaign_id="camp-1",
    )]
    body = seen["body"]
    assert "".join(out) == "The fare is 2,800 rupees."
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body
    assert body["temperature"] == 0.6
    assert body["max_completion_tokens"] == 120  # no hidden-thinking reserve
    assert "prompt_cache_key" not in body  # DeepSeek caches prefixes on its own
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}


@pytest.mark.asyncio
async def test_tool_calls_are_reassembled_for_the_knowledge_lookup():
    def handler(request):
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                     "function": {"name": "knowledge_lookup", "arguments": "{\"qu"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"fares\"}"}}]}}]},
        ))

    p = await _provider(handler)
    sink = {}
    _ = [t async for t in p.stream_chat(
        [Message(role=MessageRole.USER, content="fares?")],
        tools=[{"type": "function", "function": {"name": "knowledge_lookup", "parameters": {}}}],
        tool_calls_sink=sink,
    )]
    call = next(iter(sink.values()))
    assert call["name"] == "knowledge_lookup"
    assert json.loads(call["arguments"]) == {"query": "fares"}


@pytest.mark.asyncio
async def test_an_http_error_is_raised_before_any_token_and_names_deepseek():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    p = await _provider(handler)
    with pytest.raises(RuntimeError, match="DeepSeek chat HTTP 401"):
        _ = [t async for t in p.stream_chat([Message(role=MessageRole.USER, content="x")])]


@pytest.mark.asyncio
async def test_missing_key_is_refused(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        await DeepSeekLLMProvider().initialize({})


def test_the_openai_client_still_defaults_to_openai():
    client = _OpenAIChatClient("sk-test", timeout=5)
    assert client.chat.completions._url == "https://api.openai.com/v1/chat/completions"


def test_deepseek_is_wired_everywhere_a_provider_is_looked_up():
    from app.domain.models.ai_config import DEEPSEEK_MODELS, LLMProvider
    from app.domain.services.credential_resolver import env_var_for_provider
    from app.domain.services.voice_orchestrator import VoiceOrchestrator
    from app.infrastructure.llm.factory import LLMFactory
    from app.services.scripts.knowledge.budget import _declared_context_window

    assert LLMProvider.DEEPSEEK.value == "deepseek"
    assert [m.id for m in DEEPSEEK_MODELS] == ["deepseek-flash"]
    assert env_var_for_provider("deepseek") == "DEEPSEEK_API_KEY"
    assert VoiceOrchestrator._LLM_API_KEY_ENV["deepseek"] == "DEEPSEEK_API_KEY"
    assert "deepseek" in LLMFactory.list_providers()
    assert _declared_context_window("deepseek-flash") == 1_000_000


def test_benchmark_uses_deepseek_for_a_deepseek_config(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    from app.api.v1.endpoints.ai_options.benchmark import _select_benchmark_llm
    from app.domain.models.ai_config import AIProviderConfig

    llm, api_key = _select_benchmark_llm(
        AIProviderConfig(llm_provider="deepseek", llm_model="deepseek-flash")
    )
    assert isinstance(llm, DeepSeekLLMProvider)
    assert api_key == "ds-key"
