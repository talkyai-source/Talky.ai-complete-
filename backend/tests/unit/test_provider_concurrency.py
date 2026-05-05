"""Tests for app.infrastructure.providers.provider_concurrency"""
from __future__ import annotations

import asyncio

import pytest

from app.infrastructure.providers.provider_concurrency import (
    ProviderConcurrencyGuard,
    ProviderGuardTimeout,
    get_provider_guard,
    reset_guards_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_guards_for_tests()
    yield
    reset_guards_for_tests()


@pytest.mark.asyncio
async def test_guard_allows_up_to_max_in_flight():
    guard = ProviderConcurrencyGuard("test", max_concurrent=3, wait_timeout=2.0)

    held: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]

    async def worker(idx: int):
        async with guard.acquire():
            held[idx].set()
            await asyncio.sleep(0.05)

    tasks = [asyncio.create_task(worker(i)) for i in range(3)]
    await asyncio.gather(*[h.wait() for h in held])
    assert guard.in_flight == 3
    await asyncio.gather(*tasks)
    assert guard.in_flight == 0


@pytest.mark.asyncio
async def test_guard_queues_overflow_callers():
    guard = ProviderConcurrencyGuard("test", max_concurrent=1, wait_timeout=2.0)
    order: list[int] = []

    async def worker(idx: int):
        async with guard.acquire():
            order.append(idx)
            await asyncio.sleep(0.02)

    await asyncio.gather(worker(1), worker(2), worker(3))
    assert order == [1, 2, 3]
    assert guard.snapshot()["total_waits"] >= 2


@pytest.mark.asyncio
async def test_guard_times_out_when_full():
    guard = ProviderConcurrencyGuard("test", max_concurrent=1, wait_timeout=0.05)

    holder_release = asyncio.Event()

    async def holder():
        async with guard.acquire():
            await holder_release.wait()

    h = asyncio.create_task(holder())
    await asyncio.sleep(0.01)

    with pytest.raises(ProviderGuardTimeout):
        async with guard.acquire():
            pass

    holder_release.set()
    await h
    assert guard.snapshot()["total_timeouts"] == 1


@pytest.mark.asyncio
async def test_registry_returns_singleton():
    g1 = get_provider_guard("groq")
    g2 = get_provider_guard("groq")
    assert g1 is g2


@pytest.mark.asyncio
async def test_registry_reads_env_var(monkeypatch):
    monkeypatch.setenv("CARTESIA_MAX_CONCURRENT", "13")
    g = get_provider_guard("cartesia")
    assert g.max_concurrent == 13
