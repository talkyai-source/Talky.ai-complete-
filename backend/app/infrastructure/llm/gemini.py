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
from contextlib import aclosing
import asyncio
import logging
from typing import AsyncIterator, List, Optional

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message, MessageRole
from app.utils.resilience import CircuitBreaker, CircuitOpenError
from app.infrastructure.llm.request_profile import record_traditional_request

from app.infrastructure.llm.streaming import (
    LLMStreamStalled, stream_with_timeout, close_stream, execute_tool_call,
)

logger = logging.getLogger(__name__)

# Default timeout for LLM responses (seconds). Mirrors GroqLLMProvider so
# operators only have to learn one number.
DEFAULT_LLM_TIMEOUT = 10.0

# Retry configuration for transient Gemini failures
_LLM_MAX_RETRIES = 2
_LLM_RETRY_BASE_DELAY = 0.3  # 300ms — fast first retry for voice latency budget

# Gemini counts thinking tokens against the SINGLE max_output_tokens ceiling, so
# thinking and the visible answer compete for the same budget. On models whose
# thinking can't be fully disabled (3.x "minimal" floor) a tight cap gets eaten
# by thinking and the model yields zero text → a silent agent. So we reserve
# room for thinking ON TOP of the caller's answer budget: max_output_tokens =
# thinking_reserve + ai_config.max_tokens. This is shared headroom, not a
# separate visible-token cap or a guarantee against truncation. The reserve
# is env-tunable for operators.
_THINKING_RESERVE_TOKENS = int(os.getenv("GEMINI_THINKING_RESERVE_TOKENS", "1024"))

# Imported for the base class below, aliased so the name `LLMTimeoutError`
# still refers to Gemini's own subclass throughout this module.
from app.infrastructure.llm.streaming import (  # noqa: E402
    LLMTimeoutError as _GroqLLMTimeoutError,
)


class LLMTimeoutError(_GroqLLMTimeoutError):
    """Raised when a Gemini response times out.

    SUBCLASSES ``groq.LLMTimeoutError`` deliberately (2026-08-06). It used to
    be an independent ``Exception``, and the exception TYPE is load-bearing
    here: the voice pipeline catches ``app.infrastructure.llm.groq.
    LLMTimeoutError`` *specifically* — at ``turn_streamer.py:739``,
    ``llm_response.py:114`` and in ``resilient_llm`` — so a Gemini timeout
    raised as an unrelated class sailed past every one of those handlers and
    landed in ``turn_ender``'s generic ``except Exception``, which plays the
    hard apology line ("I'm sorry, I'm having trouble right now") instead of
    the graceful degradation those handlers implement.

    ``cerebras.py`` already documents this trap and sidesteps it by importing
    Groq's class outright. Gemini keeps its own NAME (so any existing
    ``except gemini.LLMTimeoutError`` still works) but is now a subclass, which
    makes the fix purely additive.
    """
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
        """Release both SDK transports, including when async closure fails."""
        client, self._client = self._client, None
        if client is None:
            return
        async_close = getattr(getattr(client, "aio", None), "aclose", None)
        sync_close = getattr(client, "close", None)
        try:
            if callable(async_close):
                await async_close()
        finally:
            if callable(sync_close):
                sync_close()

    @staticmethod
    def _is_gemini_3(model: str) -> bool:
        """True for the Gemini 3.x family, whose thinking CANNOT be fully
        disabled (it uses ``thinking_level`` with a ``minimal`` floor, and
        ignores ``thinking_budget``)."""
        m = (model or "").lower()
        return m.startswith("gemini-3") or m in {
            "gemini-flash-latest", "gemini-pro-latest",
        }

    @staticmethod
    def _rejects_minimal_thinking(model: str) -> bool:
        """Gemini 3.8 answers 400 "Thinking level MINIMAL is not supported for
        this model" (browser test cb1b28c3, 2026-09-30: every turn failed over
        to Groq). Its lowest level is "low" -- verified from the prod host."""
        return (model or "").lower().startswith("gemini-3.8")

    @classmethod
    def _build_thinking_config(cls, model: str, thinking_budget: Optional[int]):
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

        if cls._is_gemini_3(model):
            # thinking_budget is ignored on 3.x — map intent to thinking_level.
            # 0 / unset -> "minimal" (lowest latency, what voice wants).
            level = "low" if (thinking_budget or 0) > 0 else "minimal"
            if level == "minimal" and cls._rejects_minimal_thinking(model):
                level = "low"
            try:
                return genai_types.ThinkingConfig(thinking_level=level)
            except Exception:  # noqa: BLE001 — SDK predates thinking_level
                return None
        # Gemini 2.5 (and anything else that takes a budget): honour it if given.
        if thinking_budget is None:
            return None
        return genai_types.ThinkingConfig(thinking_budget=int(thinking_budget))

    @classmethod
    def _effective_max_output_tokens(
        cls, model: str, thinking_budget: Optional[int], max_tokens: int
    ) -> int:
        """Shared max_output_tokens ceiling with headroom for thinking.

        Gemini's single ceiling is shared by thinking + answer, so we send
        ``thinking_reserve + max_tokens`` whenever thinking is on. This does
        not reserve a guaranteed visible-token allowance:
          - thinking OFF (2.5, budget 0)          -> no reserve, answer gets it all
          - thinking BUDGETED (2.5, budget N>0)   -> reserve exactly N
          - thinking FLOORED (3.x 'minimal')      -> reserve a fixed pad
          - thinking DYNAMIC (2.5, budget None)   -> reserve the fixed pad (can't bound)
        """
        if cls._is_gemini_3(model):
            return max_tokens + _THINKING_RESERVE_TOKENS
        # Gemini 2.5 family.
        if thinking_budget is None:
            return max_tokens + _THINKING_RESERVE_TOKENS
        if thinking_budget <= 0:
            return max_tokens
        return max_tokens + int(thinking_budget)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_reasoning_kwargs(kwargs) -> None:
        if kwargs.get("reasoning_effort") is not None:
            raise ValueError(
                "Gemini does not support reasoning_effort; use its thinking_budget control"
            )

    @staticmethod
    def _completed_chunk(chunk) -> bool:
        """Validate the decision before exposing its text or function calls."""
        candidate = (getattr(chunk, "candidates", None) or [None])[0]
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            return False
        reason = getattr(reason, "value", reason)
        if reason != "STOP":
            raise LLMStreamStalled(f"Gemini response incomplete: {reason}")
        return True

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

        self._validate_reasoning_kwargs(kwargs)
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
            # Coalesce consecutive same-role turns into one Content. Gemini expects
            # alternating user/model; a silence-check (or phantom recovery) can
            # produce back-to-back assistant turns that otherwise degrade adherence
            # or trip stricter contents-ordering checks (re-audit flow #4).
            if contents and contents[-1].role == role:
                contents[-1] = genai_types.Content(
                    role=role,
                    parts=list(contents[-1].parts) + [genai_types.Part(text=msg.content)],
                )
            else:
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

        # Add shared thinking headroom to the reply target. Terminal-status
        # checks still reject empty or partial responses cut by this ceiling.
        eff_max_output_tokens = self._effective_max_output_tokens(
            model, thinking_budget, max_tokens
        )

        gen_config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": eff_max_output_tokens,
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
        tokens_yielded = 0
        for _attempt in range(_LLM_MAX_RETRIES + 1):
            try:
                async with self._circuit:
                    record_traditional_request(
                        provider=self.name,
                        request={"model": model, "contents": contents, "config": gen_config},
                        instructions=system_prompt, configured_temperature=temperature,
                        configured_max_tokens=max_tokens,
                    )
                    stream = await self._client.aio.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=gen_config,
                    )
                    terminal_seen = False
                    try:
                        async for chunk in stream:
                            terminal_seen = self._completed_chunk(chunk) or terminal_seen
                            # chunk.text may be None for safety-flag chunks or
                            # response-metadata chunks that carry no content.
                            text = getattr(chunk, "text", None)
                            if text:
                                tokens_yielded += 1
                                yield text
                        if not terminal_seen:
                            raise LLMStreamStalled(
                                "Gemini stream ended without a terminal reason"
                            )
                    finally:
                        await close_stream(stream)
                    logger.debug(
                        "Gemini stream completed, yielded %d chunks", tokens_yielded
                    )
                    if tokens_yielded == 0:
                        logger.warning("Zero text chunks received from Gemini")
                # Success — break out of retry loop.
                break

            except CircuitOpenError:
                raise
            except LLMStreamStalled:
                raise
            except Exception as e:  # noqa: BLE001 — broad on purpose, mirrors Groq
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

    async def stream_chat_with_tools(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[List[dict]] = None,
        tool_runner=None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        require_tool_result_before_content: bool = False,
        max_tool_rounds: int = 1,
        read_only_tools=(),
        navigation_round_allowed=None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream a turn that MAY call a function tool, using Gemini function
        calling. Mirrors GroqLLMProvider.stream_chat_with_tools so the voice
        pipeline drives on-demand KB on either provider through the same
        str-yield contract.

        The default remains one tool decision then a tool-less answer. Live
        section navigation can opt into three ordinary decisions plus at most
        four verified advancing catalog decisions. Native model
        parts (including thought signatures) are preserved in every continuation.
        Repeated writes share a receipt; read-only tools rerun to refresh evidence.
        """
        from app.infrastructure.llm.streaming import ToolRoundBudget
        budget = ToolRoundBudget(max_tool_rounds, navigation_round_allowed)
        if not tools or tool_runner is None:
            async with aclosing(self.stream_chat_with_timeout(
                messages, timeout_seconds=timeout_seconds, system_prompt=system_prompt,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            )) as stream:
                async for tok in stream:
                    yield tok
            return

        if not self._client:
            raise RuntimeError("Gemini client not initialized. Call initialize() first.")
        self._validate_reasoning_kwargs(kwargs)
        from google.genai import types as genai_types

        temperature = temperature if temperature is not None else self._temperature
        max_tokens = max_tokens if max_tokens is not None else self._max_tokens
        model = kwargs.get("model", self._model)

        # Gemini contents (user/model roles; system passed via config, not here).
        contents: list = []
        for msg in messages:
            if not msg.content or not msg.content.strip():
                continue
            role = "model" if msg.role == MessageRole.ASSISTANT else "user"
            contents.append(genai_types.Content(
                role=role, parts=[genai_types.Part(text=msg.content)]))
        if not contents:
            contents.append(genai_types.Content(
                role="user", parts=[genai_types.Part(text=" ")]))

        # OpenAI-style tool spec -> Gemini Tool(function_declarations). The raw
        # JSON schema goes through parameters_json_schema (no Schema conversion).
        fdecls = []
        for spec in tools:
            fn = spec.get("function", spec)
            fdecls.append(genai_types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters_json_schema=fn.get("parameters"),
            ))
        gemini_tools = [genai_types.Tool(function_declarations=fdecls)]

        _tb = kwargs.get("thinking_budget", self._thinking_budget)
        base_cfg = dict(
            temperature=temperature,
            max_output_tokens=self._effective_max_output_tokens(model, _tb, max_tokens),
            stop_sequences=kwargs.get("stop", self.DEFAULT_STOP_SEQUENCES),
            system_instruction=system_prompt or None,
        )
        tc = self._build_thinking_config(model, _tb)
        if tc is not None:
            base_cfg["thinking_config"] = tc

        def _chunk_text(chunk) -> Optional[str]:
            # Read text from parts directly (chunk.text can raise on mixed
            # text+function_call parts in some SDK versions).
            try:
                cand = (chunk.candidates or [None])[0]
                parts = getattr(getattr(cand, "content", None), "parts", None) or []
                txt = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
                return txt or None
            except Exception:
                return None

        async def _stream(cfg, fcalls_out, model_parts):
            async def chunks():
                record_traditional_request(
                    provider=self.name,
                    request={"model": model, "contents": contents, "config": cfg},
                    instructions=system_prompt, configured_temperature=temperature,
                    configured_max_tokens=max_tokens,
                )
                stream = await self._client.aio.models.generate_content_stream(
                    model=model, contents=contents, config=cfg)
                try:
                    async for chunk in stream:
                        yield chunk
                finally:
                    await close_stream(stream)

            terminal_seen = False
            async with self._circuit, aclosing(stream_with_timeout(chunks(), timeout_seconds, timeout_error=LLMTimeoutError)) as stream:
                async for chunk in stream:
                    terminal_seen = self._completed_chunk(chunk) or terminal_seen
                    candidate = (getattr(chunk, "candidates", None) or [None])[0]
                    parts = getattr(getattr(candidate, "content", None), "parts", None) or []
                    # Keep provider-returned parts intact (including thought signatures).
                    # Rebuilding Part(function_call=...) silently discards their metadata.
                    model_parts.extend(parts)
                    txt = _chunk_text(chunk)
                    if txt:
                        yield txt
                    for fc in (getattr(chunk, "function_calls", None) or []):
                        if fc and fc.name:
                            fcalls_out.append(fc)
                if not terminal_seen:
                    raise LLMStreamStalled(
                        "Gemini stream ended without a terminal reason"
                    )

        decision_cfg = genai_types.GenerateContentConfig(tools=gemini_tools, **base_cfg)
        tool_results = {}
        while budget.remaining:
            # A new list per response avoids mutating already-sent signed parts.
            model_parts, fcalls, decision_tokens = [], [], []
            async with aclosing(_stream(decision_cfg, fcalls, model_parts)) as stream:
                async for tok in stream:
                    if require_tool_result_before_content:
                        decision_tokens.append(tok)
                    else:
                        yield tok
            if not fcalls:
                if decision_tokens:
                    yield "".join(decision_tokens)
                return

            resp_parts = []
            round_results = []
            for fc in fcalls:
                if not any(getattr(p, "function_call", None) == fc for p in model_parts):
                    model_parts.append(genai_types.Part(function_call=fc))
                try:
                    args = dict(fc.args) if fc.args else {}
                except Exception:
                    args = {}
                try:
                    import json
                    key = (fc.name, json.dumps(args, sort_keys=True))
                    if key not in tool_results or fc.name in read_only_tools:
                        tool_results[key] = await execute_tool_call({"name": fc.name, "arguments": args}, tools, tool_runner)
                    result = tool_results[key]
                except Exception as exc:  # never let a tool failure stall the turn
                    logger.warning("gemini tool_runner failed name=%s: %s", fc.name, exc)
                    result = "No specific information found."
                resp_parts.append(genai_types.Part.from_function_response(
                    name=fc.name,
                    response={"result": result or "No specific information found."},
                ))
                round_results.append(({"name": fc.name, "arguments": args}, result))
            contents.append(genai_types.Content(role="model", parts=model_parts))
            contents.append(genai_types.Content(role="user", parts=resp_parts))
            budget.finish_round(round_results)

        round1_cfg = genai_types.GenerateContentConfig(**base_cfg)  # no tools
        if require_tool_result_before_content:
            grounded_tokens: list[str] = []
            async with aclosing(_stream(round1_cfg, [], [])) as stream:
                async for tok in stream:
                    grounded_tokens.append(tok)
            if grounded_tokens:
                yield "".join(grounded_tokens)
        else:
            async with aclosing(_stream(round1_cfg, [], [])) as stream:
                async for tok in stream:
                    yield tok

    async def stream_chat_with_timeout(self, messages, timeout_seconds=DEFAULT_LLM_TIMEOUT, **kwargs):
        """Shared provider-wait budget; playback never consumes this allowance."""
        async with aclosing(stream_with_timeout(
            self.stream_chat(messages, **kwargs), timeout_seconds, timeout_error=LLMTimeoutError
        )) as stream:
            async for token in stream:
                yield token

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def supports_streaming(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"GeminiLLMProvider(model={self._model}, temp={self._temperature})"
