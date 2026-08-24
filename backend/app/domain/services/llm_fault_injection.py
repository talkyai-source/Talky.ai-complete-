"""Deliberately fail the primary LLM on one campaign, so the Groq fallback can
be proven on a real call instead of only in unit tests.

WHY THIS EXISTS
---------------
On 2026-08-24 the MVP model pair became Cerebras ``gpt-oss-120b`` (primary) and
Groq ``openai/gpt-oss-20b`` (fallback), chosen on measured latency and stability
(``docs/MODEL-SELECTION.md``). ``ResilientLLMProvider`` is supposed to swap to
the secondary when the primary misses the first-token deadline or errors before
committing a token.

That path has unit tests, and unit tests exercise the CLASS. They say nothing
about the wiring on a production call: whether the secondary really initialises
with the tenant's credential, whether Groq really accepts the request our
Cerebras-shaped code built (it rejects ``prompt_cache_key`` outright, for
instance), whether the caller hears a coherent sentence rather than a stutter,
and how much silence the swap actually costs. The only honest way to answer
those is to make it happen on a real call.

Waiting for Cerebras to fail on its own is not a test.

WHY IT IS SHAPED LIKE THIS
--------------------------
This mirrors ``stt_fault_injection`` deliberately — same four properties, same
reasoning. Fault injection that reaches production is only acceptable if it
cannot go off by accident, cannot be left on, and cannot break a call that has
nothing to fall back to.

* **Off unless named.** The switch is a campaign UUID, not a boolean. There is
  no value of ``true`` that turns this on for everything.
* **It expires.** ``VOICE_LLM_FAULT_UNTIL`` is mandatory. A forgotten env var
  stops mattering on its own.
* **It refuses to run without a safety net.** If ``LLM_FAILOVER_ENABLED`` is
  off there is no secondary, so failing the primary would just kill the turn and
  make the agent speak its fallback line — proving nothing. It declines and says
  why.
* **It is loud.** Every activation logs at ERROR with the campaign and call id.
* **One turn, not the whole call.** ``VOICE_LLM_FAULT_TURNS`` (default 1) caps
  how many turns are sabotaged, so the call continues normally afterwards and
  you can hear both the failover AND the recovery on one recording.

WHAT IT SIMULATES
-----------------
A provider that accepts the request and then errors BEFORE emitting its first
token — the realistic shape of a Cerebras outage or a rate-limit rejection, and
precisely the condition ``ResilientLLMProvider`` treats as a first-token miss.

It does NOT simulate a mid-utterance failure. That is deliberate: the wrapper
intentionally does not swap once a token is committed (no mid-sentence provider
change), so simulating it would only prove the code does what it already says
it will not do.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional

logger = logging.getLogger(__name__)

_CAMPAIGN_ENV = "VOICE_LLM_FAULT_CAMPAIGN"
_UNTIL_ENV = "VOICE_LLM_FAULT_UNTIL"
_TURNS_ENV = "VOICE_LLM_FAULT_TURNS"


class InjectedLLMFailure(RuntimeError):
    """Raised in place of the primary's first token. Deliberately a plain
    RuntimeError subclass so ResilientLLMProvider classifies it exactly as it
    would a genuine provider error — no special-casing anywhere in the
    failover path, which is the point of the exercise."""


def _armed_campaign() -> Optional[str]:
    raw = (os.getenv(_CAMPAIGN_ENV, "") or "").strip()
    return raw or None


def _window_open(campaign: str) -> bool:
    """The expiry is mandatory and fail-closed: unparseable or absent means OFF."""
    raw = (os.getenv(_UNTIL_ENV, "") or "").strip()
    if not raw:
        logger.error(
            "llm_fault_injection_refused campaign=%s reason=no_expiry — %s is "
            "mandatory so this cannot be left running indefinitely",
            campaign, _UNTIL_ENV,
        )
        return False
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        logger.error(
            "llm_fault_injection_refused campaign=%s reason=bad_expiry raw=%r — "
            "expected ISO-8601, e.g. 2026-08-25T14:30:00Z",
            campaign, raw,
        )
        return False
    if datetime.now(timezone.utc) >= until:
        logger.info(
            "llm_fault_injection_expired campaign=%s until=%s — window closed, "
            "the primary runs normally",
            campaign, until.isoformat(),
        )
        return False
    return True


def _max_turns() -> int:
    try:
        return max(1, int(os.getenv(_TURNS_ENV, "1")))
    except (TypeError, ValueError):
        return 1


def maybe_break_primary(primary, campaign_id, call_id, *, failover_enabled: bool):
    """Return ``primary``, or a wrapper that fails its first N turns.

    Called where the resilient wrapper is assembled, so the sabotage sits INSIDE
    the thing under test rather than beside it.
    """
    armed = _armed_campaign()
    if not armed:
        return primary
    if str(campaign_id or "") != armed:
        return primary
    if not failover_enabled:
        logger.error(
            "llm_fault_injection_refused campaign=%s reason=no_failover — there "
            "is no secondary, so failing the primary would kill the turn rather "
            "than test anything",
            armed,
        )
        return primary
    if not _window_open(armed):
        return primary

    turns = _max_turns()
    logger.error(
        "llm_fault_injection_armed campaign=%s call=%s turns=%d primary=%s — "
        "this call's PRIMARY LLM will error before its first token; the Groq "
        "secondary is expected to answer instead",
        armed, str(call_id)[:12], turns, getattr(primary, "name", "?"),
    )
    return _BrokenPrimary(primary, turns, armed, call_id)


class _BrokenPrimary:
    """Delegates everything, except it raises before the first token for the
    first ``turns`` turns. Attribute delegation rather than inheritance so it
    works whatever concrete provider is underneath."""

    def __init__(self, inner, turns: int, campaign: str, call_id) -> None:
        self._inner = inner
        self._remaining = turns
        self._campaign = campaign
        self._call_id = str(call_id)[:12]

    def __getattr__(self, item):
        return getattr(self._inner, item)

    @property
    def name(self) -> str:
        return getattr(self._inner, "name", "unknown")

    async def stream_chat_with_timeout(self, *args, **kwargs) -> AsyncIterator[str]:
        async for tok in self._maybe_fail(
            self._inner.stream_chat_with_timeout, *args, **kwargs
        ):
            yield tok

    async def stream_chat(self, *args, **kwargs) -> AsyncIterator[str]:
        async for tok in self._maybe_fail(self._inner.stream_chat, *args, **kwargs):
            yield tok

    async def _maybe_fail(self, fn, *args, **kwargs) -> AsyncIterator[str]:
        if self._remaining > 0:
            self._remaining -= 1
            logger.error(
                "llm_fault_injection_firing campaign=%s call=%s remaining=%d — "
                "raising instead of the primary's first token",
                self._campaign, self._call_id, self._remaining,
            )
            raise InjectedLLMFailure(
                "injected primary LLM failure (VOICE_LLM_FAULT_CAMPAIGN)"
            )
        async for tok in fn(*args, **kwargs):
            yield tok
