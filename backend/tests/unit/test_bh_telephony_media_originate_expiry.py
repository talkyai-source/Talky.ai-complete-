"""telephony-infra-media-5: the originated-channel entry must outlive the ring window.

originate_call() asked ARI to ring for DIALER_RING_TIMEOUT_S (300 s in
production) but forgot the pre-generated channel id after a hard-coded 30 s.
A callee answering later arrived at _correlate_trunk_leg with nothing to match
and the answered call was hung up as an uncorrelated trunk leg.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

import app.infrastructure.telephony.asterisk_adapter as aa
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

_REAL_SLEEP = asyncio.sleep
_ADAPTERS: list[AsteriskAdapter] = []


@pytest_asyncio.fixture(autouse=True)
async def _cancel_expiry_timers():
    yield
    for adapter in _ADAPTERS:
        for task in list(adapter._originated_expiry_tasks.values()):
            task.cancel()
    await _REAL_SLEEP(0)
    _ADAPTERS.clear()


@pytest.fixture()
def sleeps(monkeypatch):
    """Record every positive sleep and park it forever (no real waiting)."""
    delays: list[float] = []

    async def fake_sleep(delay, *args, **kwargs):
        if delay and delay > 0:
            delays.append(delay)
            await asyncio.Event().wait()
        else:
            await _REAL_SLEEP(0)

    monkeypatch.setattr(aa.asyncio, "sleep", fake_sleep)
    return delays


async def _originate(monkeypatch, ring_timeout: str | None, channel_id="pre-1"):
    if ring_timeout is None:
        monkeypatch.delenv("DIALER_RING_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("DIALER_RING_TIMEOUT_S", ring_timeout)
    adapter = AsteriskAdapter()
    _ADAPTERS.append(adapter)
    adapter._ari = AsyncMock(return_value={"id": channel_id})
    result = await adapter.originate_call("+442071234567", "+15550001", channel_id=channel_id)
    await _REAL_SLEEP(0)  # let the expiry task reach its sleep
    return adapter, result


@pytest.mark.asyncio
async def test_expiry_outlives_a_300_second_ring_timeout(monkeypatch, sleeps):
    adapter, channel_id = await _originate(monkeypatch, "300")

    assert channel_id == "pre-1"
    assert "pre-1" in adapter._originated_channels
    assert sleeps and min(sleeps) > 300


@pytest.mark.asyncio
async def test_expiry_outlives_the_default_ring_timeout(monkeypatch, sleeps):
    await _originate(monkeypatch, None)

    assert sleeps and min(sleeps) > 30


@pytest.mark.asyncio
async def test_stale_entry_is_still_cleaned_up_when_the_timer_fires(monkeypatch):
    """The cleanup must remain: it feeds the is_trunk_leg heuristic."""
    delays: list[float] = []

    async def instant_sleep(delay, *args, **kwargs):
        if delay and delay > 0:
            delays.append(delay)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(aa.asyncio, "sleep", instant_sleep)
    adapter, _ = await _originate(monkeypatch, "300")
    for _ in range(5):
        await _REAL_SLEEP(0)

    assert delays
    assert "pre-1" not in adapter._originated_channels


@pytest.mark.asyncio
async def test_expiry_task_is_cancelled_when_the_origination_is_consumed(monkeypatch, sleeps):
    adapter, _ = await _originate(monkeypatch, "300")
    task = adapter._originated_expiry_tasks["pre-1"]
    assert not task.done()

    adapter._discard_originated_channel("pre-1")
    await _REAL_SLEEP(0)

    assert task.cancelled()
    assert "pre-1" not in adapter._originated_expiry_tasks


@pytest.mark.asyncio
async def test_trunk_leg_correlation_still_matches_a_late_answer(monkeypatch, sleeps):
    """A leg entering Stasis 60 s after originate (inside the ring window) correlates."""
    adapter, _ = await _originate(monkeypatch, "300")
    adapter._ari = AsyncMock(return_value={"value": "pre-1"})

    assert await adapter._correlate_trunk_leg("trunk-leg-1") == "pre-1"
    assert "pre-1" not in adapter._originated_channels
