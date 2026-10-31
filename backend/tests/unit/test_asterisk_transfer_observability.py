"""Bounded, exactly-once observability for supervised Asterisk transfers."""

from __future__ import annotations

import asyncio

import pytest
from prometheus_client import REGISTRY

from app.infrastructure.metrics import inbound_metrics
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _adapter() -> tuple[AsteriskAdapter, str]:
    adapter = AsteriskAdapter()

    async def persist_answer(*_args):
        return 1

    adapter.set_transfer_answered_persist_callback(persist_answer)
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


def _capture_metrics(monkeypatch):
    captured = {
        "attempts": 0,
        "terminals": [],
        "cleanups": [],
        "inflight": [],
    }

    def attempt():
        captured["attempts"] += 1

    monkeypatch.setattr(inbound_metrics, "record_asterisk_transfer_attempt", attempt)
    monkeypatch.setattr(
        inbound_metrics,
        "record_asterisk_transfer_terminal",
        lambda outcome, reason, duration: captured["terminals"].append((outcome, reason, duration)),
    )
    monkeypatch.setattr(
        inbound_metrics,
        "record_asterisk_transfer_cleanup",
        lambda scope, result: captured["cleanups"].append((scope, result)),
    )
    monkeypatch.setattr(
        inbound_metrics,
        "set_asterisk_transfer_inflight",
        lambda value: captured["inflight"].append(value),
    )
    return captured


def _counter_value(name: str, **labels: str) -> float:
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == f"{name}_total" and sample.labels == labels:
                return sample.value
    return 0.0


def _histogram_count(name: str, **labels: str) -> float:
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == f"{name}_count" and sample.labels == labels:
                return sample.value
    return 0.0


async def _wait_for_leg(adapter: AsteriskAdapter, parent: str):
    for _ in range(50):
        leg = adapter._transfers_by_parent.get(parent)
        if leg is not None:
            return leg
        await asyncio.sleep(0)
    raise AssertionError("transfer leg was not registered")


@pytest.mark.asyncio
async def test_connected_transfer_records_one_attempt_and_one_terminal_outcome(
    monkeypatch,
):
    captured = _capture_metrics(monkeypatch)
    adapter, parent = _adapter()

    async def ari(method, path, *, params=None, **kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    async def gateway(*_args, **_kwargs):
        return {}

    adapter._ari = ari
    adapter._gateway = gateway
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000011",
        )
    )
    leg = await _wait_for_leg(adapter, parent)

    assert adapter.get_transfer_metrics() == {
        "attempts": 0,
        "successes": 0,
        "inflight": 1,
    }

    await adapter._handle_ari_event(
        {
            "type": "ChannelStateChange",
            "channel": {"id": leg.target_id, "state": "Up"},
        }
    )
    # Asterisk may deliver both answer signals. They describe one transition,
    # not two attempts or two terminal outcomes.
    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "ANSWER",
        }
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert result["status"] == "completed"
    assert captured["attempts"] == 1
    assert len(captured["terminals"]) == 1
    outcome, reason, duration = captured["terminals"][0]
    assert (outcome, reason) == ("connected", "answered")
    assert duration >= 0
    assert captured["inflight"] == [1, 0]
    assert adapter.get_transfer_metrics() == {
        "attempts": 1,
        "successes": 1,
        "inflight": 0,
    }

    # The linked conversation remains adapter-owned after handoff. Dropping it
    # at terminal cleanup changes only the gauge, never the completed outcome.
    adapter._drop_transfer_indexes(leg)
    adapter._drop_transfer_indexes(leg)
    assert captured["inflight"] == [1, 0, 0, 0]
    assert len(captured["terminals"]) == 1


@pytest.mark.asyncio
async def test_failed_target_records_cleanup_and_terminal_only_once(monkeypatch):
    captured = _capture_metrics(monkeypatch)
    adapter, parent = _adapter()

    async def ari(method, path, *, params=None, **kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        if method == "DELETE" and kwargs.get("return_status"):
            return 404, {}
        return {}

    adapter._ari = ari
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000012",
        )
    )
    leg = await _wait_for_leg(adapter, parent)

    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "BUSY",
        }
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert result["status"] == "failed"
    assert captured["attempts"] == 1
    assert captured["cleanups"] == [("target", "confirmed")]
    assert len(captured["terminals"]) == 1
    outcome, reason, duration = captured["terminals"][0]
    assert (outcome, reason) == ("failed", "busy")
    assert duration >= 0
    assert captured["inflight"][0] == 1
    assert captured["inflight"][-1] == 0
    assert adapter.get_transfer_metrics() == {
        "attempts": 1,
        "successes": 0,
        "inflight": 0,
    }

    # Duplicate terminal/drop paths are harmless.
    adapter._drop_transfer_indexes(leg)
    assert len(captured["terminals"]) == 1


@pytest.mark.asyncio
async def test_parent_cleanup_reports_linked_scope_and_balances_inflight(monkeypatch):
    captured = _capture_metrics(monkeypatch)
    adapter, parent = _adapter()

    async def ari(_method, path, *, params=None, **_kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        return {}

    async def confirmed(call_ids, *, fence_root=True):
        assert tuple(call_ids) == (
            parent,
            "talky-xfer-00000000000000000013",
        )
        assert fence_root is True
        return True

    adapter._ari = ari
    adapter.hangup_many_confirmed = confirmed
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000013",
        )
    )
    await _wait_for_leg(adapter, parent)

    assert await adapter._cleanup_transfer_for_parent(parent) is True
    result = await asyncio.wait_for(task, timeout=1)

    assert result["status"] == "failed"
    assert captured["attempts"] == 1
    assert captured["cleanups"] == [("linked", "confirmed")]
    assert [(item[0], item[1]) for item in captured["terminals"]] == [("failed", "caller_hung_up")]
    assert captured["inflight"][0] == 1
    assert captured["inflight"][-1] == 0


@pytest.mark.asyncio
async def test_unconfirmed_target_cleanup_stays_inflight_until_later_proof(monkeypatch):
    captured = _capture_metrics(monkeypatch)
    adapter, parent = _adapter()
    adapter._inbound_cleanup_retry_s = 0.01
    first_check = asyncio.Event()
    allow_confirmation = asyncio.Event()
    checks = 0

    async def ari(_method, path, *, params=None, **_kwargs):
        if path == "/channels/create":
            return {"id": params["channelId"]}
        return {}

    async def confirmed(call_ids, *, fence_root=False):
        nonlocal checks
        assert len(tuple(call_ids)) == 1
        assert fence_root is False
        checks += 1
        if checks == 1:
            first_check.set()
            return False
        await allow_confirmation.wait()
        return True

    adapter._ari = ari
    adapter.hangup_many_confirmed = confirmed
    task = asyncio.create_task(
        adapter.transfer(
            parent,
            "+15559876543",
            "blind",
            provider_leg_id="talky-xfer-00000000000000000014",
        )
    )
    leg = await _wait_for_leg(adapter, parent)
    await adapter._handle_ari_event(
        {
            "type": "Dial",
            "channel": {"id": parent},
            "peer": {"id": leg.target_id},
            "dialstatus": "NOANSWER",
        }
    )
    await asyncio.wait_for(first_check.wait(), timeout=1)
    pending_result = await asyncio.wait_for(task, timeout=1)

    assert pending_result["status"] == "cleanup_pending"
    assert captured["cleanups"] == [("target", "unconfirmed")]
    assert captured["terminals"] == []
    assert adapter.get_transfer_metrics() == {
        "attempts": 0,
        "successes": 0,
        "inflight": 1,
    }

    allow_confirmation.set()
    cleanup_owner = adapter._transfer_failure_cleanup_tasks[parent]
    await asyncio.wait_for(cleanup_owner, timeout=3)

    assert captured["cleanups"] == [
        ("target", "unconfirmed"),
        ("target", "confirmed"),
    ]
    assert [(item[0], item[1]) for item in captured["terminals"]] == [("failed", "noanswer")]
    assert captured["inflight"][-1] == 0


@pytest.mark.asyncio
async def test_preflight_rejection_is_not_a_provider_attempt(monkeypatch):
    captured = _capture_metrics(monkeypatch)
    adapter, parent = _adapter()

    result = await adapter.transfer(parent, "+15559876543", "warm")

    assert result["error"] == "unsupported_transfer_mode"
    assert captured == {
        "attempts": 0,
        "terminals": [],
        "cleanups": [],
        "inflight": [],
    }
    assert adapter.get_transfer_metrics() == {
        "attempts": 0,
        "successes": 0,
        "inflight": 0,
    }


def test_transfer_metric_labels_are_bounded(monkeypatch):
    class Labeled:
        def __init__(self):
            self.labels_seen = []
            self.values = []

        def labels(self, **labels):
            self.labels_seen.append(labels)
            return self

        def inc(self):
            self.values.append("inc")

        def observe(self, value):
            self.values.append(value)

    outcomes = Labeled()
    duration = Labeled()
    cleanup = Labeled()
    monkeypatch.setattr(inbound_metrics, "_asterisk_transfer_outcomes", outcomes)
    monkeypatch.setattr(inbound_metrics, "_asterisk_transfer_duration", duration)
    monkeypatch.setattr(inbound_metrics, "_asterisk_transfer_cleanup", cleanup)

    inbound_metrics.record_asterisk_transfer_terminal(
        "attacker-controlled-outcome",
        "tenant-or-destination-shaped-reason",
        -1,
    )
    inbound_metrics.record_asterisk_transfer_cleanup(
        "attacker-controlled-scope",
        "attacker-controlled-result",
    )

    assert outcomes.labels_seen == [{"outcome": "failed", "reason": "other"}]
    assert duration.labels_seen == [{"outcome": "failed", "reason": "other"}]
    assert duration.values == [0.0]
    assert cleanup.labels_seen == [{"scope": "linked", "result": "error"}]


def test_transfer_metrics_are_exported_to_the_prometheus_registry():
    terminal_labels = {"outcome": "failed", "reason": "no_answer"}
    cleanup_labels = {"scope": "target", "result": "unconfirmed"}
    attempts_before = _counter_value("asterisk_inbound_transfer_attempts")
    outcomes_before = _counter_value("asterisk_inbound_transfer_outcomes", **terminal_labels)
    duration_before = _histogram_count(
        "asterisk_inbound_transfer_duration_seconds", **terminal_labels
    )
    cleanup_before = _counter_value("asterisk_inbound_transfer_cleanup", **cleanup_labels)

    inbound_metrics.record_asterisk_transfer_attempt()
    inbound_metrics.record_asterisk_transfer_terminal("failed", "NOANSWER", 1.25)
    inbound_metrics.record_asterisk_transfer_cleanup("target", "unconfirmed")

    assert _counter_value("asterisk_inbound_transfer_attempts") == attempts_before + 1
    assert (
        _counter_value("asterisk_inbound_transfer_outcomes", **terminal_labels)
        == outcomes_before + 1
    )
    assert (
        _histogram_count("asterisk_inbound_transfer_duration_seconds", **terminal_labels)
        == duration_before + 1
    )
    assert (
        _counter_value("asterisk_inbound_transfer_cleanup", **cleanup_labels) == cleanup_before + 1
    )
