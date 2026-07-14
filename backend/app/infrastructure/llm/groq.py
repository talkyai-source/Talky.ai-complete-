"""
Groq LLM Provider Implementation
Ultra-fast inference using Groq LPU architecture

Following Groq's official prompting guidelines:
- https://console.groq.com/docs/prompting
- Role channels (system, user, assistant)
- Parameter tuning for voice AI use case
- Stop sequences for cleaner outputs

Available models are defined in app/domain/models/ai_config.py (GROQ_MODELS)
and exposed via the AI Options UI at /api/v1/ai-options/providers

Day 17: Added timeout handling and deterministic mode for QA.
"""
import os
import asyncio
import logging
from typing import AsyncIterator, Dict, List, Optional
import httpx
from groq import AsyncGroq, APITimeoutError as GroqAPITimeoutError, RateLimitError as GroqRateLimitError
from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.infrastructure.providers.key_pool import KeyPool, parse_keys_csv
from app.infrastructure.providers.provider_concurrency import get_provider_guard
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds)
DEFAULT_LLM_TIMEOUT = 10.0

# Retry configuration for transient Groq failures
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # 300ms — fast first retry for voice latency budget

# Reasoning tokens count against max_completion_tokens, so on models whose
# thinking can't be fully disabled (GPT-OSS floors at reasoning_effort="low";
# Qwen3 only when run with effort != "none") a tight cap can be eaten by
# reasoning, leaving zero answer tokens. Mirror the Gemini fix: reserve thinking
# headroom ON TOP of the answer budget so reasoning never starves the reply.
# Models with thinking fully OFF (llama/kimi, or Qwen3 effort="none") get NO
# reserve — their max_tokens is purely the answer. Env-tunable.
_THINKING_RESERVE_TOKENS = int(os.getenv("GROQ_THINKING_RESERVE_TOKENS", "1024"))

# Debug logging of per-message CONTENT (first 100 chars) is off by default —
# in production debug mode it was writing caller PII and tenant operator
# instructions (system/user/assistant/tool message text) straight to logs.
# Structural info (role, content length, message count) is always safe and
# always logged; raw content only appears when an operator explicitly opts
# in for local troubleshooting.
_LOG_MESSAGE_CONTENT = os.getenv("LOG_MESSAGE_CONTENT", "false").strip().lower() in (
    "1", "true", "yes", "on",
)


def _coerce_int(value) -> int:
    """Best-effort int coercion for SDK fields whose shape may vary by
    version. Returns 0 on anything that can't be safely converted —
    we'd rather log a 0 than crash the call with a TypeError."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_cached_tokens(usage_obj) -> int:
    """Pull the cached-prompt-tokens count out of Groq's usage object.

    Groq nests the figure under ``prompt_tokens_details.cached_tokens``
    on the OpenAI-compatible API surface (May 2026). Older SDK versions
    expose it as a flat ``cached_tokens`` field. We check both shapes so
    a future SDK upgrade doesn't silently drop the metric.
    """
    if usage_obj is None:
        return 0
    flat = getattr(usage_obj, "cached_tokens", None)
    if flat is not None:
        return _coerce_int(flat)
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is not None:
        return _coerce_int(getattr(details, "cached_tokens", 0))
    # Some SDKs expose usage as a dict instead of a typed object.
    if isinstance(usage_obj, dict):
        if "cached_tokens" in usage_obj:
            return _coerce_int(usage_obj.get("cached_tokens"))
        details_d = usage_obj.get("prompt_tokens_details")
        if isinstance(details_d, dict):
            return _coerce_int(details_d.get("cached_tokens"))
    return 0


def _get_field(obj, name: str):
    """getattr-or-dict-get, safe across typed SDK objects and plain dicts."""
    if obj is None:
        return None
    val = getattr(obj, name, None)
    if val is not None:
        return val
    if isinstance(obj, dict):
        return obj.get(name)
    return None


def _extract_stream_usage(chunk):
    """Pull the usage object off a Groq streaming chunk, tolerating both SDK
    usage surfaces.

    Groq-python 0.37.x streaming chunks normally expose usage under the
    Groq-specific ``chunk.x_groq.usage`` field (present on the final content
    chunk of a stream). ``chunk.usage`` — the OpenAI-compatible field — is
    ONLY populated when the request sets ``stream_options={"include_usage":
    True}``, which Groq's SDK rejects outright (see the NOTE in stream_chat).
    So in practice ``chunk.usage`` is normally None and ``x_groq.usage`` is
    where the real numbers live; we check ``usage`` first anyway in case a
    future SDK version starts populating both, and fall back to
    ``x_groq.usage`` — whichever is present wins, both reads are getattr-safe
    so a shape change on either surface degrades to "no usage this chunk"
    instead of raising.
    """
    usage = _get_field(chunk, "usage")
    if usage is not None:
        return usage
    x_groq = _get_field(chunk, "x_groq")
    return _get_field(x_groq, "usage")


def _emit_usage_log(
    usage_obj,
    *,
    model: str,
    client_ttft_ms: Optional[float] = None,
    client_total_ms: Optional[float] = None,
    partial: bool = False,
) -> None:
    """Surface Groq's per-request token usage + server/client timing so
    operators can see when prompt caching is firing AND where time in a
    turn actually went (Groq queue/prompt/completion vs. our own network +
    client overhead).

    Emits a single structured log line at INFO with:
    * ``prompt_tokens``  — total prompt size sent (system + history)
    * ``cached_tokens``  — portion served from cache (system prompt
                            stays stable across turns of the same call;
                            once a call has 1k+ system-prompt tokens
                            this value should be non-zero on turn 2+)
    * ``cache_hit_ratio``— cached / prompt, 0.0–1.0
    * ``completion_tokens`` — yield this turn
    * ``queue_time`` / ``prompt_time`` / ``completion_time`` / ``total_time``
      — Groq's own server-side phase timings (seconds), when the SDK
      surfaces them (``x_groq.usage``: see Groq's "Understanding Metrics"
      docs). Any field the SDK doesn't provide logs as -1 rather than
      raising or silently omitting the field, so log parsers can rely on
      a stable line shape.
    * ``req_id`` — Groq's ``x_groq.id`` for cross-referencing with their
      console/support, when available.
    * ``client_ttft_ms`` — wall-clock from request dispatch to first
      content token, measured by the caller (stream_chat_with_timeout).
    * ``client_net_remainder_ms`` — client_ttft_ms minus Groq's own
      queue_time+prompt_time (both converted to ms). This is the part of
      TTFT Groq's own timings don't account for: our network hop +
      httpx/SDK overhead + (for the first token) part of completion_time.
      Negative or missing inputs log as -1 (best-effort, not exact).
    * ``partial`` — true when this line was emitted at stream end without
      a final usage chunk (e.g. barge-in cut the stream before Groq sent
      one) — token counts most-likely reflect a preceding partial log
      only, not this call; see the caller for the barge-in fallback path.

    A consistently-zero ``cache_hit_ratio`` after turn 0 is the signal
    that prompt caching is not engaging — typically because the system
    prompt is changing across turns or sits below Groq's 1k-token
    minimum cache threshold.

    Fail-soft: every field extraction below is getattr/dict-get safe with a
    default, and the whole function is wrapped by its caller so a logging
    bug can never break the token stream.
    """
    prompt_tokens = _coerce_int(_get_field(usage_obj, "prompt_tokens"))
    completion_tokens = _coerce_int(_get_field(usage_obj, "completion_tokens"))
    cached_tokens = _extract_cached_tokens(usage_obj)
    ratio = (cached_tokens / prompt_tokens) if prompt_tokens else 0.0

    # Groq server-side phase timings (seconds) — present on x_groq.usage,
    # absent on a bare OpenAI-shaped usage object. -1 marks "not provided"
    # rather than conflating it with a genuine 0.0s.
    def _timing(field: str) -> float:
        val = _get_field(usage_obj, field)
        if val is None:
            return -1.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return -1.0

    queue_time = _timing("queue_time")
    prompt_time = _timing("prompt_time")
    completion_time = _timing("completion_time")
    total_time = _timing("total_time")
    req_id = _get_field(usage_obj, "id") or "unknown"

    net_remainder_ms = -1.0
    if client_ttft_ms is not None and queue_time >= 0 and prompt_time >= 0:
        net_remainder_ms = client_ttft_ms - (queue_time + prompt_time) * 1000.0

    logger.info(
        "llm_usage model=%s partial=%s prompt_tokens=%d cached_tokens=%d "
        "cache_hit_ratio=%.2f completion_tokens=%d req_id=%s "
        "queue_time=%.3f prompt_time=%.3f completion_time=%.3f total_time=%.3f "
        "client_ttft_ms=%.0f client_total_ms=%.0f client_net_remainder_ms=%.0f",
        model, partial, prompt_tokens, cached_tokens,
        ratio, completion_tokens, req_id,
        queue_time, prompt_time, completion_time, total_time,
        client_ttft_ms if client_ttft_ms is not None else -1.0,
        client_total_ms if client_total_ms is not None else -1.0,
        net_remainder_ms,
    )
    # Mirror to Prometheus (T4-B2). The metric tracks the most-recent
    # ratio per (mode, persona); operators read it with avg_over_time
    # to spot caching regressions. mode/persona are unknown at this
    # provider-level call site; "unknown"/"none" labels keep the
    # metric callable without forcing the LLM provider to know about
    # call mode (a layering violation we'd regret later).
    try:
        from app.infrastructure.metrics.voice_metrics import (
            record_prompt_cache_hit_ratio,
        )
        record_prompt_cache_hit_ratio(ratio, mode="agent", persona=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_metrics_cache_record_failed err=%s", exc)


def _accumulate_tool_call_frags(acc: Dict[int, dict], frags) -> None:
    """Merge streamed tool-call delta fragments into ``acc`` keyed by index.

    Groq (OpenAI-compatible) streams a tool call across many chunks: the id +
    function name arrive once, the JSON ``arguments`` come as a string in
    pieces. We accumulate per call index until the stream ends.
    """
    for frag in frags:
        idx = getattr(frag, "index", 0) or 0
        slot = acc.setdefault(idx, {"id": None, "name": None, "arguments": ""})
        if getattr(frag, "id", None):
            slot["id"] = frag.id
        fn = getattr(frag, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            if getattr(fn, "arguments", None):
                slot["arguments"] += fn.arguments


def _finalize_tool_calls(acc: Dict[int, dict]) -> List[dict]:
    """Turn accumulated fragments into clean call dicts with parsed arguments.

    Each entry: {id, name, arguments_raw (str for the echo-back assistant msg),
    arguments (parsed dict for the tool runner)}.
    """
    import json

    out: List[dict] = []
    for idx in sorted(acc.keys()):
        slot = acc[idx]
        if not slot.get("name"):
            continue
        raw_args = slot.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args)
        except Exception:
            parsed = {}
        out.append({
            "id": slot.get("id") or f"call_{idx}",
            "name": slot["name"],
            "arguments_raw": raw_args,
            "arguments": parsed if isinstance(parsed, dict) else {},
        })
    return out


def _assistant_tool_call_message(calls: List[dict]) -> dict:
    """Build the assistant message that echoes the model's tool call(s) back,
    required by the API before the matching tool-result messages."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments_raw"]},
            }
            for c in calls
        ],
    }


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class GroqLLMProvider(LLMProvider):
    """
    Groq LLM provider with ultra-fast inference
    
    Production Models (available in AI Options):
    - llama-3.3-70b-versatile: 280 t/s - Best quality/speed balance (default)
    - llama-3.1-8b-instant: 560 t/s - Fastest, ideal for real-time

    Preview Models (evaluation only):
    - qwen/qwen3.6-27b: ~400 t/s - Multilingual, thinking off for voice

    Not offered (still handled if passed): openai/gpt-oss-* — agentic reasoners
    that misbehave on conversational voice. See _is_gpt_oss_model.
    
    Model selection is configured via AI Options UI (/ai-options page).
    See app/domain/models/ai_config.py for full model specifications.
    """
    
    # Default stop sequences to prevent rambling
    DEFAULT_STOP_SEQUENCES = ["User:", "Human:", "\n\n\n"]

    @staticmethod
    def _is_gpt_oss_model(model: str) -> bool:
        """GPT-OSS models on Groq use the reasoning-specific request contract."""
        return model.startswith("openai/gpt-oss-")

    @staticmethod
    def _is_qwen3_model(model: str) -> bool:
        """Qwen 3 supports explicit thinking / non-thinking modes on Groq.

        Matches the whole Qwen3 family — both ``qwen/qwen3-32b`` (dash) and
        ``qwen/qwen3.6-27b`` (dot) — so reasoning is driven off by default for
        all of them via reasoning_effort="none"."""
        return model.startswith("qwen/qwen3")

    @classmethod
    def _default_top_p_for_model(cls, model: str) -> float:
        """
        Use Groq-documented defaults per model family instead of forcing one
        sampling profile across all selectable AI Options models.
        """
        if cls._is_gpt_oss_model(model):
            return 0.95
        if cls._is_qwen3_model(model):
            return 0.8
        return 1.0

    @staticmethod
    def _inject_instructions_for_reasoning_model(
        *,
        system_prompt: Optional[str],
        messages: List[dict],
    ) -> List[dict]:
        """
        Groq recommends avoiding system prompts for GPT-OSS models and placing
        instructions in a user message instead.

        If the message list is empty or starts with an assistant message (e.g. the
        Ask AI greeting), the instructions are prepended as a standalone user
        message so that GPT-OSS always receives a user message first.
        """
        if not system_prompt:
            return messages

        instruction_block = (
            "Conversation instructions:\n"
            f"{system_prompt.strip()}\n\n"
            "Apply these instructions to every reply in this conversation."
        )

        # GPT-OSS requires the first message to be user role.
        # If history starts with an assistant message (e.g. greeting), prepend a
        # standalone user instruction message rather than trying to inject into a
        # later user message — that would leave the assistant message at index 0.
        if not messages or messages[0].get("role") != "user":
            return [{"role": "user", "content": instruction_block}, *messages]

        # Normal path: inject instructions into the first user message.
        merged_messages: List[dict] = []
        injected = False
        for message in messages:
            if not injected and message.get("role") == "user":
                merged_messages.append({
                    **message,
                    "content": (
                        f"{instruction_block}\n\n"
                        f"Current user message:\n{message.get('content', '')}"
                    ),
                })
                injected = True
                continue
            merged_messages.append(message)

        if not injected:
            merged_messages.insert(0, {
                "role": "user",
                "content": instruction_block,
            })

        return merged_messages
    
    def __init__(self):
        self._client: Optional[AsyncGroq] = None
        # Per-key client cache: AsyncGroq is keyed by api_key at construction
        # time, so multi-key routing builds one client per key on demand.
        self._clients_by_key: Dict[str, AsyncGroq] = {}
        self._pool: Optional[KeyPool] = None
        self._guard = get_provider_guard("groq")
        self._http_timeout: Optional[httpx.Timeout] = None
        self._config: dict = {}
        self._model: str = "llama-3.3-70b-versatile"  # Best balance for voice AI
        self._temperature: float = 0.6  # Slightly lower for more consistent responses
        # 150 tokens ≈ 110 words — enough for 2-3 full sentences covering most voice
        # responses while still keeping the AI concise.  100 truncated complex answers
        # mid-sentence; 200+ risks overly long responses that hurt conversational feel.
        self._max_tokens: int = 150
        # Deterministic mode settings (Day 17)
        self._deterministic_mode: bool = False
        self._deterministic_seed: Optional[int] = None
        # Circuit breaker: trips after 5 consecutive failures, re-probes after 30s
        self._circuit = CircuitBreaker(
            name="groq-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError, LLMTimeoutError},
        )
    
    async def initialize(self, config: dict) -> None:
        """Initialize Groq client with configuration"""
        self._config = config

        # Multi-key pool path overrides single-key when GROQ_API_KEYS is set
        # *and* no tenant-scoped api_key was supplied.
        pool_keys = parse_keys_csv(os.getenv("GROQ_API_KEYS"))
        single_key = config.get("api_key") or os.getenv("GROQ_API_KEY")
        if pool_keys and not config.get("api_key"):
            self._pool = KeyPool("groq", pool_keys)
            primary_key = pool_keys[0]
        else:
            self._pool = None
            primary_key = single_key

        if not primary_key:
            raise ValueError("Groq API key not found in config or environment")

        # httpx timeout shared by every per-key client.
        # read=timeout bounds TTFT — if Groq takes longer than this to produce
        # the first token, httpx raises ReadTimeout → GroqAPITimeoutError
        # before any token is yielded. connect=2s fails fast on network issues.
        self._http_timeout = httpx.Timeout(
            connect=2.0,
            read=DEFAULT_LLM_TIMEOUT,
            write=10.0,
            pool=5.0,
        )
        self._client = self._client_for(primary_key)

        # Configuration with voice-optimized defaults
        self._model = config.get("model", "llama-3.3-70b-versatile")
        self._temperature = config.get("temperature", 0.6)
        self._max_tokens = config.get("max_tokens", 150)

    def _client_for(self, api_key: str) -> AsyncGroq:
        """Return (and cache) an AsyncGroq client bound to the given key."""
        client = self._clients_by_key.get(api_key)
        if client is None:
            client = AsyncGroq(api_key=api_key, timeout=self._http_timeout)
            self._clients_by_key[api_key] = client
        return client

    async def warm_up(self) -> None:
        """
        Pre-warm the Groq httpx HTTP/2 + TLS pool and DNS cache.

        In user-first telephony mode the first real LLM call is the first agent
        response — a cold httpx connect of 80–200 ms sits directly on the
        critical path.  Issuing a tiny max_tokens=1 completion ahead of time
        seeds the connection pool so the first live call reuses a warm socket.

        Fire-and-forget: errors are logged but do not fail session creation.
        Ref: https://console.groq.com/docs/production-readiness/optimizing-latency
        """
        if self._client is None:
            return
        _t0 = asyncio.get_event_loop().time()
        try:
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                temperature=0.0,
                stream=False,
                timeout=1.5,
            )
            elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000.0
            logger.info(
                "groq_warmup_ok model=%s warmup_ms=%.0f",
                self._model, elapsed_ms,
                extra={"groq_warmup_ms": round(elapsed_ms)},
            )
        except Exception as exc:
            logger.warning("groq_warmup_failed model=%s: %s", self._model, exc)

    def set_deterministic_mode(self, enabled: bool = True, seed: int = 42):
        """
        Enable/disable deterministic mode for QA testing.
        
        In deterministic mode:
        - Temperature is set to 0.0 for reproducibility
        - Seed is fixed for consistent outputs
        
        Args:
            enabled: Whether to enable deterministic mode
            seed: Seed value for reproducibility
        """
        self._deterministic_mode = enabled
        self._deterministic_seed = seed if enabled else None
        if enabled:
            logger.info(f"Deterministic mode enabled with seed={seed}")
        else:
            logger.info("Deterministic mode disabled")
    
    async def stream_chat_with_timeout(
        self,
        messages: List[Message],
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion with a true hard deadline on every token.

        Two-layer timeout defence (Groq SDK recommendation + asyncio safety net):

        Layer 1 — httpx.Timeout(read=timeout_seconds) set on AsyncGroq at
          initialization.  httpx enforces this at the HTTP level: if no bytes
          arrive within `read` seconds (including TTFT), it raises
          GroqAPITimeoutError *before* any token is yielded.  This is the primary
          guard and handles the slow-first-token case the old post-hoc check missed.

        Layer 2 — asyncio.wait_for() per __anext__() call.  Two budgets apply:
          - Before first token: full `remaining` wall-clock budget (TTFT guard).
          - After first token: min(remaining, _INTERTOKEN_TIMEOUT) per token.
            If Groq silently stalls mid-stream (confirmed bug: stream stops
            without finish_reason or exception), we detect it in 2s and break
            cleanly rather than waiting up to 9s and then discarding content.

        The `timeout_seconds` budget measures ONLY the time spent *awaiting the
        next token from Groq* — it is accumulated across the token-fetch awaits
        and explicitly EXCLUDES the time the consumer holds this generator
        suspended at `yield` (real-time-paced TTS/playback between token pulls).
        Counting playback time against the LLM budget was truncating healthy,
        valid multi-sentence replies mid-sentence: the generator is suspended at
        its `yield` while audio plays, so a plain wall-clock kept ticking on time
        we never spent waiting on Groq. Genuine Groq slowness is still caught —
        the per-await `_INTERTOKEN_TIMEOUT` bounds every single token wait, and
        the accumulated Groq-wait budget bounds the total.

        Args:
            messages: Conversation history
            timeout_seconds: Budget for total time spent WAITING ON GROQ (not
                downstream playback) across the whole stream
            **kwargs: Passed to stream_chat

        Yields:
            str: Token/chunk of response

        Raises:
            LLMTimeoutError: If no token arrives before TTFT deadline (first token only)
        """
        _INTERTOKEN_TIMEOUT = 2.0  # Groq confirmed bug: stream stalls silently mid-stream

        # Time spent INSIDE the awaits that fetch the next token from Groq.
        # The gap between yielding a token and being asked for the next one
        # (consumer-side TTS/playback) is NOT added here, so downstream pacing
        # can never consume the LLM budget.
        groq_wait_accumulated = 0.0
        tokens_received = 0
        gen = self.stream_chat(messages, **kwargs)
        try:
            while True:
                remaining = timeout_seconds - groq_wait_accumulated
                if remaining <= 0:
                    if tokens_received > 0:
                        # Budget of actual Groq-wait time exhausted mid-stream —
                        # content already yielded/TTS'd, treat as normal end.
                        logger.warning(
                            "LLM Groq-wait budget expired mid-stream (limit=%.1fs, tokens=%d) — "
                            "treating as stream end", timeout_seconds, tokens_received
                        )
                        break
                    logger.error(
                        "LLM deadline exceeded before first token "
                        "(limit=%.1fs)", timeout_seconds
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                # Use tight inter-token timeout after first token to catch Groq silent stalls
                token_timeout = remaining if tokens_received == 0 else min(remaining, _INTERTOKEN_TIMEOUT)
                _wait_t0 = asyncio.get_event_loop().time()
                try:
                    token = await asyncio.wait_for(gen.__anext__(), timeout=token_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    # Only the Groq wait counts — a full `token_timeout` elapsed here.
                    groq_wait_accumulated += asyncio.get_event_loop().time() - _wait_t0
                    if tokens_received > 0:
                        # Inter-token stall: Groq stopped sending tokens mid-stream silently.
                        # Content already yielded and TTS'd — break cleanly, no fallback needed.
                        logger.warning(
                            "Groq inter-token stall (groq_wait=%.2fs, tokens=%d) — "
                            "treating as stream end (Groq silent-stall bug)",
                            groq_wait_accumulated, tokens_received,
                        )
                        break
                    logger.error(
                        "LLM timeout waiting for first token after %.2fs (limit=%.1fs): %s",
                        groq_wait_accumulated, timeout_seconds, "asyncio.TimeoutError",
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                except GroqAPITimeoutError as exc:
                    groq_wait_accumulated += asyncio.get_event_loop().time() - _wait_t0
                    logger.error(
                        "LLM Groq API timeout after %.2fs (limit=%.1fs, tokens=%d): %s",
                        groq_wait_accumulated, timeout_seconds, tokens_received, exc,
                    )
                    if tokens_received > 0:
                        break
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                # Success: charge ONLY the just-measured Groq-wait span to the
                # budget, then yield. Whatever downstream time the consumer spends
                # before pulling again lands OUTSIDE this measured span.
                groq_wait_accumulated += asyncio.get_event_loop().time() - _wait_t0
                if tokens_received == 0:
                    ttft_ms = groq_wait_accumulated * 1000
                    if ttft_ms > 800:
                        logger.warning(
                            "High TTFT: %.0fms — likely Groq rate limiting or cold cache. "
                            "Check Groq console for token bucket status.", ttft_ms
                        )
                tokens_received += 1
                yield token
        finally:
            await gen.aclose()
    
    async def stream_chat_with_tools(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[List[dict]] = None,
        tool_runner=None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream a turn that MAY call a function tool, yielding only spoken
        content (same str contract as stream_chat_with_timeout, so the voice
        pipeline's streaming loop is unchanged).

        Round 0: the model decides — it either answers directly (the fast,
        common path: zero retrieval, small prompt) or emits a tool call.
        Round 1 (only if it called the tool): run ``tool_runner`` for the
        requested fact(s), append the tool result, and stream the grounded
        answer. No tools are offered in round 1, so the model cannot loop.

        ``tool_runner`` is an async callable ``(name, arguments_dict) -> str``.
        With no tools/runner this degrades to the normal timeout-guarded stream.
        """
        if not tools or tool_runner is None:
            async for tok in self.stream_chat_with_timeout(
                messages, timeout_seconds=timeout_seconds, system_prompt=system_prompt,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            ):
                yield tok
            return

        sink: List[dict] = []
        produced_content = False
        # Round 0 — let the model decide. A clean tool-only response ends via
        # StopAsyncIteration (no content), so the TTFT guard does NOT misfire.
        async for tok in self.stream_chat_with_timeout(
            messages, timeout_seconds=timeout_seconds, system_prompt=system_prompt,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools, tool_choice="auto", tool_calls_sink=sink, **kwargs,
        ):
            produced_content = True
            yield tok

        # Answered directly, or nothing to look up → done.
        if produced_content or not sink:
            return

        # Round 1 — execute the tool(s) and stream the grounded answer.
        extra: List[dict] = [_assistant_tool_call_message(sink)]
        for call in sink:
            try:
                result = await tool_runner(call["name"], call["arguments"])
            except Exception as exc:  # never let a tool failure stall the turn
                logger.warning("tool_runner failed name=%s: %s", call.get("name"), exc)
                result = "No specific information found."
            extra.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result or "No specific information found.",
            })

        async for tok in self.stream_chat_with_timeout(
            messages, timeout_seconds=timeout_seconds, system_prompt=system_prompt,
            temperature=temperature, max_tokens=max_tokens, extra_messages=extra, **kwargs,
        ):
            yield tok

    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens from Groq
        
        Following Groq's official parameter guidelines:
        - Temperature 0.2-0.4 for factual, 0.6-0.8 for conversational
        - Some reasoning-capable models have model-specific defaults/recommendations
        - Stop sequences prevent rambling
        
        Args:
            messages: Conversation history
            system_prompt: System instructions for the AI
            temperature: Randomness (0.0-2.0), defaults to 0.6 for voice
            max_tokens: Maximum response length, defaults to 100 for voice
            **kwargs: Additional parameters (model, stop, top_p, seed)
        
        Yields:
            str: Token/chunk of response
        """
        if not self._client:
            raise RuntimeError("Groq client not initialized. Call initialize() first.")
        
        # Apply deterministic mode settings if enabled
        if self._deterministic_mode:
            temperature = 0.0
            kwargs["seed"] = self._deterministic_seed
        else:
            temperature = temperature if temperature is not None else self._temperature
        
        max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        
        # Get model from kwargs or use configured default
        model = kwargs.get("model", self._model)

        # On-demand tool-calling (opt-in, see voice_pipeline/knowledge_tool.py).
        # All default None → the normal non-tool path is byte-for-byte unchanged.
        #   tools/tool_choice  — passed straight to the Groq request.
        #   tool_calls_sink    — a list the caller owns; assembled tool calls are
        #                        appended to it (content is NOT yielded for them).
        #   extra_messages     — raw role dicts (assistant tool_calls + tool
        #                        results) appended after history for the 2nd round.
        tools = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice")
        tool_calls_sink = kwargs.get("tool_calls_sink")
        extra_messages = kwargs.get("extra_messages")

        # Build messages array for Groq API using role channels
        groq_messages = []
        
        # Groq reasoning docs recommend placing GPT-OSS instructions in a user
        # message instead of a system message for best adherence.
        if system_prompt and not self._is_gpt_oss_model(model):
            groq_messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # User/Assistant channels: Conversation history
        for msg in messages:
            # Skip empty messages - they can cause issues with the LLM
            if not msg.content or not msg.content.strip():
                logger.warning(f"[GROQ DEBUG] Skipping empty {msg.role.value} message in conversation history")
                continue
                
            groq_messages.append({
                "role": msg.role.value,
                "content": msg.content
            })
        
        if self._is_gpt_oss_model(model):
            groq_messages = self._inject_instructions_for_reasoning_model(
                system_prompt=system_prompt,
                messages=groq_messages,
            )

        # Tool round-2: append the assistant tool_call turn + tool result(s) so
        # the model answers grounded in what the tool returned.
        if extra_messages:
            groq_messages.extend(extra_messages)

        # Validate temperature (Groq accepts 0.0-2.0)
        if not 0.0 <= temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {temperature}")
        
        # Stop sequences - use provided or defaults for voice AI
        stop_sequences = kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES)
        
        try:
            # Log what we're sending to Groq
            logger.debug(f"Sending to Groq: model={model}, temp={temperature}, max_tokens={max_tokens}")
            logger.debug(f"Messages count: {len(groq_messages)}")
            logger.debug(f"Stop sequences: {stop_sequences}")
            
            # Log each message for debugging. Content (caller PII / tenant
            # operator instructions) is NOT logged by default — only role and
            # length, which is enough to debug shape/ordering issues without
            # writing sensitive text to logs. Set LOG_MESSAGE_CONTENT=true to
            # opt into the old truncated-content preview for local debugging.
            for i, msg in enumerate(groq_messages):
                content = msg['content']
                content_len = len(content) if content else 0
                if _LOG_MESSAGE_CONTENT:
                    content_preview = content[:100] if content else '<EMPTY>'
                    logger.debug(f"Message {i}: role={msg['role']}, content='{content_preview}...'")
                else:
                    logger.debug(
                        f"Message {i}: role={msg['role']}, content_len={content_len}"
                        f"{' <EMPTY>' if not content else ''}"
                    )
            
            top_p = kwargs.get("top_p")
            if top_p is None:
                top_p = self._default_top_p_for_model(model)

            request_kwargs = {
                "model": model,
                "messages": groq_messages,
                "temperature": temperature,
                "max_completion_tokens": max_tokens,
                "stream": True,
                "top_p": top_p,
                "stop": stop_sequences,
                # Optional: seed for deterministic outputs (useful for testing)
                "seed": kwargs.get("seed", None),
                # NOTE: OpenAI's `stream_options={"include_usage": True}` is
                # not accepted by groq-python 0.37.x — passing it raises
                # `unexpected keyword argument 'stream_options'` and breaks
                # the warmup gate, refusing every outbound call. Groq still
                # returns `usage` on the final non-empty chunk for some
                # models, which the loop below picks up via getattr().
            }

            # Function tools (opt-in). When present the model may answer
            # directly OR emit a tool call; tool_choice="auto" lets it decide.
            if tools:
                request_kwargs["tools"] = tools
                request_kwargs["tool_choice"] = tool_choice or "auto"

            reasoning_format = kwargs.get("reasoning_format")
            include_reasoning = kwargs.get("include_reasoning")
            reasoning_effort = kwargs.get("reasoning_effort")
            # True when the model still spends reasoning tokens (thinking can't be
            # fully turned off, or is left on) — those tokens count against
            # max_completion_tokens, so we reserve headroom for them below.
            thinking_floored = False
            if self._is_gpt_oss_model(model):
                if include_reasoning is None:
                    include_reasoning = False
                request_kwargs["include_reasoning"] = include_reasoning
                if reasoning_format is not None:
                    logger.warning(
                        "Ignoring reasoning_format=%s for GPT-OSS model %s; "
                        "Groq documents include_reasoning for these models instead.",
                        reasoning_format,
                        model,
                    )
                # GPT-OSS cannot fully disable reasoning — Groq accepts only
                # low/medium/high (no "none"). Default to the "low" floor for
                # voice: a small number of reasoning tokens before the first
                # output token, which cuts TTFT by ~400-1000ms vs "medium" (the
                # Groq default). Callers can override by passing reasoning_effort=.
                if reasoning_effort is None:
                    reasoning_effort = "low"
                request_kwargs["reasoning_effort"] = reasoning_effort
                # Floored, never off → always reserve answer headroom.
                thinking_floored = True
            elif self._is_qwen3_model(model):
                # Groq recommends non-thinking mode for general dialogue — Qwen3
                # CAN be turned fully off via reasoning_effort="none".
                if reasoning_effort is None:
                    reasoning_effort = "none"
                if reasoning_format is None:
                    reasoning_format = "hidden"
                request_kwargs["reasoning_effort"] = reasoning_effort
                request_kwargs["reasoning_format"] = reasoning_format
                if include_reasoning is not None:
                    logger.warning(
                        "Ignoring include_reasoning=%s for Qwen model %s; "
                        "Groq documents reasoning_format/reasoning_effort for this family.",
                        include_reasoning,
                        model,
                    )
                # Only reserve headroom if reasoning is actually left on.
                thinking_floored = reasoning_effort != "none"
            elif reasoning_format is not None:
                request_kwargs["reasoning_format"] = reasoning_format
                if reasoning_effort is not None:
                    request_kwargs["reasoning_effort"] = reasoning_effort
                thinking_floored = reasoning_effort not in (None, "none")

            # Reasoning tokens share the max_completion_tokens ceiling with the
            # answer. When thinking is on, add a reserve on top so the caller's
            # max_tokens stays fully available for the visible reply (mirrors the
            # Gemini additive-budget fix). Non-thinking models keep max_tokens as-is.
            if thinking_floored:
                request_kwargs["max_completion_tokens"] = (
                    max_tokens + _THINKING_RESERVE_TOKENS
                )

            # Stream completion using Groq's ultra-fast LPU
            # Wrapped with circuit breaker + retry for transient failures.
            # Concurrency guard caps in-flight requests at the contracted plan limit.
            import random as _rand
            from app.infrastructure.tts.elevenlabs_tts import _SingleKeyLease

            last_err = None
            tokens_yielded = 0  # Track across all attempts

            async with self._guard.acquire():
                for _attempt in range(_LLM_MAX_RETRIES + 1):
                    key_ctx = (
                        self._pool.acquire() if self._pool is not None
                        else _SingleKeyLease("")
                    )
                    async with key_ctx as _lease:
                        # Use the per-key client when a pool is in play; else
                        # the configured single-key client (preserves existing
                        # behaviour and test mockability).
                        chosen_client = (
                            self._client_for(_lease.key)
                            if self._pool is not None and _lease.key
                            else self._client
                        )
                        try:
                            async with self._circuit:
                                stream = await chosen_client.chat.completions.create(
                                    **request_kwargs
                                )

                                # Yield tokens as they arrive. Normal Groq
                                # streaming usage arrives on the Groq-specific
                                # chunk.x_groq.usage field (chunk.usage is only
                                # populated when stream_options.include_usage is
                                # set, which Groq rejects — see NOTE above);
                                # _extract_stream_usage checks both, getattr-safe.
                                token_count = 0
                                final_usage = None
                                tc_acc: Dict[int, dict] = {}
                                # Client-side timing for telemetry only — never
                                # gates yielding. dispatch = right before we
                                # start consuming the stream; first_content_t =
                                # the moment the first content token is ready.
                                _t_dispatch = asyncio.get_event_loop().time()
                                _first_content_t: Optional[float] = None
                                try:
                                    async for chunk in stream:
                                        if chunk.choices:
                                            delta = chunk.choices[0].delta
                                            if delta.content:
                                                if _first_content_t is None:
                                                    _first_content_t = asyncio.get_event_loop().time()
                                                token_count += 1
                                                tokens_yielded += 1
                                                yield delta.content
                                            # Assemble streamed tool-call fragments
                                            # (name + arguments arrive in pieces).
                                            if tool_calls_sink is not None:
                                                frags = getattr(delta, "tool_calls", None)
                                                if frags:
                                                    _accumulate_tool_call_frags(tc_acc, frags)
                                        chunk_usage = _extract_stream_usage(chunk)
                                        if chunk_usage is not None:
                                            final_usage = chunk_usage
                                finally:
                                    # Fail-soft telemetry tail. Runs on normal
                                    # stream completion AND on early close —
                                    # e.g. barge-in causes the consumer
                                    # (stream_chat_with_timeout) to call
                                    # gen.aclose(), which raises GeneratorExit at
                                    # our current `yield` above; this `finally`
                                    # still runs so we log what we captured
                                    # instead of nothing. Never let a telemetry
                                    # bug break the token stream or propagate
                                    # past the exception that triggered us here.
                                    try:
                                        client_ttft_ms = (
                                            (_first_content_t - _t_dispatch) * 1000.0
                                            if _first_content_t is not None else None
                                        )
                                        client_total_ms = (
                                            asyncio.get_event_loop().time() - _t_dispatch
                                        ) * 1000.0
                                        if final_usage is not None:
                                            _emit_usage_log(
                                                final_usage, model=model,
                                                client_ttft_ms=client_ttft_ms,
                                                client_total_ms=client_total_ms,
                                            )
                                        elif token_count > 0:
                                            # No final usage chunk arrived — most
                                            # likely a barge-in closed the stream
                                            # before Groq emitted one (or this
                                            # model/SDK path never sends it).
                                            # Content was demonstrably yielded and
                                            # TTS'd, so log what's available
                                            # rather than staying silent.
                                            _emit_usage_log(
                                                None, model=model,
                                                client_ttft_ms=client_ttft_ms,
                                                client_total_ms=client_total_ms,
                                                partial=True,
                                            )
                                    except Exception as _telemetry_exc:  # noqa: BLE001
                                        logger.debug(
                                            "groq_usage_telemetry_failed err=%s",
                                            _telemetry_exc,
                                        )

                                # Surface any assembled tool calls to the caller.
                                # Only reached on a fully-successful stream, so a
                                # retry (tokens_yielded==0) can never double-fill.
                                if tool_calls_sink is not None and tc_acc:
                                    tool_calls_sink.extend(_finalize_tool_calls(tc_acc))

                                logger.debug(
                                    f"Stream completed, yielded {token_count} tokens"
                                )
                                if token_count == 0 and not (tool_calls_sink is not None and tc_acc):
                                    logger.warning("Zero tokens received from Groq")
                            _lease.report_success()
                            return  # success — exit retry loop AND generator

                        except CircuitOpenError:
                            raise  # Don't retry when circuit is open

                        except GroqRateLimitError as e:
                            _lease.report_failure(retryable=True)
                            logger.warning(
                                "Groq rate limit hit (HTTP 429) — TTFT spikes are "
                                "likely rate-limit-induced. Error: %s", e
                            )
                            raise RuntimeError(f"Groq rate limit exceeded: {e}")

                        except Exception as e:
                            _lease.report_failure(retryable=True)
                            last_err = e
                            # CRITICAL: never retry after partial output — would
                            # yield duplicated tokens to the user.
                            if tokens_yielded > 0:
                                logger.error(
                                    f"Groq stream error after {tokens_yielded} "
                                    f"tokens — cannot retry mid-stream: {e}"
                                )
                                raise RuntimeError(f"Groq LLM streaming failed: {e}")
                            if _attempt < _LLM_MAX_RETRIES:
                                _delay = min(
                                    _LLM_RETRY_BASE_DELAY * (2 ** _attempt), 5.0
                                ) * (0.5 + _rand.random())
                                logger.warning(
                                    f"Groq retry {_attempt + 1}/{_LLM_MAX_RETRIES} "
                                    f"after {_delay:.2f}s — {e}"
                                )
                                await asyncio.sleep(_delay)
                            else:
                                logger.error(
                                    f"Groq LLM streaming failed after retries: {e}"
                                )
                                raise RuntimeError(f"Groq LLM streaming failed: {e}")

        except CircuitOpenError as co:
            logger.error(f"Groq circuit breaker open: {co}")
            raise RuntimeError(f"LLM provider unavailable: {co}")
        except Exception as e:
            if not isinstance(e, RuntimeError):
                logger.error(f"Groq LLM streaming failed: {str(e)}")
                raise RuntimeError(f"Groq LLM streaming failed: {str(e)}"
            )
            raise
    
    async def cleanup(self) -> None:
        """Release resources"""
        if self._client:
            # Groq async client doesn't require explicit cleanup
            # but we'll set it to None for garbage collection
            self._client = None
    
    @property
    def name(self) -> str:
        """Provider name"""
        return "groq"
    
    @property
    def supports_streaming(self) -> bool:
        """Groq supports token streaming"""
        return True
    
    @property
    def is_deterministic(self) -> bool:
        """Check if deterministic mode is enabled"""
        return self._deterministic_mode
    
    def __repr__(self) -> str:
        mode = "deterministic" if self._deterministic_mode else "normal"
        return f"GroqLLMProvider(model={self._model}, temp={self._temperature}, mode={mode})"
