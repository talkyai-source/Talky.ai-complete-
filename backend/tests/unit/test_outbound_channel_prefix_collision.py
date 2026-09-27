"""Three more sibling sites shared the same "talky-out" prefix collision as
the pre-answer-terminal branch covered by test_pre_answer_terminal_prefix.py
(day0923 forensics: 8b3176ca, 6e0e221b, 943702f0):

  1. The ChannelDestroyed branch that promotes a preemptive "Up" boundary to
     an outbound answer (``_promote_preemptive_up_to_outbound_answer``).
  2. The ChannelDestroyed branch that freezes the parent-leg terminal
     timestamp for a channel that isn't otherwise recognised as an outbound
     parent (``_record_terminal_at_monotonic``).
  3. Early-ringing dispatch (``_dispatch_early_ringing``), which only the
     real dialed leg should ever fire for.

Bare ``channel_id.startswith("talky-out")`` also matches
``talky-outbound-bridge-<hex>``/``talky-outbound-media-<hex>`` (this
adapter's own bridge/external-media ids for an ANSWERED call), so a
ChannelDestroyed for one of those could misfire any of the three sites
above. Fix: match only the real outbound leg id shape, ``talky-out-<uuid>``
(trailing hyphen), never the bridge/media ids that merely share the prefix.
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

_BOGUS_IDS = ["talky-outbound-bridge-abc123def456", "talky-outbound-media-abc123def456"]


async def _drain(adapter: AsteriskAdapter) -> None:
    for _ in range(20):
        if not adapter._terminal_cleanup_tasks:
            return
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 1. _promote_preemptive_up_to_outbound_answer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bogus_channel_id", _BOGUS_IDS)
async def test_bridge_and_media_ids_never_promote_preemptive_up_to_answer(bogus_channel_id):
    adapter = AsteriskAdapter()
    adapter._preemptive_up_channels.add(bogus_channel_id)

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": bogus_channel_id}}
    )
    await _drain(adapter)

    assert bogus_channel_id not in adapter._outbound_answered_at_monotonic


@pytest.mark.asyncio
async def test_a_real_preanswer_outbound_leg_still_promotes_preemptive_up_to_answer():
    adapter = AsteriskAdapter()
    channel_id = "talky-out-11111111-1111-1111-1111-111111111111"
    adapter._preemptive_up_channels.add(channel_id)

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": channel_id}}
    )
    await _drain(adapter)

    assert channel_id in adapter._outbound_answered_at_monotonic


# ---------------------------------------------------------------------------
# 2. _record_terminal_at_monotonic (non-inbound, non-otherwise-recognised leg)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bogus_channel_id", _BOGUS_IDS)
async def test_bridge_and_media_ids_never_record_terminal_at_monotonic(bogus_channel_id):
    adapter = AsteriskAdapter()

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": bogus_channel_id}}
    )
    await _drain(adapter)

    assert bogus_channel_id not in adapter._terminal_at_monotonic


@pytest.mark.asyncio
async def test_a_real_preanswer_outbound_leg_still_records_terminal_at_monotonic():
    adapter = AsteriskAdapter()
    channel_id = "talky-out-22222222-2222-2222-2222-222222222222"

    await adapter._handle_ari_event(
        {"type": "ChannelDestroyed", "channel": {"id": channel_id}}
    )
    await _drain(adapter)

    assert channel_id in adapter._terminal_at_monotonic


# ---------------------------------------------------------------------------
# 3. _dispatch_early_ringing — bridge/media ids never ring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bogus_channel_id", _BOGUS_IDS)
async def test_bridge_and_media_ids_never_fire_early_ringing(bogus_channel_id):
    adapter = AsteriskAdapter()
    on_ring = AsyncMock()
    adapter._on_early_ringing = on_ring

    adapter._dispatch_early_ringing(bogus_channel_id)
    await asyncio.sleep(0)

    on_ring.assert_not_called()
    assert bogus_channel_id not in adapter._early_ring_emitted


@pytest.mark.asyncio
async def test_a_real_dialed_leg_still_fires_early_ringing():
    adapter = AsteriskAdapter()
    on_ring = AsyncMock()
    adapter._on_early_ringing = on_ring
    channel_id = "talky-out-33333333-3333-3333-3333-333333333333"

    adapter._dispatch_early_ringing(channel_id)
    await asyncio.sleep(0)

    on_ring.assert_awaited_once_with(channel_id)
    assert channel_id in adapter._early_ring_emitted
