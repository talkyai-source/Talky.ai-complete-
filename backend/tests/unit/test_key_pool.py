"""Tests for app.infrastructure.providers.key_pool"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.infrastructure.providers.key_pool import (
    KeyPool,
    KeyPoolExhaustedError,
    parse_keys_csv,
)


@pytest.mark.asyncio
async def test_acquire_distributes_load_across_keys():
    pool = KeyPool("test", ["k1", "k2", "k3"])

    used: list[str] = []

    async def task():
        async with pool.acquire() as lease:
            used.append(lease.key)
            await asyncio.sleep(0.01)

    await asyncio.gather(*[task() for _ in range(9)])
    # With 3 keys and identical-length tasks, each key should be picked roughly equally.
    assert sorted(used).count("k1") >= 1
    assert sorted(used).count("k2") >= 1
    assert sorted(used).count("k3") >= 1


@pytest.mark.asyncio
async def test_failed_key_enters_cooldown_and_other_key_picked():
    pool = KeyPool("test", ["bad", "good"], cooldown_base_seconds=10.0)

    async with pool.acquire() as lease:
        assert lease.key in ("bad", "good")
        # Force the chosen key into cooldown.
        lease.report_failure(retryable=True)
        chosen_first = lease.key

    # Next acquire must pick the *other* key.
    async with pool.acquire() as lease2:
        assert lease2.key != chosen_first


@pytest.mark.asyncio
async def test_all_keys_in_cooldown_raises_exhausted():
    pool = KeyPool("test", ["k1", "k2"], cooldown_base_seconds=10.0)

    for _ in range(2):
        async with pool.acquire() as lease:
            lease.report_failure(retryable=True)

    with pytest.raises(KeyPoolExhaustedError):
        async with pool.acquire():
            pass


@pytest.mark.asyncio
async def test_cooldown_grows_exponentially_then_caps():
    pool = KeyPool(
        "test", ["k"], cooldown_base_seconds=1.0, cooldown_max_seconds=5.0
    )

    for expected in (1.0, 2.0, 4.0, 5.0, 5.0):
        try:
            async with pool.acquire() as lease:
                lease.report_failure(retryable=True)
        except KeyPoolExhaustedError:
            pass
        # Inspect cooldown remaining via stats.
        snap = pool.stats()[0]
        assert snap["cooldown_remaining_s"] == pytest.approx(expected, abs=0.5)
        # Force the cooldown to lapse so the next iteration can acquire again.
        pool._entries[0].cooldown_until = time.monotonic() - 0.01  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_success_decays_error_rate():
    pool = KeyPool("test", ["k"], cooldown_base_seconds=0.01)

    async with pool.acquire() as lease:
        lease.report_failure(retryable=True)
    err_after_fail = pool.stats()[0]["error_rate"]
    assert err_after_fail > 0.0

    # Wait out the tiny cooldown then run several successes.
    await asyncio.sleep(0.05)
    for _ in range(5):
        async with pool.acquire() as lease:
            lease.report_success()
    err_after_success = pool.stats()[0]["error_rate"]
    assert err_after_success < err_after_fail


@pytest.mark.asyncio
async def test_inflight_is_decremented_on_exception_in_caller():
    pool = KeyPool("test", ["k"])

    with pytest.raises(ValueError):
        async with pool.acquire() as lease:
            assert lease.key == "k"
            raise ValueError("caller blew up")

    assert pool.stats()[0]["in_flight"] == 0


def test_empty_pool_rejects_construction():
    with pytest.raises(ValueError):
        KeyPool("test", [])
    with pytest.raises(ValueError):
        KeyPool("test", ["", "  "])


def test_parse_keys_csv_handles_whitespace_and_dupes_at_caller_level():
    assert parse_keys_csv("a, b ,c") == ["a", "b", "c"]
    assert parse_keys_csv("") == []
    assert parse_keys_csv(None) == []


def test_pool_dedupes_keys_preserving_order():
    pool = KeyPool("test", ["a", "b", "a", "c"])
    assert pool.size == 3
    assert [e["key"][:1] for e in pool.stats()] == ["a", "b", "c"]
