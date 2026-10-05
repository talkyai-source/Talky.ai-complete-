"""OpenAI chat models (GPT-6 Luna) for the cascaded voice pipeline.

Uses the same OPENAI_API_KEY as GPT Realtime. There is no `openai` SDK in this
project, so the chat-completions stream is read with httpx (already a
dependency) behind the small client below, which mirrors exactly the slice of
the SDK the Cerebras provider uses: ``client.chat.completions.create(**req)``
returning an async iterator of chunks with ``.choices[0].delta`` and
``.usage``. Everything else -- retries, circuit breaker, the first-token and
inter-token deadlines, tool-call reassembly, cache stats -- is the Cerebras
provider's code, unchanged, because both speak the same wire format.

gpt-6-luna constraints, each measured from the production host on 2026-10-01:

  * reasoning_effort: none | low | medium | high | xhigh ("minimal" -> 400)
  * function tools on /v1/chat/completions ONLY with reasoning_effort="none"
  * temperature: only the default (1) -- any other value -> 400
  * max_completion_tokens (max_tokens -> 400); prompt_cache_key accepted
  * first text with "none": 758-968 ms (a short prompt); "low": ~1.7 s
"""
from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace
from typing import Any, Optional

import httpx

from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.infrastructure.llm.groq import DEFAULT_LLM_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_CHAT_MODEL = "gpt-6-luna"
_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _ns(value: Any) -> Any:
    """JSON -> attribute access, recursively (what the SDK objects give)."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


class _ChatCompletions:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        url: str = _CHAT_URL,
        label: str = "OpenAI chat",
    ) -> None:
        self._http = http
        self._api_key = api_key
        # Any OpenAI-compatible chat-completions endpoint (DeepSeek reuses it).
        self._url = url
        self._label = label

    async def create(self, **request: Any):
        """Open the stream first so an HTTP error raises here (and is retried
        by the caller before any token is spoken), then yield chunks."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        cm = self._http.stream("POST", self._url, headers=headers, json=request)
        response = await cm.__aenter__()
        if response.status_code != 200:
            body = (await response.aread()).decode(errors="replace")[:500]
            await cm.__aexit__(None, None, None)
            raise RuntimeError(f"{self._label} HTTP {response.status_code}: {body}")

        async def chunks():
            try:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    yield _ns(json.loads(data))
            finally:
                await cm.__aexit__(None, None, None)

        return chunks()


class _OpenAIChatClient:
    def __init__(
        self,
        api_key: str,
        timeout: float,
        url: str = _CHAT_URL,
        label: str = "OpenAI chat",
    ) -> None:
        self._http = httpx.AsyncClient(timeout=timeout)
        self.chat = SimpleNamespace(
            completions=_ChatCompletions(self._http, api_key, url=url, label=label)
        )

    async def close(self) -> None:
        await self._http.aclose()


class OpenAIChatLLMProvider(CerebrasLLMProvider):
    """Streaming chat provider backed by OpenAI chat completions."""

    def __init__(self) -> None:
        super().__init__()
        from app.infrastructure.providers.provider_concurrency import get_provider_guard
        from app.utils.resilience import CircuitBreaker

        self._guard = get_provider_guard("openai")
        self._model = DEFAULT_OPENAI_CHAT_MODEL
        self._circuit = CircuitBreaker(
            name="openai-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError},
        )

    async def initialize(self, config: dict) -> None:
        api_key = config.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY in the environment "
                "or pass api_key in config."
            )
        self._model = config.get("model") or DEFAULT_OPENAI_CHAT_MODEL
        self._temperature = config.get("temperature", 1.0)
        self._max_tokens = config.get("max_tokens", 150)
        self._client = _OpenAIChatClient(
            api_key, timeout=config.get("timeout", DEFAULT_LLM_TIMEOUT)
        )
        logger.info(
            "OpenAIChatLLMProvider initialized: model=%s max_tokens=%s reasoning_effort=%s",
            self._model, self._max_tokens, self._reasoning_effort(self._model),
        )

    @staticmethod
    def _reasoning_effort(model: str) -> Optional[str]:
        # "none" is the fastest setting and the only one that allows function
        # tools on chat completions (the knowledge lookup is a tool).
        return "none"

    def _build_request(self, **kwargs: Any) -> dict:
        request = super()._build_request(**kwargs)
        # Only the default temperature is accepted by these models.
        request.pop("temperature", None)
        request.pop("clear_thinking", None)
        return request

    @property
    def name(self) -> str:
        return "openai"
