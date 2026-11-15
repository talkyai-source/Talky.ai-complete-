"""Answer-proved supervised transfer for OUTBOUND calls (switch-gated)."""

import asyncio
import re

import pytest

from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
from tests.unit.test_asterisk_supervised_transfer import _live_inbound_adapter

SWITCH = "OUTBOUND_SUPERVISED_TRANSFER_ENABLED"
DEST = "+442071234567"
TRUNK = "carrier-uk.1_a"
LEG_ID = re.compile(r"talky-xfer-[0-9a-f]{20}")


def _live_outbound_adapter():
    adapter = AsteriskAdapter()
    parent = "outbound-parent"
    adapter._connected_flag = True
    adapter._active_sessions[parent] = {
        "session_id": "gateway-session",
        "listen_port": 32002,
        "bridge_id": "bridge-out",
        "direction": "outbound",
    }
    adapter._gateway_sessions[parent] = "gateway-session"
    adapter._ext_channels[parent] = "external-media"
    adapter._bridges[parent] = "bridge-out"
    adapter._rtp_in_use.add(32002)
    return adapter, parent


class _Rig:
    def __init__(self, adapter, parent, *, create_id=None):
        self.adapter = adapter
        self.parent = parent
        self.ari = []
        self.gateway = []
        self.create_id = create_id
        adapter._ari = self._ari
        adapter._gateway = self._gw

    async def _ari(self, method, path, *, params=None, **kwargs):
        self.ari.append((method, path, dict(params or {})))
        if path == "/channels/create":
            if self.create_id:
                return {"id": self.create_id}
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def _gw(self, method, path, *, payload=None, **kwargs):
        self.gateway.append((method, path, dict(payload or {})))
        return {}

    def calls(self, path):
        return [c for c in self.ari if c[1] == path]

    async def start(self, **kwargs):
        task = asyncio.create_task(
            self.adapter.transfer(self.parent, kwargs.pop("dest", DEST), "blind", **kwargs)
        )
        for _ in range(30):
            if self.adapter._transfers_by_parent.get(self.parent) is not None:
                break
            await asyncio.sleep(0)
        for _ in range(5):
            await asyncio.sleep(0)
        return task

    async def busy(self, task):
        leg = self.adapter._transfers_by_parent[self.parent]
        await self.adapter._handle_ari_event(
            {
                "type": "Dial",
                "channel": {"id": self.parent},
                "peer": {"id": leg.target_id},
                "dialstatus": "BUSY",
            }
        )
        return await asyncio.wait_for(task, timeout=1)


def _register_callbacks(adapter):
    seen = {"answered": [], "identity": [], "cleanup": [], "connected": []}

    async def answered(*a):
        seen["answered"].append(a)
        return 1

    async def identity(*a):
        seen["identity"].append(a)
        return a[2]

    async def cleanup(*a):
        seen["cleanup"].append(a)

    async def connected(*a):
        seen["connected"].append(a)

    adapter.set_transfer_answered_persist_callback(answered)
    adapter.set_transfer_provider_identity_persist_callback(identity)
    adapter.set_transfer_cleanup_confirmed_callback(cleanup)
    adapter.set_transfer_connected_callback(connected)
    return seen


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(SWITCH, "true")


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)


@pytest.mark.asyncio
async def test_switch_off_e164_still_redirects(switch_off):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, DEST, "blind", trunk_endpoint=TRUNK)
    assert rig.ari == [("POST", f"/channels/{parent}/redirect", {"endpoint": f"PJSIP/{DEST}"})]
    assert result == {
        "status": "success",
        "call_id": parent,
        "destination": DEST,
        "mode": "blind",
    }


@pytest.mark.asyncio
async def test_switch_off_no_trunk_still_redirects(switch_off):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, DEST)
    assert result["status"] == "success"
    assert rig.ari == [("POST", f"/channels/{parent}/redirect", {"endpoint": f"PJSIP/{DEST}"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("dest", ["sales", "1001", "ext-200"])
async def test_switch_on_extension_destination_still_redirects(switch_on, dest):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, dest, "blind", trunk_endpoint=TRUNK)
    assert result["status"] == "success"
    assert rig.ari == [("POST", f"/channels/{parent}/redirect", {"endpoint": f"PJSIP/{dest}"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["attended", "warm"])
async def test_switch_on_non_blind_mode_still_redirects(switch_on, mode):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, DEST, mode, trunk_endpoint=TRUNK)
    assert result["status"] == "success"
    assert rig.ari == [("POST", f"/channels/{parent}/redirect", {"endpoint": f"PJSIP/{DEST}"})]


@pytest.mark.asyncio
async def test_switch_on_e164_with_trunk_waits_for_answer_and_hands_off(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    seen = _register_callbacks(adapter)

    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=12)
    leg = adapter._transfers_by_parent[parent]
    assert LEG_ID.fullmatch(leg.target_id)
    assert not task.done()
    create = rig.calls("/channels/create")[0][2]
    assert create["endpoint"] == f"PJSIP/{DEST}@{TRUNK}"
    assert create["channelId"] == leg.target_id
    assert create["appArgs"] == f"transfer_target,{parent}"
    assert "callerId" not in create
    dial = rig.calls(f"/channels/{leg.target_id}/dial")[0][2]
    assert dial["timeout"] == 12
    assert not any(p.endswith("/redirect") for _, p, _ in rig.ari)
    # Caller stays with the AI while the target rings.
    assert not rig.calls("/bridges/bridge-out/addChannel")
    assert not rig.calls("/bridges/bridge-out/removeChannel")
    assert rig.gateway == []
    assert adapter._gateway_sessions[parent] == "gateway-session"

    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert result["status"] == "completed"
    assert result["answered"] is True and result["handoff_confirmed"] is True
    assert result["target_call_id"] == leg.target_id
    assert rig.gateway[-1][2]["reason"] == "transfer_connected"
    assert parent not in adapter._gateway_sessions
    assert parent not in adapter._ext_channels
    detach = next(
        i
        for i, (_, p, q) in enumerate(rig.ari)
        if p == "/bridges/bridge-out/removeChannel" and q.get("channel") == "external-media"
    )
    add = next(
        i
        for i, (_, p, q) in enumerate(rig.ari)
        if p == "/bridges/bridge-out/addChannel" and q.get("channel") == leg.target_id
    )
    assert detach < add
    assert seen["answered"] == [] and seen["identity"] == [] and seen["cleanup"] == []
    assert seen["connected"] == [(parent, leg.target_id)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ring", "expected"), [(12, 12), (500, 120), (1, 5)])
async def test_ring_seconds_is_per_leg_and_clamped(switch_on, ring, expected):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=ring)
    leg = adapter._transfers_by_parent[parent]
    assert rig.calls(f"/channels/{leg.target_id}/dial")[0][2]["timeout"] == expected
    await rig.busy(task)


@pytest.mark.asyncio
async def test_no_ring_seconds_uses_process_wide_value(switch_on):
    adapter, parent = _live_outbound_adapter()
    adapter._transfer_answer_timeout_s = 41.0
    rig = _Rig(adapter, parent)
    task = await rig.start(trunk_endpoint=TRUNK)
    leg = adapter._transfers_by_parent[parent]
    assert rig.calls(f"/channels/{leg.target_id}/dial")[0][2]["timeout"] == 41
    await rig.busy(task)


@pytest.mark.asyncio
async def test_wait_for_result_uses_leg_ring_seconds(switch_on, monkeypatch):
    adapter, parent = _live_outbound_adapter()
    adapter._transfer_answer_timeout_s = 90.0
    adapter._hangup_confirm_timeout_s = 0.05
    _Rig(adapter, parent)
    seen_timeouts = []
    real_wait_for = asyncio.wait_for

    async def spy(aw, timeout=None):
        seen_timeouts.append(timeout)
        # Shrink the long wait so the test does not sleep for real.
        return await real_wait_for(aw, 0.01 if timeout and timeout > 5 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy)
    result = await adapter.transfer(parent, DEST, "blind", trunk_endpoint=TRUNK, ring_seconds=5)
    assert 7.0 in seen_timeouts  # 5 s ring + 2 s grace, not 90 + 2
    assert 92.0 not in seen_timeouts
    assert result["status"] in {"failed", "cleanup_pending"}


@pytest.mark.asyncio
async def test_caller_id_is_passed_only_when_given(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    task = await rig.start(trunk_endpoint=TRUNK, caller_id="+442046132300")
    assert rig.calls("/channels/create")[0][2]["callerId"] == "+442046132300"
    await rig.busy(task)


@pytest.mark.asyncio
async def test_empty_caller_id_is_ignored(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    task = await rig.start(trunk_endpoint=TRUNK, caller_id="  ")
    assert "callerId" not in rig.calls("/channels/create")[0][2]
    await rig.busy(task)


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialstatus", "error"), [("BUSY", "busy"), ("NOANSWER", "noanswer")])
async def test_busy_and_no_answer_keep_caller_and_allow_retry(switch_on, dialstatus, error):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    seen = _register_callbacks(adapter)
    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=10)
    leg = adapter._transfers_by_parent[parent]
    first_id = leg.target_id
    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": first_id},
            "dialstatus": dialstatus,
        }
    )
    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "failed"
    assert result["error"] == error
    assert result["target_termination_confirmed"] is True
    assert result["caller_media_retained"] is True
    assert ("DELETE", f"/channels/{first_id}") in [(m, p) for m, p, _ in rig.ari]
    assert adapter._gateway_sessions[parent] == "gateway-session"
    assert rig.gateway == []
    assert seen["answered"] == [] and seen["cleanup"] == [] and seen["identity"] == []
    for _ in range(20):
        if parent not in adapter._transfers_by_parent:
            break
        await asyncio.sleep(0)
    assert parent not in adapter._transfers_by_parent

    second = await rig.start(trunk_endpoint=TRUNK)
    leg2 = adapter._transfers_by_parent[parent]
    assert leg2.target_id != first_id
    assert (await rig.busy(second))["error"] == "busy"


@pytest.mark.asyncio
async def test_switch_on_e164_without_trunk_fails_without_ari(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, DEST, "blind")
    assert result["status"] == "failed"
    assert result["error"] == "outbound_trunk_unavailable"
    assert rig.ari == []


@pytest.mark.asyncio
@pytest.mark.parametrize("trunk", ["carrier/../x", "bad trunk", "a@b", "x" * 81, "tr;unk"])
async def test_invalid_trunk_endpoint_fails_without_ari(switch_on, trunk):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(parent, DEST, "blind", trunk_endpoint=trunk)
    assert result["status"] == "failed"
    assert result["error"] == "invalid_trunk_endpoint"
    assert rig.ari == []


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_id", ["12345", "+0123456789", "abc", "+44 20 7123"])
async def test_invalid_caller_id_fails_without_ari(switch_on, caller_id):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    result = await adapter.transfer(
        parent, DEST, "blind", trunk_endpoint=TRUNK, caller_id=caller_id
    )
    assert result["status"] == "failed"
    assert result["error"] == "invalid_caller_id"
    assert rig.ari == []


@pytest.mark.asyncio
async def test_second_transfer_while_in_progress_is_refused(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    task = await rig.start(trunk_endpoint=TRUNK)
    second = await adapter.transfer(parent, DEST, "blind", trunk_endpoint=TRUNK)
    assert second["status"] == "failed"
    assert second["error"] == "transfer_already_in_progress"
    assert len(rig.calls("/channels/create")) == 1
    await rig.busy(task)


@pytest.mark.asyncio
async def test_returned_id_mismatch_fails_cleans_up_without_db_callback(switch_on):
    adapter, parent = _live_outbound_adapter()
    actual = "asterisk-actual-transfer-target"
    rig = _Rig(adapter, parent, create_id=actual)
    seen = _register_callbacks(adapter)
    result = await adapter.transfer(parent, DEST, "blind", trunk_endpoint=TRUNK)
    for _ in range(50):
        if not adapter._unclaimed_hangup_tasks:
            break
        await asyncio.sleep(0)
    assert result["status"] == "failed"
    assert result["error"] == "provider_leg_id_mismatch"
    assert seen["identity"] == [] and seen["answered"] == [] and seen["cleanup"] == []
    paths = [p for _, p, _ in rig.ari]
    assert f"/channels/{actual}" in paths
    assert f"/channels/{actual}/dial" not in paths
    assert "/bridges/bridge-out/addChannel" not in paths
    assert f"/channels/{parent}" not in paths


@pytest.mark.asyncio
async def test_inbound_unchanged_with_switch_on(switch_on):
    adapter, parent = _live_inbound_adapter()
    rig = _Rig(adapter, parent)
    seen = _register_callbacks(adapter)
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000009",
        )
    )
    for _ in range(35):
        await asyncio.sleep(0)
    leg = adapter._transfers_by_parent[parent]
    create = rig.calls("/channels/create")[0][2]
    assert create == {
        "endpoint": "PJSIP/+15559876543@trunk-tenant-trunk-id",
        "app": adapter._app_name,
        "appArgs": f"transfer_target,{parent}",
        "channelId": "talky-xfer-00000000000000000009",
        "originator": parent,
        "formats": "ulaw",
    }
    assert rig.calls(f"/channels/{leg.target_id}/dial")[0][2]["timeout"] == int(
        adapter._transfer_answer_timeout_s
    )
    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "completed"
    assert seen["answered"] == [(parent, leg.target_id)]
    assert seen["connected"] == [(parent, leg.target_id)]


@pytest.mark.asyncio
async def test_inbound_ignores_outbound_only_arguments(switch_on):
    adapter, parent = _live_inbound_adapter()
    rig = _Rig(adapter, parent)
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000010",
            trunk_endpoint="other-trunk",
            ring_seconds=7,
            caller_id="+442046132300",
        )
    )
    for _ in range(35):
        await asyncio.sleep(0)
    leg = adapter._transfers_by_parent[parent]
    create = rig.calls("/channels/create")[0][2]
    assert create["endpoint"] == "PJSIP/+15559876543@trunk-tenant-trunk-id"
    assert "callerId" not in create
    assert rig.calls(f"/channels/{leg.target_id}/dial")[0][2]["timeout"] == int(
        adapter._transfer_answer_timeout_s
    )
    await rig.busy(task)


# -- lead review addition -------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("abc", None), ("", None), (True, None), ([], None), ("30", 30.0), (12.9, 12.0), (0, 5.0), (999, 120.0)],
)
def test_ring_time_helper_never_raises_and_keeps_the_limits(value, expected):
    from app.infrastructure.telephony.asterisk_adapter import _clamped_ring_seconds

    assert _clamped_ring_seconds(value) == expected


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("", False), ("0", False), ("off", False)])
def test_adapter_reports_whether_a_phone_number_transfer_can_be_proved(monkeypatch, value, expected):
    monkeypatch.setenv(SWITCH, value)
    assert AsteriskAdapter().outbound_pstn_transfer_available is expected


def test_adapter_reports_not_available_when_the_switch_is_unset(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    assert AsteriskAdapter().outbound_pstn_transfer_available is False


# ------------------------------- the two phone legs end together (outbound, non-durable)
async def _connected_outbound(switch_on):
    adapter, parent = _live_outbound_adapter()
    rig = _Rig(adapter, parent)
    _register_callbacks(adapter)
    task = await rig.start(trunk_endpoint=TRUNK, ring_seconds=12)
    leg = adapter._transfers_by_parent[parent]
    await adapter._handle_ari_event(
        {"type": "ChannelStateChange", "channel": {"id": leg.target_id, "state": "Up"}}
    )
    assert (await asyncio.wait_for(task, timeout=1))["status"] == "completed"
    return adapter, parent, rig, leg


@pytest.mark.asyncio
async def test_a_connected_outbound_leg_stays_owned_and_hanging_up_the_parent_hangs_up_both(switch_on):
    adapter, parent, rig, leg = await _connected_outbound(switch_on)
    assert leg.durable is False and leg.connected is True
    assert adapter._transfers_by_parent[parent] is leg
    assert adapter._transfers_by_target[leg.target_id] is leg

    assert await adapter.hangup_confirmed(parent) is True

    deleted = [path for method, path, _ in rig.ari if method == "DELETE"]
    assert f"/channels/{parent}" in deleted
    assert f"/channels/{leg.target_id}" in deleted


@pytest.mark.asyncio
async def test_the_connected_outbound_target_ending_ends_the_parent(switch_on):
    adapter, parent, rig, leg = await _connected_outbound(switch_on)

    await adapter._handle_ari_event(
        {
            "type": "ChannelDestroyed",
            "channel": {"id": leg.target_id},
            "cause": 16,
            "cause_txt": "Normal Clearing",
        }
    )
    for _ in range(10):
        await asyncio.sleep(0)

    deleted = [path for method, path, _ in rig.ari if method == "DELETE"]
    assert f"/channels/{parent}" in deleted
