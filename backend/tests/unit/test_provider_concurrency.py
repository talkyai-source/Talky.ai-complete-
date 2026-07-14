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


@pytest.mark.asyncio
async def test_elevenlabs_default_guard_is_below_self_service_account_caps():
    """The old default (200) sat above every self-service ElevenLabs plan's
    concurrent_requests cap (~4-30) AND above the aiohttp connector's
    limit_per_host=50 in elevenlabs_tts.py, so requests queued invisibly
    inside aiohttp instead of at this guard. The default must stay small
    enough to be a realistic account cap, and must not exceed the connector's
    limit_per_host=50 (see elevenlabs_tts.py's TCPConnector)."""
    g = get_provider_guard("elevenlabs")
    assert g.max_concurrent <= 30, (
        "default must fit within realistic self-service account tiers"
    )
    assert g.max_concurrent <= 50, (
        "default must not exceed the aiohttp connector limit_per_host"
    )


@pytest.mark.asyncio
async def test_elevenlabs_guard_is_env_overridable(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_MAX_CONCURRENT", "25")
    g = get_provider_guard("elevenlabs")
    assert g.max_concurrent == 25
