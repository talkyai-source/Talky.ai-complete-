"""
Cerebras Inference LLM provider.

Cerebras serves models from wafer-scale (WSE) hardware rather than GPUs: the
weights sit in on-chip SRAM instead of streaming from HBM per token, which is
why its throughput ceiling is far above GPU inference for comparable models.

Why this provider exists here (2026-07-28): Groq's free tier caps at 8K tokens
per minute per ORGANISATION, while a single assistant turn is ~8.7K tokens — so
on Groq that request can never fit, at any traffic level. Cerebras
pay-as-you-go is 500K–1M TPM per model.

The API is OpenAI-compatible, so this mirrors the Groq provider's shape closely
(circuit breaker, bounded retries, shared concurrency guard) rather than
inventing a different structure.

THINKING IS DISABLED BY DEFAULT — see ``_reasoning_effort``. This is the whole
point for a voice agent: reasoning tokens are spent BEFORE the first spoken
word, so they land directly on time-to-first-token.

Docs: https://inference-docs.cerebras.ai/api-reference/chat-completions
Models are declared in app/domain/models/ai_config.py (CEREBRAS_MODELS).
"""
import asyncio
import logging
import os
from typing import AsyncIterator, Dict, List, Optional

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.ai_config import (
    CEREBRAS_MIN_REASONING_EFFORT,
    CEREBRAS_REASONING_NONE_SUPPORTED,
    CerebrasModel,
)
from app.domain.models.conversation import Message
from app.infrastructure.providers.provider_concurrency import get_provider_guard
from app.utils.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

DEFAULT_LLM_TIMEOUT = 10.0
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # match Groq — fast first retry inside the voice budget

DEFAULT_CEREBRAS_MODEL = CerebrasModel.GEMMA_4_31B.value


def _log_cache_stats(usage_obj, model: str, cache_key: Optional[str]) -> None:
    """Emit the prompt-cache hit rate for one call.

    THIS IS THE PROOF, AND TTFT IS NOT. A fast reply can be fast for reasons
    that have nothing to do with the cache, and a cache that silently stopped
    working looks exactly like a provider having a slow afternoon. The only
    honest signal is how many prompt tokens the provider says it served from
    cache, so it is logged on every call rather than inferred later.

    Expect a LOW ratio on the first call of a campaign and a high one after —
    the static prefix has to be seen once before it can be reused. A ratio that
    stays near zero across a busy campaign means something per-call has crept
    back to the front of the prompt (a name, a timestamp, a call id), which is
    the exact defect this logging exists to catch.

    Never raises: observability must not be able to break a live call.
    """
    try:
        details = getattr(usage_obj, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        if cached is None and isinstance(usage_obj, dict):
            details = usage_obj.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens")
        cached = int(cached or 0)

        prompt_tokens = getattr(usage_obj, "prompt_tokens", None)
        if prompt_tokens is None and isinstance(usage_obj, dict):
            prompt_tokens = usage_obj.get("prompt_tokens")
        prompt_tokens = int(prompt_tokens or 0)

        ratio = (cached / prompt_tokens) if prompt_tokens else 0.0
        logger.info(
            "cerebras_prompt_cache model=%s cache_key=%s prompt_tokens=%d "
            "cached_tokens=%d hit_ratio=%.2f",
            model, (cache_key or "-"), prompt_tokens, cached, ratio,
        )
    except Exception:  # noqa: BLE001 — see docstring
        pass


def _accumulate_tool_call_frags(sink: Dict[int, dict], frags) -> None:
    """Reassemble streamed tool-call fragments.

    OpenAI-compatible streaming splits a single tool call across many chunks:
    the function name arrives once, then ``arguments`` arrives as a series of
    string slices that must be concatenated in order. Keyed by ``index`` because
    a model may emit several tool calls in one turn, interleaved.
    """
    for frag in frags or []:
        idx = getattr(frag, "index", 0) or 0
        slot = sink.setdefault(idx, {"id": None, "name": None, "arguments": ""})
        if getattr(frag, "id", None):
            slot["id"] = frag.id
        fn = getattr(frag, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            if getattr(fn, "arguments", None):
                slot["arguments"] += fn.arguments


class CerebrasLLMProvider(LLMProvider):
    """Streaming chat provider backed by Cerebras Inference."""

    def __init__(self) -> None:
        self._client = None
        self._guard = get_provider_guard("cerebras")
        self._model: str = DEFAULT_CEREBRAS_MODEL
        self._temperature: float = 0.6
        self._max_tokens: int = 150
        self._circuit = CircuitBreaker(
            name="cerebras-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError},
        )

    async def initialize(self, config: dict) -> None:
        """Initialise the Cerebras client.

        Accepts ``api_key`` from config (per-tenant credential) or falls back to
        the ``CEREBRAS_API_KEY`` env var, matching how every other provider here
        resolves credentials.
        """
        try:
            from cerebras.cloud.sdk import AsyncCerebras
        except ImportError as exc:  # pragma: no cover - import guard
            raise ValueError(
                "cerebras-cloud-sdk is not installed. "
                "Add it to requirements.txt and reinstall."
            ) from exc

        api_key = config.get("api_key") or os.getenv("CEREBRAS_API_KEY")
        if not api_key:
            raise ValueError(
                "Cerebras API key not found. Set CEREBRAS_API_KEY in the "
                "environment or pass api_key in config."
            )

        self._model = config.get("model") or DEFAULT_CEREBRAS_MODEL
        self._temperature = config.get("temperature", 0.6)
        self._max_tokens = config.get("max_tokens", 150)

        # timeout bounds the whole request; max_retries=0 because retries are
        # handled below with our own backoff + circuit breaker, and letting the
        # SDK retry too would multiply the effective latency budget.
        self._client = AsyncCerebras(
            api_key=api_key,
            timeout=config.get("timeout", DEFAULT_LLM_TIMEOUT),
            max_retries=0,
        )

        logger.info(
            "CerebrasLLMProvider initialized: model=%s temperature=%s "
            "max_tokens=%s reasoning_effort=%s",
            self._model, self._temperature, self._max_tokens,
            self._reasoning_effort(self._model),
        )

    @staticmethod
    def _reasoning_effort(model: str) -> Optional[str]:
        """The lowest reasoning setting this model actually accepts.

        Cerebras does NOT expose a uniform switch, and sending an unsupported
        value is an API error rather than a silent no-op:

          gemma-4-31b   "none" | low | medium | high   -> "none"
          zai-glm-4.7   "none" disables reasoning      -> "none"
          gpt-oss-120b  low | medium | high ONLY       -> "low" (cannot be off)

        Returning None means "send nothing and let the model default stand" —
        only reached if a model id is passed that we have no entry for, where
        guessing would be worse than deferring to the API default.
        """
        m = (model or "").strip()
        if m in CEREBRAS_REASONING_NONE_SUPPORTED:
            return "none"
        return CEREBRAS_MIN_REASONING_EFFORT.get(m)

    def _build_request(
        self,
        *,
        messages: List[Message],
        system_prompt: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        model: Optional[str],
        tools: Optional[list],
        cache_key: Optional[str] = None,
    ) -> dict:
        model = model or self._model
        api_messages: List[dict] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            # Empty turns confuse chat models and some backends reject them
            # outright; skip rather than send a blank string.
            if not msg.content or not msg.content.strip():
                continue
            api_messages.append({"role": msg.role.value, "content": msg.content})

        request: dict = {
            "model": model,
            "messages": api_messages,
            "temperature": (
                self._temperature if temperature is None else temperature
            ),
            "max_completion_tokens": (
                self._max_tokens if max_tokens is None else max_tokens
            ),
            "stream": True,
        }

        effort = self._reasoning_effort(model)
        if effort is not None:
            request["reasoning_effort"] = effort

        # ROUTING HINT FOR THE PROMPT CACHE (2026-08-24).
        #
        # Cerebras caches by exact prefix and documents prompt_cache_key as the
        # way to tell the system "these requests share a prefix, keep them on
        # the same cache". It is free: it does not affect billing or output.
        #
        # The right key is the CAMPAIGN, not the call. Every call in a campaign
        # sends the same ~38k-char static prompt, so keying by campaign lets
        # them all share one warm cache; keying by call would isolate each one
        # and throw that sharing away.
        #
        # Worth knowing: Cerebras guarantees only a 5-minute TTL (up to an hour
        # under light load), so a campaign that trickles calls slowly will go
        # cold between them regardless of this hint.
        if cache_key:
            request["prompt_cache_key"] = str(cache_key)[:1024]

        # Ask for the usage block on the final SSE frame. Without this a
        # streamed response carries no usage at all, so cached_tokens — the only
        # honest proof that the cache is working — is simply absent.
        request["stream_options"] = {"include_usage": True}

        # clear_thinking is accepted by zai-glm-4.7 only. Without it the model
        # replays previous turns' thinking back into the prompt, so context — and
        # therefore cost and latency — grows every single turn.
        if model == CerebrasModel.ZAI_GLM_4_7.value:
            request["clear_thinking"] = True

        if tools:
            request["tools"] = tools

        return request

    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream response tokens.

        kwargs:
          model            override the configured model for this call
          tools            OpenAI-style tool schemas
          tool_calls_sink  dict that receives reassembled tool calls, if any
        """
        if self._client is None:
            raise ValueError("Cerebras provider not initialized")

        model = kwargs.get("model") or self._model
        tools = kwargs.get("tools")
        tool_calls_sink = kwargs.get("tool_calls_sink")
        # Campaign id when the caller threads one — see _build_request for why
        # the campaign, and not the call, is the right cache key.
        cache_key = kwargs.get("prompt_cache_key") or kwargs.get("campaign_id")

        request = self._build_request(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            tools=tools,
            cache_key=cache_key,
        )

        last_err: Optional[Exception] = None
        tokens_yielded = 0

        async with self._guard.acquire():
            for attempt in range(_LLM_MAX_RETRIES + 1):
                try:
                    async with self._circuit:
                        stream = await self._client.chat.completions.create(**request)
                        tc_acc: Dict[int, dict] = {}
                        async for chunk in stream:
                            # The usage block rides the FINAL frame, which has
                            # an empty `choices` list — so it has to be read
                            # before the skip below, or cache stats are silently
                            # thrown away on every call.
                            _usage = getattr(chunk, "usage", None)
                            if _usage is not None:
                                _log_cache_stats(_usage, model, cache_key)
                            choices = getattr(chunk, "choices", None)
                            if not choices:
                                continue
                            delta = choices[0].delta
                            content = getattr(delta, "content", None)
                            if content:
                                tokens_yielded += 1
                                yield content
                            if tool_calls_sink is not None:
                                frags = getattr(delta, "tool_calls", None)
                                if frags:
                                    _accumulate_tool_call_frags(tc_acc, frags)
                        if tool_calls_sink is not None and tc_acc:
                            tool_calls_sink.update(tc_acc)
                    return
                except Exception as exc:  # noqa: BLE001 - classified below
                    last_err = exc
                    # Once any token has been emitted the caller has already
                    # spoken part of the answer. Retrying would restart the
                    # reply mid-sentence, so surface the error instead.
                    if tokens_yielded > 0:
                        logger.error(
                            "Cerebras stream failed after %d tokens — not "
                            "retrying mid-utterance: %s", tokens_yielded, exc,
                        )
                        raise RuntimeError(f"Cerebras LLM streaming failed: {exc}")
                    if attempt >= _LLM_MAX_RETRIES:
                        break
                    delay = _LLM_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Cerebras retry %d/%d after %.2fs — %s",
                        attempt + 1, _LLM_MAX_RETRIES, delay, exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("Cerebras LLM streaming failed after retries: %s", last_err)
        raise RuntimeError(f"Cerebras LLM streaming failed: {last_err}")

    async def stream_chat_with_timeout(
        self,
        messages: List[Message],
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream with a hard wall-clock deadline and inter-token stall detection.

        THIS METHOD IS NOT OPTIONAL. The voice pipeline calls
        ``stream_chat_with_timeout`` — never ``stream_chat`` — on every turn
        (turn_streamer.py, llm_response.py, confirm_llm.py). It is absent from
        the ``LLMProvider`` ABC, so a provider that omits it type-checks and
        imports fine and then raises ``AttributeError`` on the first turn of
        every call. That is exactly what happened when Cerebras shipped without
        it: AI Options' "Test" button worked (it calls ``stream_chat``
        directly) while the campaign Test agent and real calls produced no
        reply at all.

        Logic mirrors ``GeminiLLMProvider.stream_chat_with_timeout``, which is
        provider-agnostic — it wraps ``stream_chat`` in per-token asyncio
        timeouts and adds nothing vendor-specific.

        The exception type matters: the pipeline catches
        ``app.infrastructure.llm.groq.LLMTimeoutError`` specifically, so we
        raise that one rather than defining another class it would not catch.

        Raises:
            LLMTimeoutError: no token arrived before the TTFT deadline.
        """
        from app.infrastructure.llm.groq import LLMTimeoutError

        _INTERTOKEN_TIMEOUT = 2.0

        # Budgets ONLY time spent awaiting Cerebras — never consumer-side TTS
        # playback. See GeminiLLMProvider.stream_chat_with_timeout for the full
        # explanation: the voice pipeline pulls tokens between real-time-paced
        # audio sends, so a wall-clock budget charged the caller's own playback
        # against the LLM allowance and truncated healthy multi-sentence replies
        # mid-thought with no error and no fallback line. Fixed 2026-08-06.
        cerebras_wait_accumulated = 0.0
        tokens_received = 0
        gen = self.stream_chat(messages, **kwargs)
        try:
            while True:
                remaining = timeout_seconds - cerebras_wait_accumulated
                if remaining <= 0:
                    if tokens_received > 0:
                        logger.warning(
                            "Cerebras-wait budget expired mid-stream "
                            "(limit=%.1fs, tokens=%d) — treating as stream end",
                            timeout_seconds, tokens_received,
                        )
                        break
                    logger.error(
                        "Cerebras deadline exceeded before first token "
                        "(limit=%.1fs)", timeout_seconds,
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                token_timeout = (
                    remaining if tokens_received == 0
                    else min(remaining, _INTERTOKEN_TIMEOUT)
                )
                _wait_t0 = asyncio.get_event_loop().time()
                try:
                    token = await asyncio.wait_for(
                        gen.__anext__(), timeout=token_timeout
                    )
                    # Charge ONLY the measured Cerebras-wait span, then yield.
                    cerebras_wait_accumulated += (
                        asyncio.get_event_loop().time() - _wait_t0
                    )
                    tokens_received += 1
                    yield token
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    cerebras_wait_accumulated += (
                        asyncio.get_event_loop().time() - _wait_t0
                    )
                    elapsed = cerebras_wait_accumulated
                    if tokens_received > 0:
                        logger.warning(
                            "Cerebras inter-token stall after %.2fs (tokens=%d) "
                            "— treating as stream end", elapsed, tokens_received,
                        )
                        break
                    logger.error(
                        "Cerebras timeout waiting for first token after %.2fs "
                        "(limit=%.1fs)", elapsed, timeout_seconds,
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
        finally:
            # Close the underlying stream so the HTTP connection is released
            # even when the caller stops iterating early (barge-in does this
            # on almost every turn).
            aclose = getattr(gen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass

    async def cleanup(self) -> None:
        """Release the client reference."""
        self._client = None

    @property
    def name(self) -> str:
        return "cerebras"

    @property
    def supports_streaming(self) -> bool:
        return True
