"""HA plan, phase 3: the standby call process keeps trying for the phone lock.

A call process that lost the telephony owner-lock race used to retry six times
(~5 minutes) and then give up for good. On one server that only covered a
restart; on a server pair it meant the standby could never take the phones over
from an owner that died later.

``_retry_telephony_ownership`` now retries for as long as the process runs, and
``_acquire_as_standby`` keeps a production standby from failing OPEN into a
second owner when Redis errors (the plain acquire returns True on a Redis error).
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from app import main as app_main


class _Sleeper:
    """Records requested delays without waiting."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


def _acquire_after(losses: int, calls: list, *, error: bool = False):
    async def acquire() -> bool:
        calls.append(1)
        if len(calls) <= losses:
            if error:
                raise RuntimeError("redis unavailable")
            return False
        return True

    return acquire


@pytest.mark.asyncio
async def test_standby_keeps_retrying_long_past_five_minutes():
    calls: list = []
    taken_over: list = []
    sleeper = _Sleeper()

    async def take_over() -> None:
        taken_over.append(1)

    # 30 lost tries at 50 s is 25 minutes; the old loop stopped after 6.
    won_on = await app_main._retry_telephony_ownership(
        _acquire_after(30, calls), take_over, sleep=sleeper
    )

    assert won_on == 31
    assert taken_over == [1]
    assert sleeper.delays == [50.0] * 31


@pytest.mark.asyncio
async def test_a_failed_take_over_is_retried_on_the_next_period(caplog):
    calls: list = []
    attempts: list = []

    async def take_over() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("ARI not up yet")

    with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
        won_on = await app_main._retry_telephony_ownership(
            _acquire_after(0, calls), take_over, sleep=_Sleeper()
        )

    assert won_on == 2
    assert len(attempts) == 2
    assert "telephony_ownership_retry_failed attempt=1" in caplog.text


@pytest.mark.asyncio
async def test_standby_warning_is_rate_limited(caplog):
    calls: list = []

    async def take_over() -> None:
        return None

    with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
        await app_main._retry_telephony_ownership(
            _acquire_after(25, calls), take_over, sleep=_Sleeper(), log_every=12
        )

    assert caplog.text.count("telephony_standby attempt=") == 2


@pytest.mark.asyncio
async def test_retry_is_cancelled_cleanly_on_shutdown():
    calls: list = []

    async def never_returns(_delay: float) -> None:
        await asyncio.Event().wait()

    async def take_over() -> None:
        raise AssertionError("must not take over during shutdown")

    task = asyncio.create_task(
        app_main._retry_telephony_ownership(
            _acquire_after(0, calls), take_over, sleep=never_returns
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []


class _FailOpenBackend:
    """The plain acquire fails OPEN on a Redis error; the strict one raises."""

    def __init__(self, strict_errors: int):
        self.strict_errors = strict_errors
        self.strict_calls = 0
        self.plain_calls = 0

    async def acquire_telephony_ownership(self) -> bool:
        self.plain_calls += 1
        return True  # what the registry returns when Redis is down

    async def acquire_telephony_ownership_strict(self) -> bool:
        self.strict_calls += 1
        if self.strict_calls <= self.strict_errors:
            raise RuntimeError("redis unavailable")
        return True


@pytest.mark.asyncio
async def test_production_standby_never_fails_open_into_a_second_owner():
    backend = _FailOpenBackend(strict_errors=3)
    taken_over: list = []

    async def take_over() -> None:
        taken_over.append(backend.strict_calls)

    won_on = await app_main._retry_telephony_ownership(
        lambda: app_main._acquire_as_standby(backend, production=True),
        take_over,
        sleep=_Sleeper(),
    )

    # Redis errors kept it standby; it took over only once Redis answered.
    assert won_on == 4
    assert taken_over == [4]
    assert backend.plain_calls == 0


@pytest.mark.asyncio
async def test_outside_production_the_plain_acquire_is_used():
    backend = _FailOpenBackend(strict_errors=99)

    assert await app_main._acquire_as_standby(backend, production=False) is True
    assert backend.plain_calls == 1
    assert backend.strict_calls == 0


@pytest.mark.asyncio
async def test_a_backend_without_strict_acquire_uses_the_plain_one():
    class _MemoryBackend:
        async def acquire_telephony_ownership(self) -> bool:
            return True

    assert await app_main._acquire_as_standby(_MemoryBackend(), production=True) is True


def test_lifespan_keeps_the_standby_retry_and_cancels_it_on_shutdown():
    """Source guard (not behavioural proof): the lifespan wiring."""
    source = inspect.getsource(app_main.lifespan)
    assert "range(1, 7)" not in source
    assert "app.state.telephony_ownership_retry_task = asyncio.create_task(" in source
    assert "_acquire_as_standby(_state_backend, production=strict_validation)" in source
    yield_at = source.index("    yield\n")
    after_yield = source[yield_at:]
    assert after_yield.index("telephony_ownership_retry_task") < after_yield.index(
        "_tb._adapter.disconnect"
    )
    assert "_owner_retry.cancel()" in after_yield
