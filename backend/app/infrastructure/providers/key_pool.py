"""
Generic API-key pool with health-aware routing.

A `KeyPool` holds N API keys for one upstream provider (Groq, ElevenLabs,
Deepgram, Cartesia, ...). On every acquire it picks the lowest-in-flight key
that is not in cooldown and whose error-rate EWMA is below threshold. On 429
or 5xx the caller reports failure; the key enters cooldown with exponential
backoff. On success, error-rate decays.

Semantics:

  pool.acquire() returns a context manager that:
    - selects a key
    - increments its in_flight counter
    - on __exit__ decrements; if `report_failure(retryable=True)` was called,
      the key is placed in cooldown.

The pool exposes Prometheus-friendly snapshots via `pool.stats()` so the
telephony observability endpoint can surface saturation per provider.

Design choices:

- We do NOT do client-side rate-limit budgeting (token-bucket per key). The
  upstream is the source of truth on quota. We react to 429s by cooling down
  the offending key and routing to a healthier sibling. This is simpler and
  correct as long as we run >=2 keys.
- The EWMA error-rate (alpha=0.3) makes a single 429 cool a key briefly
  rather than burn the whole budget on the next request to the same key.
- `KeyPoolExhaustedError` is raised when every key is in cooldown. The
  caller's circuit breaker then trips and fallback (resilient_tts) takes over.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, Optional

logger = logging.getLogger(__name__)


class KeyPoolExhaustedError(RuntimeError):
    """Every key in the pool is currently in cooldown."""


@dataclass
class KeyEntry:
    """One API key plus its live health state."""

    key: str
    weight: float = 1.0
    in_flight: int = 0
    error_rate: float = 0.0  # EWMA, range 0..1
    cooldown_until: float = 0.0  # monotonic seconds; 0 = no cooldown
    consecutive_failures: int = 0
    total_acquires: int = 0
    total_failures: int = 0

    def is_cool(self, now: float) -> bool:
        return self.cooldown_until <= now

    def redacted(self) -> str:
        """For logs — never log the key itself."""
        if not self.key:
            return "<empty>"
        if len(self.key) <= 8:
            return self.key[:2] + "***"
        return f"{self.key[:4]}…{self.key[-4:]}"


@dataclass
class _PoolConfig:
    cooldown_base_seconds: float = 1.0
    cooldown_max_seconds: float = 60.0
    error_rate_threshold: float = 0.5  # divert from a key whose EWMA > 0.5
    ewma_alpha: float = 0.3


class KeyPool:
    """Round-by-load key pool. One instance per upstream provider."""

    def __init__(
        self,
        provider_name: str,
        keys: Iterable[str],
        *,
        cooldown_base_seconds: float = 1.0,
        cooldown_max_seconds: float = 60.0,
    ) -> None:
        cleaned = [k.strip() for k in keys if k and k.strip()]
        if not cleaned:
            raise ValueError(
                f"KeyPool[{provider_name}]: at least one API key required"
            )
        # Preserve order; deduplicate while keeping the first occurrence.
        seen: set[str] = set()
        unique: list[str] = []
        for k in cleaned:
            if k not in seen:
                seen.add(k)
                unique.append(k)

        self.provider_name = provider_name
        self._entries: list[KeyEntry] = [KeyEntry(key=k) for k in unique]
        self._cfg = _PoolConfig(
            cooldown_base_seconds=cooldown_base_seconds,
            cooldown_max_seconds=cooldown_max_seconds,
        )
        self._lock = asyncio.Lock()

    # ---------- public API ----------

    @property
    def size(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator["_AcquiredKey"]:
        """
        Pick a healthy key, lease it for the duration of the context.

        Caller can mark the lease failed via `lease.report_failure(retryable=True)`.
        On context exit the in_flight counter is decremented and (if reported)
        the key enters cooldown.
        """
        entry = await self._select()
        async with self._lock:
            entry.in_flight += 1
            entry.total_acquires += 1
        lease = _AcquiredKey(pool=self, entry=entry)
        try:
            yield lease
        finally:
            async with self._lock:
                entry.in_flight = max(0, entry.in_flight - 1)
                if lease._failure_reported and lease._retryable:
                    self._apply_failure(entry)
                elif lease._success_reported:
                    self._apply_success(entry)

    async def report_external_failure(self, key: str, retryable: bool = True) -> None:
        """
        Mark a key as having failed *outside* of an `acquire()` block — used by
        callers who release the lease before the actual API call completes
        (e.g. streaming generators that fail mid-stream). Idempotent.
        """
        async with self._lock:
            for e in self._entries:
                if e.key == key and retryable:
                    self._apply_failure(e)
                    return

    def stats(self) -> list[dict]:
        """Snapshot for Prometheus/observability. Does not lock — read-only."""
        now = time.monotonic()
        return [
            {
                "provider": self.provider_name,
                "key": e.redacted(),
                "in_flight": e.in_flight,
                "error_rate": round(e.error_rate, 3),
                "cooling_down": not e.is_cool(now),
                "cooldown_remaining_s": max(0.0, e.cooldown_until - now),
                "total_acquires": e.total_acquires,
                "total_failures": e.total_failures,
            }
            for e in self._entries
        ]

    # ---------- internal ----------

    async def _select(self) -> KeyEntry:
        async with self._lock:
            now = time.monotonic()
            cool = [e for e in self._entries if e.is_cool(now)]
            if not cool:
                # Every key in cooldown — surface with the soonest recovery time
                # so the caller can decide retry/backoff.
                soonest = min(e.cooldown_until - now for e in self._entries)
                raise KeyPoolExhaustedError(
                    f"All {self.provider_name} keys in cooldown; "
                    f"next available in {soonest:.1f}s"
                )

            # Prefer keys whose EWMA is below threshold; if all are above,
            # pick the least-bad one (smallest error_rate).
            healthy = [
                e for e in cool if e.error_rate <= self._cfg.error_rate_threshold
            ]
            candidates = healthy or cool

            # Within candidates, lowest in_flight wins; ties broken by
            # weight (higher weight = more capacity → preferred), then by
            # smallest error rate.
            candidates.sort(
                key=lambda e: (e.in_flight, -e.weight, e.error_rate)
            )
            return candidates[0]

    def _apply_failure(self, entry: KeyEntry) -> None:
        entry.error_rate = (
            self._cfg.ewma_alpha * 1.0
            + (1.0 - self._cfg.ewma_alpha) * entry.error_rate
        )
        entry.consecutive_failures += 1
        entry.total_failures += 1
        # Exponential cooldown: 1s, 2s, 4s, 8s ... cap.
        cooldown = min(
            self._cfg.cooldown_base_seconds * (2 ** (entry.consecutive_failures - 1)),
            self._cfg.cooldown_max_seconds,
        )
        entry.cooldown_until = time.monotonic() + cooldown
        logger.warning(
            "key_pool_cooldown provider=%s key=%s cooldown_s=%.1f error_rate=%.2f",
            self.provider_name, entry.redacted(), cooldown, entry.error_rate,
        )

    def _apply_success(self, entry: KeyEntry) -> None:
        entry.error_rate = (
            self._cfg.ewma_alpha * 0.0
            + (1.0 - self._cfg.ewma_alpha) * entry.error_rate
        )
        entry.consecutive_failures = 0


class _AcquiredKey:
    """Lease object yielded by `KeyPool.acquire()`."""

    def __init__(self, pool: KeyPool, entry: KeyEntry) -> None:
        self._pool = pool
        self._entry = entry
        self._failure_reported = False
        self._success_reported = False
        self._retryable = False

    @property
    def key(self) -> str:
        return self._entry.key

    @property
    def redacted(self) -> str:
        return self._entry.redacted()

    def report_failure(self, retryable: bool = True) -> None:
        """Caller observed an error attributable to this key (429, 5xx, network)."""
        self._failure_reported = True
        self._retryable = retryable

    def report_success(self) -> None:
        """Optional — call on confirmed success to decay the EWMA error-rate."""
        self._success_reported = True


def parse_keys_csv(value: Optional[str]) -> list[str]:
    """Helper: split `KEY1,KEY2,KEY3` env var values into a clean list."""
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


# Platform key pools (owner request 2026-10-19). For these providers the
# resolver itself rotates the platform's keys, because the orchestrator always
# passes a resolved api_key and so never reaches the per-instance KeyPool path.
POOLED_ENV_VARS = frozenset({"ASSEMBLYAI_API_KEY", "DEEPSEEK_API_KEY"})

# How long a refused platform key sits out. A dead, unfunded or disabled key
# needs a person (new key, top-up) and a restart clears the bench; a rate or
# concurrency limit recovers by itself.
BENCH_REFUSED_KEY_S = 6 * 3600.0
BENCH_RATE_LIMITED_KEY_S = 60.0


class PlatformKeyRotation:
    """Process-wide rotation over the platform's own keys for one provider:
    ``<NAME>`` plus the comma-separated ``<NAME>S``, read on every call so an
    .env change needs only a restart. Each new session takes the next key that
    is not benched. Unlike KeyPool (one per provider instance) the bench is
    shared by every session in the process, so a refused key is skipped by
    later calls too."""

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        self._cursor = 0
        self._benched_until: dict[str, float] = {}

    def keys(self) -> list[str]:
        single = (os.getenv(self.env_var) or "").strip()
        out: list[str] = []
        for key in ([single] if single else []) + parse_keys_csv(os.getenv(self.env_var + "S")):
            if key not in out:
                out.append(key)
        return out

    def next_key(self, *, exclude: Iterable[str] = (), allow_benched: bool = True) -> Optional[str]:
        """A new session's key: the next unbenched key in rotation."""
        skip = set(exclude)
        keys = [k for k in self.keys() if k not in skip]
        if not keys:
            return None
        now = time.monotonic()
        ready = [k for k in keys if self._benched_until.get(k, 0.0) <= now]
        if not ready:
            if not allow_benched:
                return None
            # Every key is benched: the one that recovers first beats none.
            return min(keys, key=lambda k: self._benched_until.get(k, 0.0))
        key = ready[self._cursor % len(ready)]
        self._cursor += 1
        return key

    def retry_key(self, *, exclude: Iterable[str]) -> Optional[str]:
        """The key to retry a refused request on: the first unbenched key in
        .env order, not the next in rotation, so several refused keys in a row
        cost one extra round trip instead of one per refused key."""
        skip = set(exclude)
        now = time.monotonic()
        for key in self.keys():
            if key not in skip and self._benched_until.get(key, 0.0) <= now:
                return key
        return None

    def bench(self, key: str, seconds: float) -> bool:
        """Sit a refused key out. False when the key is not one of the
        platform's (a tenant's own key is never benched or swapped)."""
        keys = self.keys()
        if key not in keys:
            return False
        self._benched_until[key] = time.monotonic() + seconds
        # Position only, never key material: #n matches the order in the .env.
        logger.warning(
            "platform_key_benched env=%s key=#%d/%d cooldown_s=%.0f",
            self.env_var, keys.index(key) + 1, len(keys), seconds,
        )
        return True


_ROTATIONS: dict[str, PlatformKeyRotation] = {}


def platform_key_rotation(env_var: str) -> PlatformKeyRotation:
    rotation = _ROTATIONS.get(env_var)
    if rotation is None:
        rotation = _ROTATIONS[env_var] = PlatformKeyRotation(env_var)
    return rotation
