"""Resilient LLM wrapper — startup failover (Phase 4.1) + first-token deadline.

Wraps a primary LLM provider (Groq) with circuit-breaker-gated failover to a
secondary (any other ``LLMProvider`` — a faster Groq model, or a different
vendor for true isolation).

Two protections, one wrapper:

1. **Startup / handshake failover** (original Phase 4.1) — if the primary
   raises *before* the first token (auth dead, circuit open, connection
   refused) we cleanly retry the same turn on the secondary. After tokens are
   flowing we never swap: interleaving two LLMs mid-sentence would garble the
   reply.

2. **Time-to-first-token deadline** (the voice tail-case fix) — the voice path
   calls ``stream_chat_with_timeout``, whose budget doubles as BOTH the TTFT
   deadline AND the wall clock, so it can't be shortened without truncating
   long replies. A *stalled* first token (socket open, no bytes) therefore
   left the caller in dead air for the full 10s before the fallback line.
   The wrapper races ONLY the first ``__anext__()`` against a tight deadline
   (~2.5s); on a miss it aborts the primary and the secondary takes the turn.
   The rest of the stream is handed back to the provider untouched, so its own
   inter-token stall guard still applies.

Mirrors the STT/TTS T1.3 failover shape. Opt-in and fail-soft: with no
secondary the orchestrator never builds this wrapper, so behaviour is exactly
today's. See ``voice_orchestrator._create_llm_provider`` for the env-gated
wiring (``LLM_FAILOVER_ENABLED``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from app.domain.interfaces.llm_provider import LLMProvider
from app.domain.models.conversation import Message
from app.infrastructure.llm.groq import DEFAULT_LLM_TIMEOUT, LLMTimeoutError
from app.utils.resilience import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Observability is best-effort: a metrics import must never break a call.
try:
    from app.infrastructure.metrics.voice_metrics import record_llm_failover
except Exception:  # noqa: BLE001 — metrics optional in some contexts
    def record_llm_failover(outcome: str) -> None:  # type: ignore[misc]
        pass


@dataclass
class LLMFailoverPolicy:
    """Knobs for the failover lifecycle. Defaults tuned for voice: the
    first-token deadline sits well above a healthy warm TTFT (~150-800ms)
    but far below the 10s wall clock that drops calls."""

    # Original Phase 4.1 breaker knobs.
    failure_threshold: int = 3
    recovery_timeout_seconds: float = 30.0
    # First-token race. None of the original call sites pass this, so the
    # default keeps their behaviour; the voice path sets it from env.
    first_token_deadline_seconds: float = 2.5
    # One good primary turn re-closes a HALF_OPEN breaker (fast recovery).
    success_threshold: int = 1


class _FirstTokenMiss(Exception):
    """Internal signal: a provider failed to deliver a first token within the
    deadline (or errored / was breaker-blocked before one). Recoverable —
    try the next provider."""

    def __init__(self, reason: str, *, circuit_open: bool = False):
        super().__init__(reason)
        self.circuit_open = circuit_open


async def _safe_aclose(gen) -> None:
    """Close an async generator without letting cleanup errors surface."""
    try:
        await gen.aclose()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


class ResilientLLMProvider(LLMProvider):
    """Primary + secondary LLM with circuit-breaker-gated failover and a
    first-token deadline on the voice streaming path."""

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
            success_threshold=self._policy.success_threshold,
            # No excluded_exceptions (audit #7): the PRIMARY runs under this
            # breaker, so a primary first-token timeout (LLMTimeoutError /
            # asyncio.TimeoutError) MUST count — otherwise the breaker never
            # opens and EVERY turn keeps paying the full first-token deadline
            # before failing over (the exact outage the breaker exists to spare
            # callers). The SECONDARY runs with use_breaker=False, so its own
            # timeouts can never reach this breaker. Control-flow exceptions
            # (CancelledError / GeneratorExit) remain excluded inside
            # CircuitBreaker itself (_CONTROL_FLOW_EXC).
        )

    @property
    def name(self) -> str:
        return f"resilient({self._primary.name})"

    @property
    def supports_streaming(self) -> bool:
        return self._primary.supports_streaming

    def __getattr__(self, item):
        """Delegate any un-wrapped attribute (set_deterministic_mode,
        is_deterministic, model introspection, …) to the primary so existing
        call sites see a transparent provider. Guarded against recursion before
        ``_primary`` is assigned."""
        primary = self.__dict__.get("_primary")
        if primary is None:
            raise AttributeError(item)
        return getattr(primary, item)

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

    async def warm_up(self) -> None:
        """Warm BOTH providers so a failover doesn't pay a cold connect on top
        of the stall it's recovering from. Best-effort."""
        for provider in (self._primary, self._secondary):
            if provider is None:
                continue
            warm = getattr(provider, "warm_up", None)
            if warm is None:
                continue
            try:
                await warm()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "resilient_llm_warmup_failed provider=%s err=%s",
                    getattr(provider, "name", "?"), exc,
                )

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

    # ──────────────────────────────────────────────────────────────────
    # Voice path — first-token deadline + failover
    # ──────────────────────────────────────────────────────────────────

    async def stream_chat_with_timeout(
        self,
        messages: List[Message],
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream a turn, racing the first token against the deadline. On a
        first-token miss (or pre-first-token error / open breaker) abort the
        primary and stream from the secondary. Once a token is committed the
        primary owns the rest of the stream (no mid-utterance swap)."""
        # Fail-soft: no secondary → today's behaviour exactly (no tight deadline,
        # so a slow-but-fine first token is never cut to a fallback line).
        if self._secondary is None:
            async for tok in self._primary.stream_chat_with_timeout(
                messages, timeout_seconds=timeout_seconds, **kwargs
            ):
                yield tok
            return

        # Primary under the breaker — which raises CircuitOpenError instantly
        # when it's been failing, so a degraded primary costs no deadline tax.
        try:
            async for tok in self._attempt(
                self._primary, messages,
                timeout_seconds=timeout_seconds, use_breaker=True, **kwargs,
            ):
                yield tok
            return
        except _FirstTokenMiss as miss:
            outcome = "primary_circuit_open" if miss.circuit_open else "primary_missed"
            record_llm_failover(outcome)
            logger.warning(
                "llm_failover outcome=%s reason=%s primary=%s → secondary=%s",
                outcome, miss, self._primary.name, self._secondary.name,
            )

        # Secondary is the last resort — no breaker, no further failover.
        try:
            async for tok in self._attempt(
                self._secondary, messages,
                timeout_seconds=timeout_seconds, use_breaker=False, **kwargs,
            ):
                yield tok
        except _FirstTokenMiss as miss:
            record_llm_failover("secondary_missed")
            logger.error(
                "llm_failover_exhausted secondary=%s reason=%s — speaking fallback",
                self._secondary.name, miss,
            )
            # The exact exception the turn streamer catches to speak its
            # fallback line. Dead-air is capped at the deadline, not 10s.
            raise LLMTimeoutError(
                "both LLM providers missed the first-token deadline"
            ) from miss

    async def _attempt(
        self,
        provider: LLMProvider,
        messages: List[Message],
        *,
        timeout_seconds: float,
        use_breaker: bool,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Drive one provider. Raise ``_FirstTokenMiss`` if it can't produce a
        first token within the deadline (or errors / is breaker-blocked before
        one). After the first token is yielded, errors propagate normally —
        we're committed and cannot fail over without double-speaking."""
        gen = provider.stream_chat_with_timeout(
            messages, timeout_seconds=timeout_seconds, **kwargs
        )
        try:
            try:
                if use_breaker:
                    async with self._breaker:
                        token, has_token = await self._first_token(gen)
                else:
                    token, has_token = await self._first_token(gen)
            except CircuitOpenError as exc:
                raise _FirstTokenMiss("primary circuit open", circuit_open=True) from exc
            except (asyncio.CancelledError, GeneratorExit):
                raise  # control flow — never a failover trigger
            except Exception as exc:  # noqa: BLE001 — any pre-first-token error
                raise _FirstTokenMiss(repr(exc)) from exc

            if not has_token:
                return  # clean zero-token completion — nothing to say, no failover

            # Committed: this provider owns the turn from here.
            yield token
            async for tok in gen:
                yield tok
        finally:
            await _safe_aclose(gen)

    async def _first_token(self, gen) -> "tuple[str, bool]":
        """Wait for the first token under the deadline. Returns
        ``(token, True)`` on a real first token, or ``("", False)`` on a clean
        zero-token completion (which must NOT count as a breaker failure).
        Raises ``asyncio.TimeoutError`` on deadline, or the provider's error."""
        try:
            token = await asyncio.wait_for(
                gen.__anext__(), timeout=self._policy.first_token_deadline_seconds
            )
            return token, True
        except StopAsyncIteration:
            return "", False

    # ──────────────────────────────────────────────────────────────────
    # Tool turns stay on the primary (see module docstring).
    # ──────────────────────────────────────────────────────────────────

    async def stream_chat_with_tools(self, *args, **kwargs) -> AsyncIterator[str]:
        async for tok in self._primary.stream_chat_with_tools(*args, **kwargs):
            yield tok

    # ──────────────────────────────────────────────────────────────────
    # Startup / handshake failover (original Phase 4.1 — preserved).
    # ──────────────────────────────────────────────────────────────────

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
