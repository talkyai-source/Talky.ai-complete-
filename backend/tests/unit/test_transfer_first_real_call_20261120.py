"""First-real-call transfer defects found by inspection (2026-11-20).

1. The ExternalMedia channel's own StasisEnd/ChannelDestroyed (caused by our
   DELETE during detach) must not be mistaken for the call's media ending.
2. Asterisk 20 rejects /channels/create carrying both originator and formats.
"""

import asyncio

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter  # noqa: F401
from tests.unit.test_asterisk_supervised_transfer import _live_inbound_adapter

EXT = "external-media"


async def _start_transfer(adapter, parent):
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000101",
        )
    )
    for _ in range(20):
        if adapter._transfers_by_parent.get(parent) is not None:
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    return task, adapter._transfers_by_parent[parent]


def _wire(adapter, ari_calls, on_ext_delete):
    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_calls.append((method, path, dict(params or {})))
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and path == f"/channels/{EXT}":
            return await on_ext_delete()
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def fake_gateway(method, path, *, payload=None, **kwargs):
        return {}

    async def active_ids():
        return set()

    adapter._ari = fake_ari
    adapter._gateway = fake_gateway
    adapter.list_active_channel_ids = active_ids

    async def noop(*a, **k):
        return 1

    adapter.set_transfer_answered_persist_callback(noop)
    adapter.set_transfer_connected_callback(noop)


@pytest.mark.asyncio
async def test_external_media_end_events_during_detach_do_not_hang_up_caller_or_target():
    adapter, parent = _live_inbound_adapter()
    ari_calls = []

    async def ext_delete():
        # Asterisk emits the ExternalMedia channel's end events BEFORE the
        # DELETE response reaches us.
        for event_type in ("StasisEnd", "ChannelDestroyed"):
            await adapter._handle_ari_event(
                {
                    "type": event_type,
                    "channel": {"id": EXT, "name": "UnicastRTP/x"},
                }
            )
        await asyncio.sleep(0)
        return 204, {}

    _wire(adapter, ari_calls, ext_delete)
    task, leg = await _start_transfer(adapter, parent)
    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    result = await asyncio.wait_for(task, timeout=2)
    for _ in range(5):
        await asyncio.sleep(0)

    assert result["status"] == "completed"
    assert result["handoff_confirmed"] is True
    deleted = [path for method, path, _ in ari_calls if method == "DELETE"]
    assert f"/channels/{parent}" not in deleted
    assert f"/channels/{leg.target_id}" not in deleted
    assert parent not in adapter._end_dispatched


@pytest.mark.asyncio
async def test_failed_external_media_hangup_restores_ext_channel_entry():
    adapter, parent = _live_inbound_adapter()
    ari_calls = []

    async def ext_delete():
        return 204, {}

    _wire(adapter, ari_calls, ext_delete)

    async def still_active():
        return {EXT}

    adapter.list_active_channel_ids = still_active
    adapter._hangup_confirm_timeout_s = 0.05
    adapter._hangup_confirm_poll_s = 0.01

    leg = type("Leg", (), {})()
    leg.parent_id = parent
    leg.bridge_id = "bridge-1"
    leg.target_id = "target-x"

    with pytest.raises(RuntimeError, match="transfer_ai_media_detach_unconfirmed"):
        await adapter._detach_ai_media_for_transfer(leg)
    assert adapter._ext_channels[parent] == EXT


@pytest.mark.asyncio
async def test_transfer_create_does_not_send_formats_with_originator():
    adapter, parent = _live_inbound_adapter()
    ari_calls = []

    async def ext_delete():
        return 204, {}

    _wire(adapter, ari_calls, ext_delete)
    task, leg = await _start_transfer(adapter, parent)
    create = next(p for m, path, p in ari_calls if path == "/channels/create")
    assert create["originator"] == parent
    assert "formats" not in create
    task.cancel()
    try:
        await task
    except BaseException:
        pass
