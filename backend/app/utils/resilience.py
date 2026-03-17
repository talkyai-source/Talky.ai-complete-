"""
Resilience utilities for production voice pipeline stability.

Provides:
- CircuitBreaker: Prevents cascading failures when external providers are down.
- retry_with_backoff: Async retry decorator with exponential backoff and jitter.

Usage:
    breaker = CircuitBreaker(name="groq", failure_threshold=3, recovery_timeout=30.0)

    @retry_with_backoff(max_retries=2, base_delay=0.5)
    async def call_provider():
        async with breaker:
            return await provider.do_work()
"""
import asyncio
import logging
import random
import time
from enum import Enum
from functools import wraps
from typing import Optional, Set, Type

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing — reject calls immediately
    HALF_OPEN = "half_open"  # Probing — allow one test call


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""
    def __init__(self, name: str, remaining_seconds: float):
        self.name = name
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"Circuit '{name}' is OPEN — retry in {remaining_seconds:.1f}s"
        )


class CircuitBreaker:
    """
    Async-safe circuit breaker with CLOSED → OPEN → HALF_OPEN → CLOSED lifecycle.

    Parameters
    ----------
    name : str
        Human-readable label (for logs/metrics).
    failure_threshold : int
        Consecutive failures before opening the circuit.
    recovery_timeout : float
        Seconds to wait in OPEN state before allowing a probe (HALF_OPEN).
    success_threshold : int
        Consecutive successes in HALF_OPEN required to close the circuit.
    excluded_exceptions : set of Exception types
        Exceptions that should NOT count as failures (e.g. validation errors).
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        excluded_exceptions: Optional[Set[Type[Exception]]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.excluded_exceptions: Set[Type[Exception]] = excluded_exceptions or set()

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    # --- async context-manager interface ---

    async def __aenter__(self):
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(f"CircuitBreaker[{self.name}]: OPEN → HALF_OPEN (probing)")
                else:
                    remaining = self.recovery_timeout - elapsed
                    raise CircuitOpenError(self.name, remaining)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self._on_success()
        elif exc_type and not issubclass(exc_type, tuple(self.excluded_exceptions)):
            await self._on_failure()
        # Don't suppress the exception
        return False

    async def _on_success(self):
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(f"CircuitBreaker[{self.name}]: HALF_OPEN → CLOSED (recovered)")
            else:
                # Reset failure count on any success in CLOSED state
                self._failure_count = 0

    async def _on_failure(self):
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — reopen immediately
                self._state = CircuitState.OPEN
                logger.warning(f"CircuitBreaker[{self.name}]: HALF_OPEN → OPEN (probe failed)")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"CircuitBreaker[{self.name}]: CLOSED → OPEN "
                    f"(failures={self._failure_count}/{self.failure_threshold})"
                )

    def reset(self):
        """Force-reset to CLOSED (useful during tests or provider re-init)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        logger.info(f"CircuitBreaker[{self.name}]: force-reset to CLOSED")


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    jitter: bool = True,
    retryable_exceptions: Optional[Set[Type[Exception]]] = None,
):
    """
    Decorator: retry an async function with exponential backoff + jitter.

    Parameters
    ----------
    max_retries : int
        Maximum number of retry attempts (0 = no retries).
    base_delay : float
        Initial delay in seconds.
    max_delay : float
        Cap on the backoff delay.
    jitter : bool
        Add randomised jitter to prevent thundering-herd.
    retryable_exceptions : set
        Exception types that trigger a retry.  Defaults to (Exception,).
    """
    _retryable = tuple(retryable_exceptions) if retryable_exceptions else (Exception,)

    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except _retryable as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {fn.__qualname__} "
                        f"after {delay:.2f}s — {exc}"
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator

