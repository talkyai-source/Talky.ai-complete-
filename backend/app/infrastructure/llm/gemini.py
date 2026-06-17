"""
Google Gemini LLM Provider Implementation

Uses the modern unified `google-genai` Python SDK (NOT the deprecated
`google-generativeai` package). Same SDK and API key (`GEMINI_API_KEY`) will
later cover Gemma 4 once it's exposed via Google AI Studio.

Available models are defined in app/domain/models/ai_config.py (GEMINI_MODELS)
and exposed via the AI Options UI at /api/v1/ai-options/providers.

Reference:
- https://ai.google.dev/gemini-api/docs/models
- https://googleapis.github.io/python-genai/
"""
import os
import asyncio
import logging
from typing import AsyncIterator, List, Optional

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds). Mirrors GroqLLMProvider so
# operators only have to learn one number.
DEFAULT_LLM_TIMEOUT = 10.0

# Retry configuration for transient Gemini failures
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # 300ms — fast first retry for voice latency budget


class LLMTimeoutError(Exception):
    """Raised when LLM response times out (parallels groq.LLMTimeoutError)."""
    pass


class GeminiLLMProvider(LLMProvider):
    """
    Google Gemini provider using the google-genai SDK.

    Production model:
    - gemini-2.5-flash: low-latency streaming, ~1M context, 65K max output

    Reserved for later (architecture supports them with no code change beyond
    registering the model name in GEMINI_MODELS):
    - gemma-4-31b-it: 31B dense, 256K context — once Google AI Studio exposes it
    - gemma-4-26b-a4b-it: 26B MoE — once Google AI Studio exposes it
    """

    # Same defaults as Groq so swapping providers doesn't change agent behaviour
    # in subtle ways.
    DEFAULT_STOP_SEQUENCES = ["User:", "Human:", "\n\n\n"]

    def __init__(self) -> None:
        self._client = None  # google.genai.Client
        self._config: dict = {}
        self._model: str = "gemini-2.5-flash"
        # 0.7 matches Gemini's recommended conversational sweet spot. Voice
        # configs typically override to ~0.6 via VoiceSessionConfig.
        self._temperature: float = 0.7
        self._max_tokens: int = 150
        # Thinking budget:
        #   0     -> disable thinking (DEFAULT — this is a real-time voice
        #            agent; "reasoning tokens" are pure latency when you just
        #            want a short spoken reply). Truly disables thinking on the
        #            Gemini 2.5 family.
        #   N > 0 -> cap thinking at N tokens (2.5 family).
        #   None  -> let Gemini decide dynamically (thinking ON) — opt-in only.
        # NOTE: Gemini 3.x (e.g. gemini-3.x-flash-lite) IGNORES this — it has a
        # "minimal" thinking floor that cannot be turned off, so it still
        # reasons mid-stream and is slow for voice. Use a 2.5 model for true
        # thinking-off. See _build_thinking_config.
        self._thinking_budget: Optional[int] = 0
        # Circuit breaker mirrors Groq settings for behavioural symmetry. If
        # Gemini-specific tuning becomes needed later, adjust here only.
        self._circuit = CircuitBreaker(
            name="gemini-llm",
            failure_threshold=5,
            recovery_timeout=30.0,
            success_threshold=2,
            excluded_exceptions={ValueError, LLMTimeoutError},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, config: dict) -> None:
        """Initialise the Gemini client.

        Accepts api_key from `config["api_key"]` or `GEMINI_API_KEY` env.
        """
        self._config = config
        api_key = config.get("api_key") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "Gemini API key not found. Set GEMINI_API_KEY in env or "
                "pass api_key in config."
            )

        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "google-genai package is not installed. "
                "Run: pip install google-genai"
            ) from exc

        # genai.Client is sync to construct; the .aio sub-client is async.
        self._client = genai.Client(api_key=api_key)

        self._model = config.get("model", "gemini-2.5-flash")
        self._temperature = float(config.get("temperature", 0.7))
        self._max_tokens = int(config.get("max_tokens", 150))
        # Default to 0 (thinking OFF) when the caller doesn't specify — voice
        # wants the lowest latency. Pass thinking_budget=None explicitly to opt
        # back into dynamic thinking.
        raw_thinking = config.get("thinking_budget", 0)
        self._thinking_budget = (
            int(raw_thinking) if raw_thinking is not None else None
        )

        # Validate temperature against Gemini's accepted range (0.0–2.0).
        if not 0.0 <= self._temperature <= 2.0:
            raise ValueError(
                f"Temperature must be between 0.0 and 2.0, got {self._temperature}"
            )

        logger.info(
            "GeminiLLMProvider initialized: model=%s, temperature=%s, "
            "max_tokens=%s, thinking_budget=%s",
            self._model, self._temperature, self._max_tokens,
            self._thinking_budget,
        )

    async def warm_up(self) -> None:
        """Pre-warm the HTTP/2 + TLS pool with a tiny request.

        Mirrors GroqLLMProvider.warm_up() so the orchestrator's warmup logic
        (in `_on_ringing`) works identically for either provider. Fire-and-forget;
        errors are logged but never block session creation.
        """
        if self._client is None:
            return
        _t0 = asyncio.get_event_loop().time()
        try:
            from google.genai import types as genai_types

            warm_cfg_kwargs = {"temperature": 0.0, "max_output_tokens": 1}
            warm_tc = self._build_thinking_config(self._model, self._thinking_budget)
            if warm_tc is not None:
                warm_cfg_kwargs["thinking_config"] = warm_tc
            await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents="hi",
                    config=genai_types.GenerateContentConfig(**warm_cfg_kwargs),
                ),
                timeout=2.0,
            )
            elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000.0
            logger.info(
                "gemini_warmup_ok model=%s warmup_ms=%.0f",
                self._model, elapsed_ms,
                extra={"gemini_warmup_ms": round(elapsed_ms)},
            )
        except Exception as exc:
            logger.warning("gemini_warmup_failed model=%s: %s", self._model, exc)

    async def cleanup(self) -> None:
        """Release client reference for GC."""
        self._client = None

    @staticmethod
    def _build_thinking_config(model: str, thinking_budget: Optional[int]):
        """Pick the thinking knob the model actually honours, or None.

        The voice path expresses "thinking off" as ``thinking_budget=0`` — the
        right thing to do for low latency. But that parameter only works on the
        Gemini 2.5 family. Gemini 3.x **ignores** ``thinking_budget`` (it uses
        ``thinking_level``) and cannot have thinking fully disabled; ``minimal``
        is its lowest-latency level. Sending the 2.5-era param to a 3.x model is
        a silent no-op, so the model can still spend time reasoning mid-stream —
        the call-stall traced on 2026-06-09. Translate the caller's intent into
        whichever knob the model's family honours.
        """
        try:
            from google.genai import types as genai_types
        except ImportError:
            return None
        if not hasattr(genai_types, "ThinkingConfig"):
            return None

        m = (model or "").lower()
        is_gemini_3 = m.startswith("gemini-3") or m in {
            "gemini-flash-latest", "gemini-pro-latest",
        }
        if is_gemini_3:
            # thinking_budget is ignored on 3.x — map intent to thinking_level.
            # 0 / unset -> "minimal" (lowest latency, what voice wants).
            level = "low" if (thinking_budget or 0) > 0 else "minimal"
            try:
                return genai_types.ThinkingConfig(thinking_level=level)
            except Exception:  # noqa: BLE001 — SDK predates thinking_level
                return None
        # Gemini 2.5 (and anything else that takes a budget): honour it if given.
        if thinking_budget is None:
            return None
        return genai_types.ThinkingConfig(thinking_budget=int(thinking_budget))

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens from Gemini.

        Args:
            messages: Conversation history.
            system_prompt: System instructions. Passed via Gemini's
                `system_instruction` config field, NOT prepended to messages —
                Gemini keeps the system role separate from user/model turns.
            temperature: Randomness (0.0–2.0). Defaults to provider config.
            max_tokens: Maximum response length. Defaults to provider config.
            **kwargs: Reserved for future model-specific options. `stop` is
                accepted to override DEFAULT_STOP_SEQUENCES.

        Yields:
            str: Token chunks from the model. Empty/None chunks are filtered
            out so the TTS path never sees a no-op string.
        """
        if not self._client:
            raise RuntimeError("Gemini client not initialized. Call initialize() first.")

        from google.genai import types as genai_types

        temperature = temperature if temperature is not None else self._temperature
        max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        model = kwargs.get("model", self._model)

        if not 0.0 <= temperature <= 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0, got {temperature}")

        # Build Gemini `contents` list. Gemini uses "user" and "model" roles
        # (not "assistant"). System instruction is passed separately via the
        # config object below — do NOT include it here.
        contents = []
        for msg in messages:
            if not msg.content or not msg.content.strip():
                # Match Groq behaviour: skip empty turns rather than risk an
                # API error or a blank model turn.
                logger.warning(
                    "[GEMINI] Skipping empty %s message in conversation history",
                    msg.role.value,
                )
                continue
            role = "model" if msg.role == MessageRole.ASSISTANT else "user"
            contents.append(
                genai_types.Content(
                    role=role,
                    parts=[genai_types.Part(text=msg.content)],
                )
            )

        # If we ended up with no contents (cold start, all empties), Gemini
        # rejects the request. Push a placeholder user turn so the system
        # prompt alone can drive the first response.
        if not contents:
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=" ")],
                )
            )

        stop_sequences = kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES)

        # Per-call thinking budget override, falling back to the provider default.
        thinking_budget = kwargs.get("thinking_budget", self._thinking_budget)

        gen_config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "stop_sequences": stop_sequences,
            "system_instruction": system_prompt if system_prompt else None,
        }
        thinking_config = self._build_thinking_config(model, thinking_budget)
        if thinking_config is not None:
            # 2.5 family -> thinking_budget; 3.x -> thinking_level="minimal".
            # Keeps reasoning off (or minimal) so the model doesn't stall a
            # real-time voice turn part-way through its reply.
            gen_config_kwargs["thinking_config"] = thinking_config

        gen_config = genai_types.GenerateContentConfig(**gen_config_kwargs)

        logger.debug(
            "Sending to Gemini: model=%s, temp=%s, max_tokens=%s, contents=%d",
            model, temperature, max_tokens, len(contents),
        )

        # Retry only before the first token arrives — once the caller has
        # received tokens, retrying would produce garbled / doubled output.
        import random as _rand
        last_err = None
        tokens_yielded = 0
        for _attempt in range(_LLM_MAX_RETRIES + 1):
            try:
                async with self._circuit:
                    stream = await self._client.aio.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=gen_config,
                    )

                    async for chunk in stream:
                        # chunk.text may be None for safety-flag chunks or
                        # response-metadata chunks that carry no content.
                        text = getattr(chunk, "text", None)
                        if text:
                            tokens_yielded += 1
                            yield text

                    logger.debug(
                        "Gemini stream completed, yielded %d chunks", tokens_yielded
                    )
                    if tokens_yielded == 0:
                        logger.warning("Zero text chunks received from Gemini")
                # Success — break out of retry loop.
                break

            except CircuitOpenError:
                raise

            except Exception as e:  # noqa: BLE001 — broad on purpose, mirrors Groq
                last_err = e
                if tokens_yielded > 0:
                    # Mid-stream failure: do not retry, would corrupt output.
                    logger.error(
                        "Gemini stream error after %d tokens yielded — "
                        "cannot retry mid-stream: %s",
                        tokens_yielded, e,
                    )
                    raise RuntimeError(f"Gemini LLM streaming failed: {e}")

                if _attempt < _LLM_MAX_RETRIES:
                    delay = min(
                        _LLM_RETRY_BASE_DELAY * (2 ** _attempt),
                        5.0,
                    ) * (0.5 + _rand.random())
                    logger.warning(
                        "Gemini retry %d/%d after %.2fs — %s",
                        _attempt + 1, _LLM_MAX_RETRIES, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("Gemini LLM streaming failed after retries: %s", e)
                    raise RuntimeError(f"Gemini LLM streaming failed: {e}")

    async def stream_chat_with_timeout(
        self,
        messages: List[Message],
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream with a hard wall-clock deadline and inter-token stall detection.

        Mirrors GroqLLMProvider.stream_chat_with_timeout so the voice pipeline
        can call either provider through the same interface. Logic is
        provider-agnostic — wraps `stream_chat` in per-token asyncio timeouts.

        Raises:
            LLMTimeoutError: no token arrived before the TTFT deadline.
        """
        _INTERTOKEN_TIMEOUT = 2.0

        t_start = asyncio.get_event_loop().time()
        tokens_received = 0
        gen = self.stream_chat(messages, **kwargs)
        try:
            while True:
                remaining = timeout_seconds - (asyncio.get_event_loop().time() - t_start)
                if remaining <= 0:
                    if tokens_received > 0:
                        logger.warning(
                            "Gemini wall-clock expired mid-stream "
                            "(limit=%.1fs, tokens=%d) — treating as stream end",
                            timeout_seconds, tokens_received,
                        )
                        break
                    logger.error(
                        "Gemini deadline exceeded before first token (limit=%.1fs)",
                        timeout_seconds,
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
                token_timeout = (
                    remaining if tokens_received == 0
                    else min(remaining, _INTERTOKEN_TIMEOUT)
                )
                try:
                    token = await asyncio.wait_for(gen.__anext__(), timeout=token_timeout)
                    if tokens_received == 0:
                        ttft_ms = (asyncio.get_event_loop().time() - t_start) * 1000
                        if ttft_ms > 800:
                            logger.warning(
                                "High Gemini TTFT: %.0fms — may indicate cold start "
                                "or rate limiting.", ttft_ms,
                            )
                    tokens_received += 1
                    yield token
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - t_start
                    if tokens_received > 0:
                        logger.warning(
                            "Gemini inter-token stall after %.2fs (tokens=%d) — "
                            "treating as stream end",
                            elapsed, tokens_received,
                        )
                        break
                    logger.error(
                        "Gemini timeout waiting for first token after %.2fs (limit=%.1fs)",
                        elapsed, timeout_seconds,
                    )
                    raise LLMTimeoutError(
                        f"LLM response timed out after {timeout_seconds}s"
                    )
        finally:
            # Ensure the underlying stream generator is closed so the HTTP
            # connection is released even if the caller stops iterating early.
            aclose = getattr(gen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supports_streaming(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"GeminiLLMProvider(model={self._model}, temp={self._temperature})"
