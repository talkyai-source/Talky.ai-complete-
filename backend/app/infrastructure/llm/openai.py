"""OpenAI GPT-6 Luna text adapter for the traditional STT/LLM/TTS pipeline.

Luna permits Chat Completions function calling with reasoning_effort="none".
https://developers.openai.com/api/docs/models/gpt-6-luna
Native Realtime has its own adapter, session and prompts in app/realtime/.
"""
from __future__ import annotations

from contextlib import aclosing

import json
import os

import httpx

from app.domain.interfaces.llm_provider import LLMProvider
from app.infrastructure.llm.streaming import (
    LLMStreamStalled, LLMTimeoutError, accumulate_tool_calls, finalize_tool_calls,
    stream_tool_turn, stream_with_timeout,
)
from app.infrastructure.providers.provider_concurrency import get_provider_guard


class OpenAILLMProvider(LLMProvider):
    def __init__(self):
        self._client = None
        self._model = "gpt-6-luna"
        self._temperature = 0.6
        self._max_tokens = 150
        self._guard = get_provider_guard("openai")

    @property
    def name(self):
        return "openai"

    @property
    def supports_streaming(self):
        return True

    @property
    def supports_tools(self):
        return True

    async def initialize(self, config):
        key = config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI API key is not configured")
        self._model = config.get("model") or "gpt-6-luna"
        if self._model != "gpt-6-luna":
            raise ValueError("This voice adapter supports gpt-6-luna")
        self._temperature = float(config.get("temperature", 0.6))
        self._max_tokens = int(config.get("max_tokens", 150))
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )

    async def cleanup(self):
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def warm_up(self):
        async for _ in self.stream_chat_with_timeout([], system_prompt="Say hi.", max_tokens=8, timeout_seconds=2.0):
            pass

    async def stream_chat(self, messages, system_prompt=None, temperature=None, max_tokens=None, **kwargs):
        if self._client is None:
            raise RuntimeError("OpenAI client is not initialized")
        model = kwargs.get("model") or self._model
        if model != "gpt-6-luna" or kwargs.get("reasoning_effort", "none") != "none":
            raise ValueError("GPT-6 Luna voice turns use reasoning_effort=none")
        body_messages = []
        if system_prompt:
            body_messages.append({"role": "system", "content": system_prompt})
        body_messages.extend({"role": m.role.value, "content": m.content} for m in messages if m.content and m.content.strip())
        body_messages.extend(kwargs.get("extra_messages") or [])
        if not body_messages:
            body_messages.append({"role": "user", "content": "Hello"})
        request = {
            "model": model, "messages": body_messages, "stream": True,
            "reasoning_effort": "none",
            "temperature": self._temperature if temperature is None else temperature,
            "max_completion_tokens": self._max_tokens if max_tokens is None else max_tokens,
        }
        if kwargs.get("campaign_id"):
            request["prompt_cache_key"] = str(kwargs["campaign_id"])
        tools, sink = kwargs.get("tools"), kwargs.get("tool_calls_sink")
        if tools:
            request.update(tools=tools, tool_choice=kwargs.get("tool_choice", "auto"), parallel_tool_calls=False)
        calls = {}
        finished = False
        received = False
        try:
            async with self._guard.acquire():
                async with self._client.stream("POST", "chat/completions", json=request) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        event = json.loads(payload)
                        if event.get("error"):
                            raise RuntimeError("OpenAI rejected the streamed request")
                        for choice in event.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                received = True
                                yield delta["content"]
                            accumulate_tool_calls(calls, delta.get("tool_calls"))
                            reason = choice.get("finish_reason")
                            if reason:
                                if reason not in {"stop", "tool_calls"}:
                                    raise LLMStreamStalled(f"OpenAI response incomplete: {reason}")
                                finished = True
            if not finished:
                raise LLMStreamStalled("OpenAI stream closed without a terminal response")
            if sink is not None:
                sink.extend(finalize_tool_calls(calls))
        except httpx.TimeoutException as exc:
            error = LLMStreamStalled if received else LLMTimeoutError
            raise error("OpenAI connection timed out") from exc

    async def stream_chat_with_timeout(self, messages, timeout_seconds=10.0, **kwargs):
        async with aclosing(stream_with_timeout(self.stream_chat(messages, **kwargs), timeout_seconds)) as stream:
            async for token in stream:
                yield token

    async def stream_chat_with_tools(self, messages, **kwargs):
        async with aclosing(stream_tool_turn(self, messages, **kwargs)) as stream:
            async for token in stream:
                yield token
