"""telephony-infra-media-3: hangup_call must report what the PBX confirmed.

AsteriskAdapter.hangup() is fail-soft and discards the bool that
hangup_confirmed() computes, so hangup_call() used to return True after a
failed ARI DELETE.  end_session then skipped local teardown, machine_detection
skipped its error log and the audio-route force-hangup zeroed its failure
streak while the call stayed connected.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway


async def _gateway_with(adapter, call_id="call-1", pbx="pbx-1") -> TelephonyMediaGateway:
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    await gateway.on_call_started(call_id, {"adapter": adapter, "pbx_call_id": pbx})
    return gateway


class _SoftAdapter:
    """Adapter without hangup_confirmed (anything but Asterisk)."""

    def __init__(self):
        self.hangup = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_unconfirmed_pbx_hangup_returns_false():
    adapter = AsyncMock()
    adapter.hangup_confirmed.return_value = False
    gateway = await _gateway_with(adapter)

    assert await gateway.hangup_call("call-1", "user_goodbye") is False
    adapter.hangup_confirmed.assert_awaited_once_with("pbx-1")


@pytest.mark.asyncio
async def test_confirmed_pbx_hangup_returns_true():
    adapter = AsyncMock()
    adapter.hangup_confirmed.return_value = True
    gateway = await _gateway_with(adapter)

    assert await gateway.hangup_call("call-1", "user_goodbye") is True
    adapter.hangup_confirmed.assert_awaited_once_with("pbx-1")


@pytest.mark.asyncio
async def test_adapter_without_hangup_confirmed_keeps_the_legacy_contract():
    adapter = _SoftAdapter()
    gateway = await _gateway_with(adapter)

    assert await gateway.hangup_call("call-1", "user_goodbye") is True
    adapter.hangup.assert_awaited_once_with("pbx-1")


@pytest.mark.asyncio
async def test_unconfirmed_forced_hangup_is_not_a_success():
    """_on_force_hangup_done must not zero the failure streak on an unconfirmed hangup."""
    adapter = AsyncMock()
    adapter.hangup_confirmed.return_value = False
    gateway = await _gateway_with(adapter)
    session = gateway._sessions["call-1"]
    session.consecutive_audio_route_failures = gateway._AUDIO_ROUTE_FORCE_END_THRESHOLD - 1

    with patch("app.utils.audio_utils.ulaw_to_pcm", side_effect=RuntimeError("bad packet")):
        await gateway.on_audio_received("call-1", b"\xff" * 160)

    task = session.force_hangup_task
    assert task is not None
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert session.consecutive_audio_route_failures >= gateway._AUDIO_ROUTE_FORCE_END_THRESHOLD
    assert session.is_active is False
