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
from typing import AsyncIterator, List, Optional
import httpx
from groq import AsyncGroq, APITimeoutError as GroqAPITimeoutError, RateLimitError as GroqRateLimitError
from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds)
DEFAULT_LLM_TIMEOUT = 10.0

# Retry configuration for transient Groq failures
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # 300ms — fast first retry for voice latency budget


class LLMTimeoutError(Exception):
    """Raised when LLM response times out"""
    pass


class GroqLLMProvider(LLMProvider):
    """
    Groq LLM provider with ultra-fast inference
    
    Production Models (available in AI Options):
    - llama-3.3-70b-versatile: 280 t/s - Best quality/speed balance (default)
    - llama-3.1-8b-instant: 560 t/s - Fastest, ideal for real-time
    - openai/gpt-oss-120b: 500 t/s - OpenAI flagship with reasoning
    - openai/gpt-oss-20b: 1000 t/s - Fast and efficient
    
    Preview Models (evaluation only):
    - meta-llama/llama-4-maverick-17b-128e-instruct: 600 t/s - Complex reasoning
    - meta-llama/llama-4-scout-17b-16e-instruct: 750 t/s - Fast variant
    - qwen/qwen3-32b: 400 t/s - Multilingual
    - moonshotai/kimi-k2-instruct-0905: 200 t/s - Large context (262K)
    
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
        """Qwen 3 supports explicit thinking / non-thinking modes on Groq."""
        return model.startswith("qwen/qwen3-")

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
        api_key = config.get("api_key") or os.getenv("GROQ_API_KEY")
        
        if not api_key:
            raise ValueError("Groq API key not found in config or environment")
        
        # Initialize async client with httpx-level timeouts (Groq SDK recommendation).
        # read=timeout bounds TTFT — if Groq takes longer than this to produce the
        # first token, httpx raises ReadTimeout → GroqAPITimeoutError before any
        # token is yielded.  connect=2s fails fast on network issues.
        self._client = AsyncGroq(
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=2.0,
                read=DEFAULT_LLM_TIMEOUT,
                write=10.0,
                pool=5.0,
            ),
        )
        
        # Configuration with voice-optimized defaults
        self._model = config.get("model", "llama-3.3-70b-versatile")
        self._temperature = config.get("temperature", 0.6)
        self._max_tokens = config.get("max_tokens", 150)

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

        Args:
            messages: Conversation history
            timeout_seconds: Hard wall-clock limit for the entire stream
            **kwargs: Passed to stream_chat

        Yields:
            str: Token/chunk of response

        Raises:
            LLMTimeoutError: If no token arrives before TTFT deadline (first token only)
        """
        _INTERTOKEN_TIMEOUT = 2.0  # Groq confirmed bug: stream stalls silently mid-stream

        t_start = asyncio.get_event_loop().time()
        tokens_received = 0
        gen = self.stream_chat(messages, **kwargs)
        try:
            while True:
                remaining = timeout_seconds - (asyncio.get_event_loop().time() - t_start)
                if remaining <= 0:
                    if tokens_received > 0:
                        # Wall-clock expired mid-stream — treat as normal end, content already TTS'd
                        logger.warning(
                            "LLM wall-clock expired mid-stream (limit=%.1fs, tokens=%d) — "
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
                try:
                    token = await asyncio.wait_for(gen.__anext__(), timeout=token_timeout)
                    if tokens_received == 0:
                        ttft_ms = (asyncio.get_event_loop().time() - t_start) * 1000
                        if ttft_ms > 800:
                            logger.warning(
                                "High TTFT: %.0fms — likely Groq rate limiting or cold cache. "
                                "Check Groq console for token bucket status.", ttft_ms
                            )
                    tokens_received += 1
                    yield token
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - t_start
                    if tokens_received > 0:
                        # Inter-token stall: Groq stopped sending tokens mid-stream silently.
                        # Content already yielded and TTS'd — break cleanly, no fallback needed.
                        logger.warning(
                            "Groq inter-token stall after %.2fs (tokens=%d) — "
                            "treating as stream end (Groq silent-stall bug)",
                            elapsed, tokens_received,
                        )
                        break
                    logger.error(
                        "LLM timeout waiting for first token after %.2fs (limit=%.1fs): %s",
                        elapsed, timeout_seconds, "asyncio.TimeoutError",
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                except GroqAPITimeoutError as exc:
                    elapsed = asyncio.get_event_loop().time() - t_start
                    logger.error(
                        "LLM Groq API timeout after %.2fs (limit=%.1fs, tokens=%d): %s",
                        elapsed, timeout_seconds, tokens_received, exc,
                    )
                    if tokens_received > 0:
                        break
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
        finally:
            await gen.aclose()
    
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
            
            # Log each message for debugging (truncated)
            for i, msg in enumerate(groq_messages):
                content_preview = msg['content'][:100] if msg['content'] else '<EMPTY>'
                logger.debug(f"Message {i}: role={msg['role']}, content='{content_preview}...'")
            
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
            }

            reasoning_format = kwargs.get("reasoning_format")
            include_reasoning = kwargs.get("include_reasoning")
            reasoning_effort = kwargs.get("reasoning_effort")
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
                # Default to "low" for voice pipelines — the model uses a small
                # number of reasoning tokens before the first output token, which
                # cuts TTFT by ~400-1000ms vs "medium" (the Groq default).
                # Callers can override by passing reasoning_effort= explicitly.
                if reasoning_effort is None:
                    reasoning_effort = "low"
                request_kwargs["reasoning_effort"] = reasoning_effort
            elif self._is_qwen3_model(model):
                # Groq recommends non-thinking mode for general dialogue.
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
            elif reasoning_format is not None:
                request_kwargs["reasoning_format"] = reasoning_format
                if reasoning_effort is not None:
                    request_kwargs["reasoning_effort"] = reasoning_effort

            # Stream completion using Groq's ultra-fast LPU
            # Wrapped with circuit breaker + retry for transient failures
            import random as _rand

            last_err = None
            tokens_yielded = 0  # Track across all attempts
            for _attempt in range(_LLM_MAX_RETRIES + 1):
                try:
                    async with self._circuit:
                        stream = await self._client.chat.completions.create(**request_kwargs)

                        # Yield tokens as they arrive
                        token_count = 0
                        async for chunk in stream:
                            if chunk.choices:
                                delta = chunk.choices[0].delta
                                if delta.content:
                                    token_count += 1
                                    tokens_yielded += 1
                                    yield delta.content

                        logger.debug(f"Stream completed, yielded {token_count} tokens")

                        if token_count == 0:
                            logger.warning("Zero tokens received from Groq")
                    # Success — break out of retry loop
                    break

                except CircuitOpenError:
                    raise  # Don't retry when circuit is open

                except GroqRateLimitError as e:
                    # HTTP 429 — token or request bucket exhausted.
                    # Retrying immediately won't help; log clearly so the operator
                    # knows to upgrade the Groq plan or reduce request frequency.
                    logger.warning(
                        "Groq rate limit hit (HTTP 429) — TTFT spikes are likely "
                        "rate-limit-induced. Consider upgrading to a paid Groq plan "
                        "or reducing concurrent requests. Error: %s", e
                    )
                    raise RuntimeError(f"Groq rate limit exceeded: {e}")

                except Exception as e:
                    last_err = e
                    # CRITICAL: Never retry after partial output — the caller has
                    # already received some tokens.  Retrying would yield tokens
                    # from a new request appended to the partial first response,
                    # producing garbled/doubled output visible to the user.
                    if tokens_yielded > 0:
                        logger.error(
                            f"Groq stream error after {tokens_yielded} tokens yielded — "
                            f"cannot retry mid-stream: {e}"
                        )
                        raise RuntimeError(f"Groq LLM streaming failed: {e}")
                    if _attempt < _LLM_MAX_RETRIES:
                        _delay = min(
                            _LLM_RETRY_BASE_DELAY * (2 ** _attempt),
                            5.0,
                        ) * (0.5 + _rand.random())
                        logger.warning(
                            f"Groq retry {_attempt + 1}/{_LLM_MAX_RETRIES} "
                            f"after {_delay:.2f}s — {e}"
                        )
                        await asyncio.sleep(_delay)
                    else:
                        logger.error(f"Groq LLM streaming failed after retries: {e}")
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
