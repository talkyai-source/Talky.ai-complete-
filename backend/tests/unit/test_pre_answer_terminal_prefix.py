"""pre-answer-terminal-prefix-collision (day0923 forensics: 8b3176ca,
6e0e221b, 943702f0).

The 'pre-answer terminal' branch in _handle_ari_event exists to catch a
channel WE originated dying before it ever enters Stasis (busy/no-answer/
rejected — no StasisStart with app-originate). It is gated by
``channel_id.startswith("talky-out")``, which also matches the bridge and
external-media leg ids the adapter itself generates for an ANSWERED call
(``talky-outbound-bridge-<hex>`` from asterisk_adapter.py, ``talky-outbound-
media-<hex>``), because "talky-outbound-..." also starts with "talky-out".
A ChannelDestroyed for either of those on an answered call fires a
mislabelled, duplicate call-end dispatch:

    943702f0.talky-api.log:149-154 'pre-answer terminal channel=talky-outbou'
    (18:12:04.916945) immediately followed by the real
    'session ended channel=talky-out-94 reason=ChannelHangupRequest'
    (18:12:04.918355).

Fix: match only the real outbound leg id shape, ``talky-out-<uuid>``
(telephony_bridge.py: ``f"talky-out-{call_id}"``), never the bridge/media
ids that merely share the "talky-out" prefix.
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


async def _drain(adapter: AsteriskAdapter) -> None:
    for _ in range(20):
        if not adapter._terminal_cleanup_tasks:
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bogus_channel_id",
    ["talky-outbound-bridge-abc123def456", "talky-outbound-media-abc123def456"],
)
async def test_bridge_and_media_ids_never_dispatch_a_pre_answer_call_end(bogus_channel_id):
    adapter = AsteriskAdapter()
    on_end = AsyncMock()
    adapter._on_any_call_end = on_end

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": bogus_channel_id}}
    )
    await _drain(adapter)

    on_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_real_preanswer_outbound_leg_still_dispatches_call_end():
    """The branch must keep doing its actual job for the real leg id shape."""
    adapter = AsteriskAdapter()
    on_end = AsyncMock()
    adapter._on_any_call_end = on_end
    channel_id = "talky-out-11111111-1111-1111-1111-111111111111"

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": channel_id}}
    )
    await _drain(adapter)

    on_end.assert_awaited_once_with(channel_id)
