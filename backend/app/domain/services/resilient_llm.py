"""Resilient LLM wrapper (Phase 4.1).

Same shape as ``resilient_tts.py`` but for the LLM layer. Wraps a
primary provider (Groq) with circuit-breaker-gated failover to a
secondary (OpenAI ``gpt-4o-mini-realtime`` or any other LLMProvider).

Mid-stream LLM failures are handled differently from TTS: if a
secondary swap happens AFTER the primary already yielded tokens, the
caller would get the AI's response in two voices' worth of style mid-
sentence. Same rule as TTS: **never swap mid-stream**. If the primary
fails before the first token, we cleanly retry on secondary with the
same messages. If it fails after, we re-raise so the caller can choose
recovery (typically: cut the turn short, the next turn re-prompts).

The Groq provider already has its own retry-with-backoff inside
``stream_chat``; this wrapper sits one level up so a TOTAL Groq outage
(circuit open, every key cooling down) routes to OpenAI without the
caller code changing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)


@dataclass
class LLMFailoverPolicy:
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0


class ResilientLLMProvider(LLMProvider):
    """Primary + secondary LLM with circuit-breaker-gated startup failover."""

    def __init__(
        self,
        primary: LLMProvider,
        secondary: Optional[LLMProvider] = None,
        policy: Optional[LLMFailoverPolicy] = None,
    ):
        self._primary = primary
        self._secondary = secondary
        self._policy = policy or LLMFailoverPolicy()
        self._breaker = CircuitBreaker(
            name=f"llm-{primary.name}",
            failure_threshold=self._policy.failure_threshold,
            recovery_timeout=self._policy.recovery_timeout_seconds,
        )

    @property
    def name(self) -> str:
        return f"resilient({self._primary.name})"

    @property
    def supports_streaming(self) -> bool:
        return self._primary.supports_streaming

    async def initialize(self, config: dict) -> None:
        await self._primary.initialize(config)
        if self._secondary is not None:
            try:
                await self._secondary.initialize(config)
            except Exception as exc:
                logger.warning(
                    "resilient_llm_secondary_init_failed provider=%s err=%s",
                    self._secondary.name, exc,
                )
                self._secondary = None

    async def cleanup(self) -> None:
        for p in (self._primary, self._secondary):
            if p is None:
                continue
            try:
                await p.cleanup()
            except Exception as exc:
                logger.debug(
                    "resilient_llm_cleanup_error provider=%s err=%s", p.name, exc,
                )

    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 150,
        **kwargs,
    ) -> AsyncIterator[str]:
        # Fast path: circuit already open → straight to secondary.
        if self._breaker.state.value == "open" and self._secondary is not None:
            logger.info("resilient_llm_primary_circuit_open — using secondary")
            async for token in self._secondary.stream_chat(
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield token
            return

        started = False
        try:
            async with self._breaker:
                async for token in self._primary.stream_chat(
                    messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                ):
                    started = True
                    yield token
                return
        except CircuitOpenError:
            if self._secondary is not None:
                async for token in self._secondary.stream_chat(
                    messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                ):
                    yield token
            return
        except Exception as exc:
            if started:
                # Tokens already streamed to caller — switching mid-
                # response would interleave two LLMs. Re-raise.
                logger.error(
                    "resilient_llm_mid_stream_drop provider=%s err=%s — "
                    "response truncated, not failing over",
                    self._primary.name, exc,
                )
                raise
            if self._secondary is None:
                logger.error(
                    "resilient_llm_startup_failed_no_secondary provider=%s err=%s",
                    self._primary.name, exc,
                )
                raise
            logger.warning(
                "resilient_llm_startup_failed_failover_to=%s err=%s",
                self._secondary.name, exc,
            )
            async for token in self._secondary.stream_chat(
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield token
