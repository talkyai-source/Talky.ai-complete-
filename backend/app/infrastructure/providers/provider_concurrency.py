"""
Per-provider concurrency guards.

Each upstream provider has a hard concurrency ceiling — Groq RPM bucket,
ElevenLabs `concurrent_requests` plan limit, Deepgram per-account streaming
WS cap, Cartesia plan cap. Without an in-process ceiling, a burst of N
inbound calls will all hit the upstream simultaneously and start losing on
429s before the upstream-side rate limiter kicks in.

`ProviderConcurrencyGuard` wraps an `asyncio.Semaphore` sized to ≤ 85% of
the contracted account cap, and exposes:

  - `async with guard.acquire(): ...` — the FIRST line of every provider
    call. If the cap is full, the new call waits (bounded by `wait_timeout`)
    rather than fanning out to the upstream and 429-ing.
  - `guard.snapshot()` — Prometheus-friendly readout.

The registry pattern (`get_provider_guard("groq")`) gives every part of the
codebase one place to look — singleton per provider, per process.

Sized via env vars:

  GROQ_MAX_CONCURRENT, ELEVENLABS_MAX_CONCURRENT,
  DEEPGRAM_MAX_CONCURRENT, CARTESIA_MAX_CONCURRENT,
  GOOGLE_TTS_MAX_CONCURRENT.

Defaults are conservative (8-200) — increase when account caps are uplifted.

ElevenLabs specifically: self-service Flash plans cap `concurrent_requests`
at roughly 4-30 depending on tier (see https://elevenlabs.io/pricing), far
below what a generic default here should assume. The guard's job is to make
callers WAIT (visibly, via `snapshot()`/wait-timeout logging) instead of
fanning out past the account cap and eating invisible 429s. Two independent
ceilings exist and BOTH must stay above the guard for it to mean anything:
  1. the ElevenLabs account's `concurrent_requests` limit (contractual), and
  2. aiohttp's `TCPConnector(limit_per_host=...)` in elevenlabs_tts.py
     (currently 50) — a per-process TCP-connection cap that queues requests
     silently inside aiohttp with NO guard-visible wait if the guard number
     is set higher than this.
Keep ELEVENLABS_MAX_CONCURRENT <= account cap AND <= limit_per_host so the
guard is the first (and only visible) place callers queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, int] = {
    "groq": 80,
    # Self-service ElevenLabs Flash plans cap concurrent_requests at ~4-30
    # (plan-dependent); 200 silently overshot every self-service tier, so
    # requests queued invisibly inside the aiohttp connector
    # (limit_per_host=50, see elevenlabs_tts.py) instead of at this guard,
    # where wait time/timeouts are logged. 8 is a safe floor for the
    # smallest paid tiers — raise via ELEVENLABS_MAX_CONCURRENT to match
    # your actual account cap, but never above limit_per_host (50).
    "elevenlabs": 8,
    "deepgram": 80,
    "cartesia": 80,
    "google_tts": 200,
}


class ProviderGuardTimeout(RuntimeError):
    """Raised when waiting for a slot exceeds `wait_timeout`."""


class ProviderConcurrencyGuard:
    """Bounded async semaphore + observability for one upstream provider."""

    def __init__(
        self,
        provider_name: str,
        max_concurrent: int,
        *,
        wait_timeout: float = 5.0,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")
        self.provider_name = provider_name
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._in_flight = 0
        self._waiting = 0
        self._total_waits = 0
        self._total_wait_seconds = 0.0
        self._total_timeouts = 0
        self._wait_timeout = wait_timeout
        self._lock = asyncio.Lock()

    @property
    def max_concurrent(self) -> int:
        return self._max

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def acquire(self) -> "_GuardSlot":
        return _GuardSlot(self)

    def snapshot(self) -> dict:
        return {
            "provider": self.provider_name,
            "max": self._max,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "saturation_pct": round(self._in_flight / max(self._max, 1) * 100, 1),
            "total_waits": self._total_waits,
            "total_wait_seconds": round(self._total_wait_seconds, 3),
            "total_timeouts": self._total_timeouts,
        }


class _GuardSlot:
    """Async context manager for one provider call slot."""

    def __init__(self, guard: ProviderConcurrencyGuard) -> None:
        self._guard = guard
        self._t0: float = 0.0

    async def __aenter__(self) -> "_GuardSlot":
        guard = self._guard
        async with guard._lock:
            guard._waiting += 1
        try:
            self._t0 = time.monotonic()
            try:
                await asyncio.wait_for(
                    guard._sem.acquire(), timeout=guard._wait_timeout
                )
            except asyncio.TimeoutError as exc:
                async with guard._lock:
                    guard._total_timeouts += 1
                logger.warning(
                    "provider_guard_timeout provider=%s waited_s=%.2f cap=%d",
                    guard.provider_name, guard._wait_timeout, guard._max,
                )
                raise ProviderGuardTimeout(
                    f"{guard.provider_name} concurrency guard exhausted "
                    f"({guard._max} in flight) — waited {guard._wait_timeout}s"
                ) from exc
        finally:
            async with guard._lock:
                guard._waiting -= 1

        wait_s = time.monotonic() - self._t0
        async with guard._lock:
            guard._in_flight += 1
            guard._total_waits += 1 if wait_s > 0.001 else 0
            guard._total_wait_seconds += wait_s
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        guard = self._guard
        async with guard._lock:
            guard._in_flight = max(0, guard._in_flight - 1)
        guard._sem.release()


# ----- registry -----

_GUARDS: dict[str, ProviderConcurrencyGuard] = {}
_GUARD_LOCK: Optional[asyncio.Lock] = None


def _read_env_cap(provider_name: str) -> int:
    raw = os.getenv(f"{provider_name.upper()}_MAX_CONCURRENT")
    if raw and raw.strip().isdigit():
        return int(raw)
    return _DEFAULTS.get(provider_name, 80)


def get_provider_guard(provider_name: str) -> ProviderConcurrencyGuard:
    """Singleton accessor. Safe to call from sync code at import time."""
    name = provider_name.lower()
    guard = _GUARDS.get(name)
    if guard is None:
        cap = _read_env_cap(name)
        guard = ProviderConcurrencyGuard(name, cap)
        _GUARDS[name] = guard
        logger.info("provider_guard_init provider=%s max_concurrent=%d", name, cap)
    return guard


def all_guards() -> list[ProviderConcurrencyGuard]:
    """For observability endpoints — list every guard registered so far."""
    return list(_GUARDS.values())


def reset_guards_for_tests() -> None:
    """Test utility: drop all guards so each test starts clean."""
    _GUARDS.clear()
