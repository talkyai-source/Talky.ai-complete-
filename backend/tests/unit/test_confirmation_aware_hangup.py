from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony import termination
from app.domain.services.telephony.termination import (
    finalize_proven_inbound_termination,
    load_active_provider_leg_ids,
    mark_termination_pending_and_load_context,
    request_confirmed_hangup,
)
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


def _adapter() -> AsteriskAdapter:
    adapter = AsteriskAdapter()
    adapter._hangup_confirm_timeout_s = 0.03
    adapter._hangup_confirm_poll_s = 0.001
    return adapter


@pytest.mark.parametrize(
    ("reason", "reason_code"),
    [
        ("unknown_did", 1),
        ("did_not_verified", 1),
        ("max_active_calls_reached", 17),
        ("admission_timeout", 42),
        ("subscription_inactive", 21),
        ("new_policy_denial", 21),
        ("after_hours_closed", None),
    ],
)
def test_inbound_denial_reason_mapping_is_explicit_and_fail_closed(reason, reason_code):
    assert AsteriskAdapter._inbound_denial_reason_code(reason) == reason_code


@pytest.mark.asyncio
async def test_reasoned_preanswer_hangup_retries_bare_in_same_iteration(monkeypatch):
    adapter = _adapter()
    requests: list[dict] = []

    async def ari(method, path, **kwargs):
        assert method == "DELETE"
        assert path == "/channels/denied-1"
        requests.append(kwargs)
        if len(requests) == 1:
            raise RuntimeError("older ARI rejected reason_code")
        return 404, {}

    async def must_not_list():
        raise AssertionError("bare DELETE 404 is authoritative absence proof")

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", must_not_list)

    assert await adapter.hangup_confirmed("denied-1", reason_code=42) is True
    assert requests[0]["params"] == {"reason_code": "42"}
    assert "params" not in requests[1]
    assert adapter._terminal_at_monotonic == {}
    assert adapter._terminal_at_utc == {}


@pytest.mark.asyncio
async def test_delete_acceptance_is_not_termination_while_channel_remains(monkeypatch):
    adapter = _adapter()

    async def ari(method, path, **_kwargs):
        assert method == "DELETE"
        assert path == "/channels/call-1"
        return 204, {}

    async def active():
        return {"call-1"}

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", active)

    assert await adapter.hangup_confirmed("call-1") is False


@pytest.mark.asyncio
async def test_delete_404_is_authoritative_already_absent_proof(monkeypatch):
    adapter = _adapter()

    async def ari(_method, _path, **_kwargs):
        return 404, {}

    async def must_not_list():
        raise AssertionError("404 must not need a second ARI query")

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", must_not_list)

    assert await adapter.hangup_confirmed("call-2") is True


@pytest.mark.asyncio
async def test_confirmation_waits_until_every_transfer_human_leg_is_absent(monkeypatch):
    adapter = _adapter()
    transfer = SimpleNamespace(parent_id="parent", target_id="target")
    adapter._transfers_by_parent["parent"] = transfer
    adapter._transfers_by_target["target"] = transfer
    deleted: list[str] = []
    inventories = iter([{"target"}, set()])

    async def ari(_method, path, **_kwargs):
        deleted.append(path.rsplit("/", 1)[-1])
        return 204, {}

    async def active():
        return next(inventories)

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", active)

    assert await adapter.hangup_confirmed("parent") is True
    assert deleted == ["parent", "target"]


@pytest.mark.asyncio
async def test_explicit_recovered_legs_share_one_confirmation_deadline(monkeypatch):
    adapter = _adapter()
    deleted: list[str] = []

    async def ari(_method, path, **_kwargs):
        deleted.append(path.rsplit("/", 1)[-1])
        return 204, {}

    async def active():
        return set()

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", active)

    assert (
        await adapter.hangup_many_confirmed(["recovered-parent", "recovered-transfer-target"])
        is True
    )
    assert deleted == ["recovered-parent", "recovered-transfer-target"]


@pytest.mark.asyncio
async def test_ari_failure_without_inventory_proof_is_unconfirmed(monkeypatch):
    adapter = _adapter()

    async def ari(_method, _path, **_kwargs):
        raise RuntimeError("ARI unavailable")

    async def active():
        return None

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", active)

    assert await adapter.hangup_confirmed("call-3") is False


@pytest.mark.asyncio
async def test_hangup_request_does_not_finalize_while_parent_channel_remains(monkeypatch):
    adapter = _adapter()
    adapter._inbound_cleanup_retry_s = 0
    call_id = "active-parent"
    parent_absent = False
    first_inventory = asyncio.Event()
    ended: list[str] = []
    adapter._active_sessions[call_id] = {
        "session_id": "gateway-1",
        "listen_port": 32000,
        "bridge_id": "bridge-1",
        "direction": "outbound",
    }
    adapter._gateway_sessions[call_id] = "gateway-1"
    adapter._bridges[call_id] = "bridge-1"

    async def ari(method, _path, **kwargs):
        if method == "DELETE" and kwargs.get("return_status"):
            return 204, {}
        return {}

    async def active():
        first_inventory.set()
        return set() if parent_absent else {call_id}

    async def gateway(*_args, **_kwargs):
        return {}

    async def on_end(value):
        ended.append(value)

    monkeypatch.setattr(adapter, "_ari", ari)
    monkeypatch.setattr(adapter, "list_active_channel_ids", active)
    monkeypatch.setattr(adapter, "_gateway", gateway)
    monkeypatch.setattr(adapter, "_release_rtp_port", gateway)
    adapter._on_any_call_end = on_end

    await adapter._handle_ari_event({"type": "ChannelHangupRequest", "channel": {"id": call_id}})
    await asyncio.wait_for(first_inventory.wait(), timeout=1)
    await asyncio.sleep(0.04)
    assert ended == []
    assert call_id in adapter._active_sessions

    parent_absent = True
    for _ in range(200):
        if not adapter._terminal_cleanup_tasks:
            break
        await asyncio.sleep(0.005)

    assert ended == [call_id]
    assert call_id not in adapter._active_sessions


@pytest.mark.asyncio
async def test_legacy_adapter_request_is_never_promoted_to_confirmation():
    calls: list[str] = []

    class LegacyAdapter:
        async def hangup(self, call_id: str) -> None:
            calls.append(call_id)

    proof = await request_confirmed_hangup(LegacyAdapter(), "legacy-call")

    assert calls == ["legacy-call"]
    assert proof.requested is True
    assert proof.confirmed is False
    assert proof.code == "confirmation_unsupported"


@pytest.mark.asyncio
async def test_persisted_linked_legs_use_one_multi_leg_confirmation_call():
    calls: list[tuple[str, ...]] = []

    class Adapter:
        async def hangup_many_confirmed(self, call_ids):
            calls.append(tuple(call_ids))
            return True

        async def hangup_confirmed(self, _call_id: str):
            raise AssertionError("linked legs must not use parent-only proof")

    proof = await request_confirmed_hangup(
        Adapter(),
        "parent",
        provider_leg_ids=("parent", "target-1", "target-1", "target-2"),
    )

    assert proof.confirmed is True
    assert calls == [("parent", "target-1", "target-2")]


@pytest.mark.asyncio
async def test_linked_legs_fail_closed_without_multi_leg_capability():
    calls: list[str] = []

    class ParentOnlyAdapter:
        async def hangup_confirmed(self, call_id: str):
            calls.append(call_id)
            return True

    proof = await request_confirmed_hangup(
        ParentOnlyAdapter(),
        "parent",
        provider_leg_ids=("target",),
    )

    assert proof.confirmed is False
    assert proof.requested is False
    assert proof.code == "linked_leg_confirmation_unsupported"
    assert calls == []


@pytest.mark.asyncio
async def test_load_active_provider_leg_ids_uses_durable_parent_lookup(monkeypatch):
    captured: dict[str, object] = {}

    class Conn:
        async def fetch(self, query, call_reference):
            captured["query"] = query
            captured["call_reference"] = call_reference
            return [
                {"provider_leg_id": "parent"},
                {"provider_leg_id": "talky-xfer-a"},
                {"provider_leg_id": "talky-xfer-a"},
                {"provider_leg_id": "  talky-xfer-b  "},
            ]

    @asynccontextmanager
    async def acquire(pool, tenant_id, *, timeout=None):
        captured["pool"] = pool
        captured["tenant_id"] = tenant_id
        captured["timeout"] = timeout
        yield Conn()

    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    pool = object()
    result = await load_active_provider_leg_ids(
        pool,
        call_reference="durable-call-id",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert result == ("parent", "talky-xfer-a", "talky-xfer-b")
    assert captured["pool"] is pool
    assert captured["call_reference"] == "durable-call-id"
    assert captured["tenant_id"] == "00000000-0000-0000-0000-000000000001"
    assert "c.id::text=$1" in str(captured["query"])
    assert "c.provider_call_id=$1" in str(captured["query"])
    assert "c.external_call_uuid=$1" in str(captured["query"])
    assert "'initiated','ringing','answered'" in str(captured["query"])


@pytest.mark.asyncio
async def test_termination_context_fences_before_snapshotting_linked_legs(monkeypatch):
    events: list[str] = []

    class Conn:
        async def fetchrow(self, query, call_reference):
            assert "FOR UPDATE" in query
            assert call_reference == "durable-call"
            events.append("lock")
            return {
                "call_id": "00000000-0000-0000-0000-000000000010",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
                "provider_call_id": "parent-provider",
                "status": "in_call",
                "provider": "asterisk",
                "direction": "inbound",
                "campaign_id": None,
                "answered_at": None,
            }

        async def execute(self, query, *_args):
            assert "status='termination_pending'" in query
            events.append("fence")

        async def fetch(self, query, *_args):
            assert "FROM call_legs" in query
            events.append("legs")
            return [
                {"provider_leg_id": "talky-xfer-one"},
                {"provider_leg_id": "talky-xfer-one"},
                {"provider_leg_id": "talky-xfer-two"},
            ]

    @asynccontextmanager
    async def acquire(pool, tenant_id, *, timeout=None):
        assert pool is sentinel_pool
        assert tenant_id == "00000000-0000-0000-0000-000000000001"
        assert timeout == 5.0
        yield Conn()

    sentinel_pool = object()
    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)

    context = await mark_termination_pending_and_load_context(
        sentinel_pool,
        call_reference="durable-call",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert events == ["lock", "fence", "legs"]
    assert context.provider_call_id == "parent-provider"
    assert context.previous_status == "in_call"
    assert context.provider_leg_ids == ("talky-xfer-one", "talky-xfer-two")


@pytest.mark.asyncio
async def test_proven_inbound_finalizer_commits_children_parent_global_then_ack(
    monkeypatch,
):
    import app.domain.services.global_concurrency as concurrency
    import app.domain.services.telephony.inbound_admission as admission
    import app.domain.services.telephony.inbound_transfer as transfers
    import app.domain.services.telephony.state_backend as state_backend

    order: list[str] = []

    class State:
        async def register_cleanup_obligation(self, call_id, **kwargs):
            assert call_id == "provider-parent"
            assert kwargs["state"] == "termination_pending"
            order.append("ledger")

        async def acknowledge_orphan_recovery(self, call_id):
            assert call_id == "provider-parent"
            order.append("ack")

    async def finalize_children(_pool, **kwargs):
        assert kwargs["call_id"] == "durable-parent"
        order.append("children")
        return 1

    async def finalize_parent(_self, request):
        assert request.call_id == "durable-parent"
        assert request.provider_call_id == "provider-parent"
        order.append("parent")

    async def release_global(_redis, *, call_id):
        assert call_id == "provider-parent"
        order.append("global")

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    monkeypatch.setattr(
        transfers,
        "finalize_connected_inbound_transfers",
        finalize_children,
    )
    monkeypatch.setattr(
        admission.InboundAdmissionService,
        "finalize",
        finalize_parent,
    )
    monkeypatch.setattr(concurrency, "release_lease_strict", release_global)
    monkeypatch.setattr(
        termination,
        "_load_persisted_inbound_terminal_duration",
        AsyncMock(return_value=17),
    )

    await finalize_proven_inbound_termination(
        object(),
        provider_call_id="provider-parent",
        durable_call_id="durable-parent",
        tenant_id="tenant-1",
        terminal_status="ended",
        reason="operator_hangup",
        redis_client=object(),
    )

    assert order == ["ledger", "children", "parent", "global", "ack"]


def test_proven_finalizer_has_no_caller_supplied_settlement_escape_hatch():
    parameters = inspect.signature(finalize_proven_inbound_termination).parameters
    assert "duration_seconds" not in parameters
    assert "release_only" not in parameters


@pytest.mark.asyncio
async def test_stale_preanswer_snapshot_is_held_instead_of_released(monkeypatch):
    """A caller snapshot cannot release a call while ARI Answer is persisting.

    The durable read happens after PBX absence proof.  If it still cannot prove
    answer-to-hangup duration, settlement must enter the carrier/CDR hold path;
    a zero-second release would permanently underbill an answered call when the
    Answer write commits immediately afterwards.
    """

    import app.domain.services.global_concurrency as concurrency
    import app.domain.services.telephony.inbound_admission as admission
    import app.domain.services.telephony.inbound_transfer as transfers
    import app.domain.services.telephony.state_backend as state_backend

    finalized = AsyncMock()
    released = AsyncMock()

    class State:
        async def register_cleanup_obligation(self, *_args, **_kwargs):
            return None

        async def acknowledge_orphan_recovery(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    monkeypatch.setattr(
        termination,
        "_load_persisted_inbound_terminal_duration",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        transfers,
        "finalize_connected_inbound_transfers",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(admission.InboundAdmissionService, "finalize", finalized)
    monkeypatch.setattr(admission.InboundAdmissionService, "release", released)
    monkeypatch.setattr(concurrency, "release_lease_strict", AsyncMock())

    await finalize_proven_inbound_termination(
        object(),
        provider_call_id="provider-parent",
        durable_call_id="durable-parent",
        tenant_id="tenant-1",
        terminal_status="ended",
        reason="tenant_operator_hangup_before_answer",
        redis_client=object(),
    )

    released.assert_not_awaited()
    finalized.assert_awaited_once()
    request = finalized.await_args.args[-1]
    assert request.duration_seconds == 0
    assert request.reason == "process_restart_answer_ambiguous"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_duration", "expected_duration"),
    [(5, 5), (0, None)],
)
async def test_persisted_terminal_duration_requires_new_provider_proof(
    monkeypatch,
    persisted_duration,
    expected_duration,
):
    terminated_at = datetime(2026, 9, 2, 12, 0, 5, tzinfo=timezone.utc)

    class Conn:
        async def fetchrow(self, query, *args):
            normalized = " ".join(query.split())
            assert "answered_at IS NOT NULL" not in normalized
            assert "provider_terminated_at IS NOT NULL" not in normalized
            assert "NOW()" not in normalized
            assert args == ("durable-parent", "provider-parent")
            return {
                "answered_at": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
                "provider_terminated_at": terminated_at,
                "duration_seconds": persisted_duration,
                "reserved_seconds": 60,
            }

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_kwargs):
        assert pool is sentinel_pool
        assert tenant_id == "tenant-1"
        yield Conn()

    sentinel_pool = object()
    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)

    if expected_duration is None:
        with pytest.raises(RuntimeError, match="duration is invalid"):
            await termination._load_persisted_inbound_terminal_duration(
                sentinel_pool,
                durable_call_id="durable-parent",
                provider_call_id="provider-parent",
                tenant_id="tenant-1",
            )
    else:
        duration = await termination._load_persisted_inbound_terminal_duration(
            sentinel_pool,
            durable_call_id="durable-parent",
            provider_call_id="provider-parent",
            tenant_id="tenant-1",
        )
        assert duration == expected_duration


@pytest.mark.asyncio
async def test_persisted_terminal_duration_marks_inflight_answer_as_ambiguous(
    monkeypatch,
):
    class Conn:
        async def fetchrow(self, query, *args):
            normalized = " ".join(query.split())
            assert "answered_at IS NOT NULL" not in normalized
            assert "provider_terminated_at IS NOT NULL" not in normalized
            assert "NOW()" not in normalized
            assert args == ("durable-parent", "provider-parent")
            return {
                "answered_at": None,
                "provider_terminated_at": datetime(
                    2026, 9, 2, 12, 0, 5, tzinfo=timezone.utc
                ),
                "duration_seconds": 0,
                "reserved_seconds": 60,
            }

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_kwargs):
        assert pool is sentinel_pool
        assert tenant_id == "tenant-1"
        yield Conn()

    sentinel_pool = object()
    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)

    duration = await termination._load_persisted_inbound_terminal_duration(
        sentinel_pool,
        durable_call_id="durable-parent",
        provider_call_id="provider-parent",
        tenant_id="tenant-1",
    )

    assert duration is None


@pytest.mark.asyncio
async def test_missing_terminal_proof_retains_cleanup_ledger_before_settlement(
    monkeypatch,
):
    import app.domain.services.global_concurrency as concurrency
    import app.domain.services.telephony.inbound_admission as admission
    import app.domain.services.telephony.inbound_transfer as transfers
    import app.domain.services.telephony.state_backend as state_backend

    order: list[str] = []

    class State:
        async def register_cleanup_obligation(self, _call_id, **_kwargs):
            order.append("ledger")

        async def acknowledge_orphan_recovery(self, _call_id):
            order.append("ack")

    async def unexpected(*_args, **_kwargs):
        order.append("unexpected")

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    monkeypatch.setattr(
        termination,
        "_load_persisted_inbound_terminal_duration",
        AsyncMock(side_effect=RuntimeError("missing provider terminal proof")),
    )
    monkeypatch.setattr(transfers, "finalize_connected_inbound_transfers", unexpected)
    monkeypatch.setattr(admission.InboundAdmissionService, "finalize", unexpected)
    monkeypatch.setattr(concurrency, "release_lease_strict", unexpected)

    with pytest.raises(RuntimeError, match="missing provider terminal proof"):
        await finalize_proven_inbound_termination(
            object(),
            provider_call_id="provider-parent",
            durable_call_id="durable-parent",
            tenant_id="tenant-1",
            terminal_status="ended",
            reason="operator_hangup",
            redis_client=object(),
        )

    assert order == ["ledger"]


@pytest.mark.asyncio
async def test_proven_inbound_finalizer_retains_ledger_when_child_commit_fails(
    monkeypatch,
):
    import app.domain.services.global_concurrency as concurrency
    import app.domain.services.telephony.inbound_admission as admission
    import app.domain.services.telephony.inbound_transfer as transfers
    import app.domain.services.telephony.state_backend as state_backend

    order: list[str] = []

    class State:
        async def register_cleanup_obligation(self, _call_id, **_kwargs):
            order.append("ledger")

        async def acknowledge_orphan_recovery(self, _call_id):
            order.append("ack")

    async def fail_children(*_args, **_kwargs):
        order.append("children_failed")
        raise RuntimeError("transfer lease database unavailable")

    async def unexpected(*_args, **_kwargs):
        order.append("unexpected")

    monkeypatch.setattr(state_backend, "get_state_backend", lambda: State())
    monkeypatch.setattr(
        transfers,
        "finalize_connected_inbound_transfers",
        fail_children,
    )
    monkeypatch.setattr(admission.InboundAdmissionService, "finalize", unexpected)
    monkeypatch.setattr(concurrency, "release_lease_strict", unexpected)
    monkeypatch.setattr(
        termination,
        "_load_persisted_inbound_terminal_duration",
        AsyncMock(return_value=17),
    )

    with pytest.raises(RuntimeError, match="transfer lease database unavailable"):
        await finalize_proven_inbound_termination(
            object(),
            provider_call_id="provider-parent",
            durable_call_id="durable-parent",
            tenant_id="tenant-1",
            terminal_status="ended",
            reason="operator_hangup",
            redis_client=object(),
        )

    assert order == ["ledger", "children_failed"]
