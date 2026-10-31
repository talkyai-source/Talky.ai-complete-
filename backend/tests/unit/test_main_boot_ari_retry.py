"""S7: the boot ARI connect was attempted once and never retried.

``lifespan`` ran ``_auto_connect_telephony()`` a single time. In the non-strict
path a failure (Asterisk still starting, ARI port not yet listening) only
logged a warning, the process kept the owner lock, and every originate then hit
"adapter not connected" until someone restarted talky-api or POSTed /connect.

``_retry_boot_ari_connect`` is the background retry (capped backoff) that
``lifespan`` starts after a failed first attempt by the telephony owner, and
cancels on shutdown. The strict production-inbound path still re-raises.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from app import main as app_main


class _Backend:
    def __init__(self, owner: bool = True):
        self.owner = owner

    def is_telephony_owner(self) -> bool:
        return self.owner


class _Sleeper:
    """Records requested delays without waiting."""

    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


def _flaky_connect(failures: int, calls: list):
    async def connect() -> None:
        calls.append(1)
        if len(calls) <= failures:
            raise RuntimeError(f"ARI not up yet (attempt {len(calls)})")

    return connect


@pytest.mark.asyncio
async def test_retries_until_the_connect_succeeds_with_backoff():
    calls: list = []
    sleeper = _Sleeper()
    connected: list = []

    async def on_connected() -> None:
        connected.append(1)

    result = await app_main._retry_boot_ari_connect(
        _flaky_connect(2, calls),
        _Backend(),
        sleep=sleeper,
        on_connected=on_connected,
    )

    assert result is True
    assert len(calls) == 3  # two failures, then the connect that works
    assert sleeper.delays == [2.0, 5.0, 10.0]
    assert connected == [1]


@pytest.mark.asyncio
async def test_backoff_is_capped_and_keeps_going():
    calls: list = []
    sleeper = _Sleeper()

    result = await app_main._retry_boot_ari_connect(
        _flaky_connect(7, calls), _Backend(), sleep=sleeper
    )

    assert result is True
    assert sleeper.delays == [2.0, 5.0, 10.0, 30.0, 60.0, 60.0, 60.0, 60.0]
    assert max(sleeper.delays) == 60.0


@pytest.mark.asyncio
async def test_retry_stops_when_the_process_lost_the_owner_lock():
    calls: list = []
    backend = _Backend()
    sleeper = _Sleeper()

    async def sleep_then_lose_ownership(delay: float) -> None:
        await sleeper(delay)
        backend.owner = False

    result = await app_main._retry_boot_ari_connect(
        _flaky_connect(99, calls), backend, sleep=sleep_then_lose_ownership
    )

    assert result is False
    assert calls == []  # never connected a process that is no longer the owner


@pytest.mark.asyncio
async def test_retry_is_cancelled_cleanly_on_shutdown():
    calls: list = []

    async def never_returns(_delay: float) -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(
        app_main._retry_boot_ari_connect(
            _flaky_connect(99, calls), _Backend(), sleep=never_returns
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []


@pytest.mark.asyncio
async def test_failed_attempts_are_logged_as_warnings(caplog):
    calls: list = []
    with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
        await app_main._retry_boot_ari_connect(
            _flaky_connect(1, calls), _Backend(), sleep=_Sleeper()
        )
    assert "boot_ari_connect_retry_failed" in caplog.text
    assert "attempt 1" in caplog.text


@pytest.mark.asyncio
async def test_on_connected_failure_does_not_undo_the_connection(caplog):
    calls: list = []

    async def broken_recovery() -> None:
        raise RuntimeError("recovery blew up")

    with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
        result = await app_main._retry_boot_ari_connect(
            _flaky_connect(0, calls),
            _Backend(),
            sleep=_Sleeper(),
            on_connected=broken_recovery,
        )
    assert result is True
    assert "recovery blew up" in caplog.text


def test_lifespan_starts_the_retry_after_a_failed_boot_connect_and_cancels_it():
    """Source guard (not behavioural proof): the lifespan wiring."""
    source = inspect.getsource(app_main.lifespan)
    connect_at = source.index("await _auto_connect_telephony()\n        except Exception as e:")
    handler = source[connect_at : connect_at + 1400]
    # Strict production-inbound still re-raises, before any retry is armed.
    assert handler.index("if strict_validation and _production_inbound_enabled:") < handler.index(
        "_retry_boot_ari_connect"
    )
    assert "raise\n" in handler.split("_retry_boot_ari_connect")[0]
    assert "app.state.boot_ari_retry_task" in handler
    # Shutdown cancels it before the adapter is torn down.
    yield_at = source.index("    yield\n")
    after_yield = source[yield_at:]
    assert after_yield.index("boot_ari_retry_task") < after_yield.index("_tb._adapter.disconnect")
    assert "_boot_retry.cancel()" in after_yield
