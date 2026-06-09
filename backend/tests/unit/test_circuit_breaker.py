"""CircuitBreaker control-flow exclusion (the 2026-06-09 call-stall fix).

Control-flow exceptions — ``asyncio.CancelledError`` (task cancelled) and
``GeneratorExit`` (async generator closed early) — are normal flow, not
service faults. They must NEVER count toward the breaker. Counting the
``GeneratorExit`` thrown when the Gemini streaming wrapper closed a stalled
stream early is what opened the ``gemini-llm`` breaker mid-call and killed
every subsequent turn.
"""
import asyncio

import pytest

from app.utils.resilience import CircuitBreaker, CircuitOpenError, CircuitState


def _breaker(**kw):
    return CircuitBreaker(
        name="test",
        failure_threshold=3,
        recovery_timeout=30.0,
        success_threshold=1,
        **kw,
    )


@pytest.mark.asyncio
async def test_generator_exit_never_counts_as_failure():
    cb = _breaker()
    for _ in range(10):
        with pytest.raises(GeneratorExit):
            async with cb:
                raise GeneratorExit
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


@pytest.mark.asyncio
async def test_cancelled_error_never_counts_as_failure():
    cb = _breaker()
    for _ in range(10):
        with pytest.raises(asyncio.CancelledError):
            async with cb:
                raise asyncio.CancelledError
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


@pytest.mark.asyncio
async def test_real_exception_still_opens_breaker():
    cb = _breaker()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            async with cb:
                raise RuntimeError("boom")
    assert cb.state == CircuitState.OPEN
    # Once open, entry fast-fails without running the body.
    with pytest.raises(CircuitOpenError):
        async with cb:
            pass


@pytest.mark.asyncio
async def test_explicitly_excluded_exception_does_not_count():
    cb = _breaker(excluded_exceptions={ValueError})
    for _ in range(5):
        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("ignored")
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_success_resets_failure_count():
    cb = _breaker()
    with pytest.raises(RuntimeError):
        async with cb:
            raise RuntimeError("x")
    assert cb._failure_count == 1
    async with cb:
        pass  # a clean turn resets the count
    assert cb._failure_count == 0
