import asyncio

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _live_inbound_adapter():
    adapter = AsteriskAdapter()
    parent = "inbound-parent"
    adapter._connected_flag = True
    adapter._active_sessions[parent] = {
        "session_id": "gateway-session",
        "listen_port": 32001,
        "bridge_id": "bridge-1",
        "direction": "inbound",
    }
    adapter._gateway_sessions[parent] = "gateway-session"
    adapter._ext_channels[parent] = "external-media"
    adapter._bridges[parent] = "bridge-1"
    adapter._rtp_in_use.add(32001)
    adapter._inbound_admissions[parent] = {
        "allowed": True,
        "trunk_id": "tenant-trunk-id",
        "config_snapshot": {
            "route": {"sip_trunk_name": "customer-byo-trunk"},
        },
    }
    return adapter, parent


@pytest.mark.asyncio
async def test_supervised_transfer_waits_for_answer_and_uses_tenant_trunk():
    adapter, parent = _live_inbound_adapter()
    ari_calls = []
    gateway_calls = []
    connected = []
    answer_persisted = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_calls.append((method, path, dict(params or {})))
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def fake_gateway(method, path, *, payload=None, **kwargs):
        gateway_calls.append((method, path, dict(payload or {})))
        return {}

    async def on_connected(call_id, target_id):
        connected.append((call_id, target_id))

    async def persist_answer(call_id, target_id):
        answer_persisted.append((call_id, target_id))
        return 1

    adapter._ari = fake_ari
    adapter._gateway = fake_gateway
    adapter.set_transfer_answered_persist_callback(persist_answer)
    adapter.set_transfer_connected_callback(on_connected)

    transfer_task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000001",
        )
    )
    for _ in range(20):
        if adapter._transfers_by_parent.get(parent) is not None:
            break
        await asyncio.sleep(0)
    leg = adapter._transfers_by_parent[parent]

    # Provider setup/dial acceptance is not transfer success.
    await asyncio.sleep(0)
    assert not transfer_task.done()
    create = next(item for item in ari_calls if item[1] == "/channels/create")
    assert create[2]["endpoint"] == "PJSIP/+15559876543@trunk-tenant-trunk-id"
    assert not any(path.endswith("/redirect") for _, path, _ in ari_calls)
    assert not any(
        path == "/bridges/bridge-1/addChannel" and params.get("channel") == leg.target_id
        for _, path, params in ari_calls
    )
    assert adapter._gateway_sessions[parent] == "gateway-session"

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": leg.target_id, "state": "Up"},
        }
    )
    result = await asyncio.wait_for(transfer_task, timeout=1)

    assert result["status"] == "completed"
    assert answer_persisted == [(parent, leg.target_id)]
    assert result["answered"] is True
    assert result["handoff_confirmed"] is True
    assert result["target_call_id"] == leg.target_id
    assert connected == [(parent, leg.target_id)]
    assert gateway_calls[-1][2]["reason"] == "transfer_connected"
    assert parent not in adapter._gateway_sessions
    assert parent not in adapter._ext_channels
    assert adapter._active_sessions[parent]["media_detached_for_transfer"] is True
    assert 32001 not in adapter._rtp_in_use
    detach_index = next(
        index
        for index, (_, path, params) in enumerate(ari_calls)
        if path == "/bridges/bridge-1/removeChannel" and params.get("channel") == "external-media"
    )
    bridge_target_index = next(
        index
        for index, (_, path, params) in enumerate(ari_calls)
        if path == "/bridges/bridge-1/addChannel" and params.get("channel") == leg.target_id
    )
    assert detach_index < bridge_target_index


@pytest.mark.asyncio
async def test_returned_transfer_id_mismatch_is_rejected_before_bridge_or_dial():
    adapter, parent = _live_inbound_adapter()
    planned = "talky-xfer-00000000000000000005"
    actual = "asterisk-actual-transfer-target"
    ari_paths: list[str] = []
    captured = {}

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_paths.append(path)
        if path == "/channels/create":
            captured["leg"] = adapter._transfers_by_parent[parent]
            return {"id": actual}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = fake_ari
    result = await adapter.transfer(
        parent,
        "+15559876543",
        "blind",
        provider_leg_id=planned,
    )

    for _ in range(50):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)

    assert result["status"] == "failed"
    assert result["error"] == "provider_leg_id_mismatch"
    assert result["target_call_id"] == planned
    assert result["actual_target_call_id"] == actual
    assert "/channels/create" in ari_paths
    assert f"/bridges/bridge-1/addChannel" not in ari_paths
    assert f"/channels/{actual}/dial" not in ari_paths
    assert f"/channels/{actual}" in ari_paths
    assert f"/channels/{parent}" not in ari_paths
    assert captured["leg"].persisted_target_id == planned


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_target", "event_parent"),
    [
        ("asterisk-unexpected-target", "inbound-parent"),
        ("talky-xfer-00000000000000000006", "wrong-parent"),
    ],
)
async def test_stasis_target_identity_mismatch_never_rebinds_planned_leg(
    event_target,
    event_parent,
):
    adapter, parent = _live_inbound_adapter()
    planned = "talky-xfer-00000000000000000006"
    loop = asyncio.get_running_loop()
    from app.infrastructure.telephony.asterisk_adapter import _AsteriskTransferLeg

    leg = _AsteriskTransferLeg(
        parent_id=parent,
        target_id=planned,
        bridge_id="bridge-1",
        destination="+15559876543",
        endpoint="PJSIP/+15559876543@trunk-tenant-trunk-id",
        mode="blind",
        future=loop.create_future(),
        persisted_target_id=planned,
    )
    adapter._transfers_by_parent[parent] = leg
    adapter._transfers_by_target[planned] = leg
    rejected = []
    adapter._schedule_unclaimed_hangup = lambda channel_id, *, reason: rejected.append(
        (channel_id, reason)
    )

    await adapter._handle_ari_event(
        {
            "type": "StasisStart",
            "args": ["transfer_target", event_parent],
            "channel": {"id": event_target, "name": "PJSIP/transfer-target"},
        }
    )

    assert leg.target_id == planned
    assert leg.persisted_target_id == planned
    assert adapter._transfers_by_parent[parent] is leg
    assert adapter._transfers_by_target[planned] is leg
    if event_target != planned:
        assert event_target not in adapter._transfers_by_target
    assert rejected == [(event_target, "transfer_target_identity_mismatch")]


@pytest.mark.asyncio
async def test_gateway_stop_failure_retains_retryable_media_ownership_and_no_success():
    adapter, parent = _live_inbound_adapter()
    loop = asyncio.get_running_loop()
    from app.infrastructure.telephony.asterisk_adapter import _AsteriskTransferLeg

    target = "talky-xfer-00000000000000000007"
    leg = _AsteriskTransferLeg(
        parent_id=parent,
        target_id=target,
        bridge_id="bridge-1",
        destination="+15559876543",
        endpoint="PJSIP/+15559876543@trunk-tenant-trunk-id",
        mode="blind",
        future=loop.create_future(),
        connected=True,
        persisted_target_id=target,
    )
    adapter._transfers_by_parent[parent] = leg
    adapter._transfers_by_target[target] = leg
    ari_paths = []
    terminal_cleanup = []

    async def fake_gateway(*_args, **_kwargs):
        raise RuntimeError("gateway unavailable")

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_paths.append(path)
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    def capture_terminal(owner_id, _factory, *, reason):
        terminal_cleanup.append((owner_id, reason))
        return True

    adapter._gateway = fake_gateway
    adapter._ari = fake_ari
    adapter._schedule_terminal_cleanup = capture_terminal

    await adapter._complete_transfer_connection(leg)

    assert not leg.future.done()
    assert leg.pending_failure["error"] == "transfer_handoff_failed"
    assert adapter._gateway_sessions[parent] == "gateway-session"
    assert adapter._active_sessions[parent]["listen_port"] == 32001
    assert 32001 in adapter._rtp_in_use
    assert adapter._active_sessions[parent].get("media_detached_for_transfer") is not True
    assert "/bridges/bridge-1/addChannel" not in ari_paths
    assert terminal_cleanup == [(parent, "transfer_handoff_failed")]


@pytest.mark.asyncio
async def test_platform_default_transfer_uses_shared_configured_endpoint(monkeypatch):
    adapter, parent = _live_inbound_adapter()
    adapter._inbound_admissions[parent]["config_snapshot"]["route"][
        "sip_trunk_name"
    ] = "platform-default"
    monkeypatch.setenv("TELEPHONY_PJSIP_OUTBOUND_ENDPOINT", "blazedigitel-endpoint")
    created = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        if path == "/channels/create":
            created.append(dict(params or {}))
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = fake_ari
    transfer_task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000002",
        )
    )
    for _ in range(20):
        leg = adapter._transfers_by_parent.get(parent)
        if leg is not None:
            break
        await asyncio.sleep(0)
    assert created[0]["endpoint"] == "PJSIP/+15559876543@blazedigitel-endpoint"
    adapter._resolve_transfer_failure(leg, "test_complete")
    await transfer_task
    for _ in range(20):
        if parent not in adapter._transfers_by_parent:
            break
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_busy_target_keeps_ai_and_caller_connected():
    adapter, parent = _live_inbound_adapter()
    ari_calls = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_calls.append((method, path, dict(params or {})))
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def unexpected_gateway(*args, **kwargs):
        raise AssertionError("AI gateway must remain live when the target is busy")

    adapter._ari = fake_ari
    adapter._gateway = unexpected_gateway
    transfer_task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000003",
        )
    )
    for _ in range(20):
        if adapter._transfers_by_parent.get(parent) is not None:
            break
        await asyncio.sleep(0)
    leg = adapter._transfers_by_parent[parent]

    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "BUSY",
        }
    )
    result = await asyncio.wait_for(transfer_task, timeout=1)

    assert result["status"] == "failed"
    assert result["error"] == "busy"
    assert result["target_termination_confirmed"] is True
    assert result["caller_media_retained"] is True
    assert adapter._gateway_sessions[parent] == "gateway-session"
    assert adapter._ext_channels[parent] == "external-media"
    assert parent in adapter._active_sessions
    for _ in range(20):
        if parent not in adapter._transfers_by_parent:
            break
        await asyncio.sleep(0)
    assert parent not in adapter._transfers_by_parent


@pytest.mark.asyncio
async def test_busy_then_late_up_cannot_resurrect_target_or_start_handoff():
    adapter, parent = _live_inbound_adapter()
    cleanup_entered = asyncio.Event()
    allow_cleanup = asyncio.Event()
    handoff_calls = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        return {}

    async def confirmed(call_ids, *, fence_root=True):
        cleanup_entered.set()
        await allow_cleanup.wait()
        assert tuple(call_ids) == (leg.target_id,)
        assert fence_root is False
        return True

    async def unexpected_handoff(value):
        handoff_calls.append(value.target_id)

    adapter._ari = fake_ari
    adapter.hangup_many_confirmed = confirmed
    adapter._complete_transfer_connection = unexpected_handoff
    transfer_task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000008",
        )
    )
    for _ in range(20):
        leg = adapter._transfers_by_parent.get(parent)
        if leg is not None:
            break
        await asyncio.sleep(0)

    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "BUSY",
        }
    )
    await asyncio.wait_for(cleanup_entered.wait(), timeout=1)
    assert leg.pending_failure["error"] == "busy"

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": leg.target_id, "state": "Up"},
        }
    )
    await asyncio.sleep(0)

    assert leg.connected is False
    assert handoff_calls == []
    assert not adapter._transfer_handoff_tasks
    assert not transfer_task.done()
    assert adapter._gateway_sessions[parent] == "gateway-session"
    assert adapter._ext_channels[parent] == "external-media"

    allow_cleanup.set()
    result = await asyncio.wait_for(transfer_task, timeout=1)
    assert result["status"] == "failed"
    assert result["error"] == "busy"


@pytest.mark.asyncio
async def test_termination_fence_rejects_transfer_before_target_creation():
    adapter, parent = _live_inbound_adapter()
    delete_entered = asyncio.Event()
    allow_delete = asyncio.Event()
    ari_paths = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        ari_paths.append(path)
        if method == "DELETE" and kwargs.get("return_status"):
            delete_entered.set()
            await allow_delete.wait()
            return 404, {}
        if path == "/channels/create":
            raise AssertionError("a fenced call must not create a transfer target")
        return {}

    adapter._ari = fake_ari
    hangup_task = asyncio.create_task(adapter.hangup_confirmed(parent))
    await asyncio.wait_for(delete_entered.wait(), timeout=1)

    result = await adapter.transfer(
        parent,
        "+15559876543",
        "blind",
        provider_leg_id="talky-xfer-00000000000000000009",
    )

    assert result["status"] == "failed"
    assert result["error"] == "call_terminating"
    assert "/channels/create" not in ari_paths
    assert parent not in adapter._transfers_by_parent

    allow_delete.set()
    assert await asyncio.wait_for(hangup_task, timeout=1) is True


@pytest.mark.asyncio
async def test_provider_leg_collision_across_parents_is_rejected_without_ari():
    adapter, first_parent = _live_inbound_adapter()
    second_parent = "inbound-parent-2"
    adapter._hangup_confirm_timeout_s = 0.01
    adapter._active_sessions[second_parent] = {
        "session_id": "gateway-session-2",
        "listen_port": 32002,
        "bridge_id": "bridge-2",
        "direction": "inbound",
    }
    adapter._gateway_sessions[second_parent] = "gateway-session-2"
    adapter._ext_channels[second_parent] = "external-media-2"
    adapter._bridges[second_parent] = "bridge-2"
    adapter._rtp_in_use.add(32002)
    adapter._inbound_admissions[second_parent] = {
        "allowed": True,
        "trunk_id": "tenant-trunk-id-2",
        "config_snapshot": {
            "route": {"sip_trunk_name": "customer-byo-trunk-2"},
        },
    }

    loop = asyncio.get_running_loop()
    from app.infrastructure.telephony.asterisk_adapter import _AsteriskTransferLeg

    target = "talky-xfer-0000000000000000000a"
    first_leg = _AsteriskTransferLeg(
        parent_id=first_parent,
        target_id=target,
        bridge_id="bridge-1",
        destination="+15559876543",
        endpoint="PJSIP/+15559876543@trunk-tenant-trunk-id",
        mode="blind",
        future=loop.create_future(),
        persisted_target_id=target,
    )
    adapter._transfers_by_parent[first_parent] = first_leg
    adapter._transfers_by_target[target] = first_leg

    async def unexpected_ari(*_args, **_kwargs):
        raise AssertionError("provider-leg collision must fail before ARI")

    adapter._ari = unexpected_ari
    result = await adapter.transfer(
        second_parent,
        "+15559876543",
        "blind",
        provider_leg_id=target,
    )

    assert result["status"] == "failed"
    assert result["error"] == "provider_leg_id_in_use"
    assert adapter._transfers_by_parent[first_parent] is first_leg
    assert adapter._transfers_by_target[target] is first_leg
    assert second_parent not in adapter._transfers_by_parent


@pytest.mark.asyncio
async def test_connected_target_hangup_ends_parent_logical_call():
    adapter, parent = _live_inbound_adapter()
    deleted = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        if method == "DELETE":
            deleted.append(path)
            if kwargs.get("return_status"):
                return 404, {}
        return {}

    loop = asyncio.get_running_loop()
    from app.infrastructure.telephony.asterisk_adapter import _AsteriskTransferLeg

    leg = _AsteriskTransferLeg(
        parent_id=parent,
        target_id="human-target",
        bridge_id="bridge-1",
        destination="+15559876543",
        endpoint="PJSIP/+15559876543@trunk-tenant-trunk-id",
        mode="blind",
        future=loop.create_future(),
        connected=True,
        target_in_bridge=True,
    )
    leg.future.set_result({"status": "completed"})
    adapter._transfers_by_parent[parent] = leg
    adapter._transfers_by_target[leg.target_id] = leg
    adapter._ari = fake_ari

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": leg.target_id},
            "cause": 16,
            "cause_txt": "Normal Clearing",
        }
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert f"/channels/{parent}" in deleted


@pytest.mark.asyncio
async def test_parent_terminal_defers_logical_end_until_transfer_target_absent():
    adapter, parent = _live_inbound_adapter()
    adapter._hangup_confirm_timeout_s = 0.02
    adapter._hangup_confirm_poll_s = 0.001
    adapter._inbound_cleanup_retry_s = 0
    deleted: list[str] = []
    ended: list[str] = []
    first_inventory = asyncio.Event()
    allow_absence = asyncio.Event()

    async def fake_ari(method, path, *, params=None, **kwargs):
        if method == "DELETE":
            deleted.append(path)
            if kwargs.get("return_status"):
                # ARI accepted both DELETEs, but that is not absence proof.
                return 204, {}
        return {}

    async def active_ids():
        first_inventory.set()
        return set() if allow_absence.is_set() else {"human-target"}

    async def fake_gateway(*_args, **_kwargs):
        return {}

    async def on_end(call_id):
        ended.append(call_id)

    loop = asyncio.get_running_loop()
    from app.infrastructure.telephony.asterisk_adapter import _AsteriskTransferLeg

    leg = _AsteriskTransferLeg(
        parent_id=parent,
        target_id="human-target",
        bridge_id="bridge-1",
        destination="+15559876543",
        endpoint="PJSIP/+15559876543@trunk-tenant-trunk-id",
        mode="blind",
        future=loop.create_future(),
        connected=True,
        target_in_bridge=True,
    )
    leg.future.set_result({"status": "completed"})
    adapter._transfers_by_parent[parent] = leg
    adapter._transfers_by_target[leg.target_id] = leg
    adapter._ari = fake_ari
    adapter._gateway = fake_gateway
    adapter.list_active_channel_ids = active_ids
    adapter._on_any_call_end = on_end

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": parent},
            "cause": 16,
            "cause_txt": "Normal Clearing",
        }
    )
    await asyncio.wait_for(first_inventory.wait(), timeout=1)
    await asyncio.sleep(0.03)

    assert ended == []
    assert adapter._transfers_by_parent[parent] is leg
    assert adapter._transfers_by_target[leg.target_id] is leg

    allow_absence.set()
    for _ in range(200):
        if not adapter._terminal_cleanup_tasks:
            break
        await asyncio.sleep(0.005)

    assert ended == [parent]
    assert parent not in adapter._transfers_by_parent
    assert leg.target_id not in adapter._transfers_by_target
    assert deleted.count(f"/channels/{leg.target_id}") >= 1


@pytest.mark.asyncio
async def test_failed_transfer_relationship_survives_204_until_parent_all_leg_proof():
    adapter, parent = _live_inbound_adapter()
    adapter._hangup_confirm_timeout_s = 0.02
    adapter._hangup_confirm_poll_s = 0.001
    adapter._inbound_cleanup_retry_s = 0
    target_present = True
    ended: list[str] = []

    async def fake_ari(method, path, *, params=None, **kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 204, {}
        return {}

    async def active_ids():
        return {leg.target_id} if target_present else set()

    async def fake_gateway(*_args, **_kwargs):
        return {}

    async def on_end(call_id):
        ended.append(call_id)

    adapter._ari = fake_ari
    adapter._gateway = fake_gateway
    adapter._on_any_call_end = on_end
    transfer_task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000004",
        )
    )
    for _ in range(20):
        leg = adapter._transfers_by_parent.get(parent)
        if leg is not None:
            break
        await asyncio.sleep(0)
    adapter.list_active_channel_ids = active_ids

    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "BUSY",
        }
    )
    result = await asyncio.wait_for(transfer_task, timeout=1)
    assert result["status"] == "cleanup_pending"
    await asyncio.sleep(0.03)
    assert adapter._transfers_by_parent[parent] is leg

    # The parent can end while target-only cleanup is retrying. The parent
    # terminal owner must still discover and prove the failed child leg.
    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": parent},
            "cause": 16,
            "cause_txt": "Normal Clearing",
        }
    )
    await asyncio.sleep(0.03)
    assert ended == []
    assert adapter._transfers_by_parent[parent] is leg

    target_present = False
    for _ in range(200):
        if not adapter._terminal_cleanup_tasks:
            break
        await asyncio.sleep(0.005)

    assert ended == [parent]
    assert parent not in adapter._transfers_by_parent
    assert not adapter._transfer_failure_cleanup_tasks


@pytest.mark.asyncio
async def test_asterisk_does_not_claim_unsupported_transfer_modes():
    adapter, parent = _live_inbound_adapter()
    for mode in ("attended", "deflect"):
        result = await adapter.transfer(parent, "1003", mode)
        assert result["status"] == "failed"
        assert result["error"] == "unsupported_transfer_mode"
