"""DeepSeek V4.1 Flash for the cascaded voice pipeline (owner request 2026-10-06).

DeepSeek's chat API is OpenAI-compatible, so this reuses the httpx chat client
from ``openai_chat`` pointed at DeepSeek, and the Cerebras provider's retries,
circuit breaker, first-token/inter-token deadlines and tool-call reassembly.

Rules, each measured from the production host on 2026-10-06 with the owner's
key:

  * GET /models lists ``deepseek-flash`` (V4.1 Flash) and ``deepseek-v4-pro``
  * thinking is ON by default: left on, the first 60 tokens were all hidden
    reasoning and no text came out, so every request sends
    ``thinking: {"type": "disabled"}``
  * temperature, max_completion_tokens and function tools are accepted
  * prefix caching is automatic (10,880 of 11,090 prompt tokens hit on a
    repeat), so no cache key is sent
  * first text with thinking off: 446-613 ms on a short prompt, 526-771 ms on
    an 11k-token prompt (about the size of a live call prompt)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.infrastructure.llm.groq import DEFAULT_LLM_TIMEOUT
from app.infrastructure.llm.openai_chat import _OpenAIChatClient

logger = logging.getLogger(__name__)

DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
_DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekLLMProvider(CerebrasLLMProvider):
    """Streaming chat provider backed by DeepSeek chat completions."""

    def __init__(self) -> None:
        super().__init__()
        from app.infrastructure.providers.provider_concurrency import get_provider_guard
        from app.utils.resilience import CircuitBreaker

        self._guard = get_provider_guard("deepseek")
        self._model = DEFAULT_DEEPSEEK_MODEL
        self._circuit = CircuitBreaker(
            name="deepseek-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError},
        )

    async def initialize(self, config: dict) -> None:
        api_key = config.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError(
                "DeepSeek API key not found. Set DEEPSEEK_API_KEY in the environment "
                "or pass api_key in config."
            )
        self._model = config.get("model") or DEFAULT_DEEPSEEK_MODEL
        self._temperature = config.get("temperature", 0.5)
        self._max_tokens = config.get("max_tokens", 150)
        self._client = _OpenAIChatClient(
            api_key,
            timeout=config.get("timeout", DEFAULT_LLM_TIMEOUT),
            url=_DEEPSEEK_CHAT_URL,
            label="DeepSeek chat",
        )
        logger.info(
            "DeepSeekLLMProvider initialized: model=%s max_tokens=%s thinking=disabled",
            self._model, self._max_tokens,
        )

    @staticmethod
    def _reasoning_effort(model: str) -> Optional[str]:
        # Thinking is switched off with the `thinking` field below; sending no
        # effort also keeps the completion budget free of a thinking reserve.
        return None

    def _build_request(self, **kwargs: Any) -> dict:
        request = super()._build_request(**kwargs)
        request["thinking"] = {"type": "disabled"}
        request.pop("prompt_cache_key", None)
        request.pop("clear_thinking", None)
        return request

    @property
    def name(self) -> str:
        return "deepseek"
