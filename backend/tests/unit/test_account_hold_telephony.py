"""Formal account holds at actual call-owner/provider boundaries; synthetic I/O."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.domain.services.telephony.termination_receipts import TerminationReceiptResult
from fastapi import HTTPException

from app.domain.services.telephony import lifecycle


@pytest.mark.asyncio
async def test_hold_committed_during_warmup_prevents_actual_origination(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as bridge
    from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _campaign

    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()])
    checks = []

    async def held(*_args, **_kwargs):
        checks.append("hold")
        raise HTTPException(409, detail={"error": "call_authority_denied"})

    monkeypatch.setattr(bridge, "_require_call_authority", held, raising=False)
    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)
    assert error.value.status_code == 409
    assert checks == ["hold"]
    assert "prewarm" in path.events
    assert "originate" not in path.events


@pytest.mark.asyncio
async def test_hold_during_answer_intent_write_prevents_actual_answer(monkeypatch):
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
    from app.domain.services.telephony import state_backend
    from tests.unit.test_asterisk_preanswer_admission import _event, _allowed
    from tests.unit.pbx_identity_helpers import INFO

    adapter = AsteriskAdapter()
    adapter._control_backend_system = INFO["system"]["entity_id"]
    adapter._control_backend_startup = INFO["status"]["startup_time"]
    adapter._verify_control_backend = AsyncMock()
    held = False
    effects = []

    async def intent(*_args, **_kwargs):
        nonlocal held
        held = True

    async def authority(*_args, **_kwargs):
        if held:
            raise RuntimeError("formal_account_hold")

    async def ari(method, path, **_kwargs):
        effects.append((method, path))
        raise RuntimeError("synthetic stop after first effect")

    monkeypatch.setattr(lifecycle, "_require_call_authority", authority, raising=False)
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: SimpleNamespace(
        register_answer_intent_cleanup_obligation=intent))
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    adapter.set_inbound_admission_finalizer(AsyncMock())
    adapter.set_inbound_answered_persist_callback(AsyncMock())
    adapter.set_inbound_terminal_proof_persist_callback(AsyncMock())
    adapter._ari = ari
    adapter._cleanup_failed_inbound_setup_now_or_schedule = AsyncMock()
    await adapter._on_stasis_start("inbound-1", _event())
    assert held
    assert ("POST", "/channels/inbound-1/answer") not in effects
    adapter._cleanup_failed_inbound_setup_now_or_schedule.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_hold_during_originate_retains_real_cleanup_outcome(monkeypatch, confirmed):
    from app.api.v1.endpoints import telephony_bridge as bridge
    from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _campaign
    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()], hangup_confirmed=confirmed)
    async def authority(_tenant, **kwargs):
        if kwargs.get("existing_call_id"):
            raise HTTPException(409, detail={"error": "call_authority_denied"})
    monkeypatch.setattr(bridge, "_require_call_authority", authority)
    with pytest.raises(HTTPException) as error:
        await bridge.make_call(path.request, path.body)
    assert "originate" in path.events
    assert "termination_pending" in path.events
    if confirmed:
        assert error.value.status_code == 409
        assert "fail_call" in path.events
    else:
        assert error.value.detail["provider_hangup_confirmed"] is False
        assert "fail_call" not in path.events


@pytest.mark.asyncio
async def test_existing_intent_receipt_replay_does_not_dispatch_or_need_new_admission(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as bridge
    from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _internal_intent_row, _campaign
    path = _install_call_path(monkeypatch, campaign_rows=[_campaign()], internal_intent_row=_internal_intent_row(
        status="dialing", provider_call_id="already-owned-channel"))
    authority = AsyncMock(side_effect=AssertionError("replay is not a new provider effect"))
    monkeypatch.setattr(bridge, "_require_call_authority", authority)
    result = await bridge.make_call(path.request, path.body)
    assert result.status_code == 200
    authority.assert_not_awaited()
    assert "originate" not in path.events


def call_row(**updates):
    from tests.unit.test_call_guard_formal_authority import TENANT, NOW
    from tests.unit.pbx_identity_helpers import BINDING
    return {"id": "00000000-0000-0000-0000-000000000601", "tenant_id": TENANT,
            "provider": "asterisk", "provider_call_id": "owned-channel", "external_call_uuid": "planned-channel",
            "answered_at": None, "started_at": NOW, "route_snapshot": {"control_backend": BINDING},
            "campaign_id": None, "direction": "outbound", "status": "in_progress", **updates}


def acknowledged_row():
    from datetime import timedelta
    from tests.unit.test_call_guard_formal_authority import NOW
    row = call_row()
    row["route_snapshot"]["provider_acknowledgement"] = {
        "provider": row["provider"], "provider_call_id": row["provider_call_id"],
        "control_backend": row["route_snapshot"]["control_backend"],
        "acknowledged_at": (NOW - timedelta(seconds=1)).isoformat(),
    }
    return row


@pytest.mark.parametrize("field,value", [
    ("provider", "freeswitch"), ("provider_call_id", "other-call"),
    ("control_backend", "pbx1:" + "f" * 64), ("acknowledged_at", "invalid"),
    ("acknowledged_at", "2026-10-21T12:00:00"),
])
def test_unrelated_or_malformed_ack_cannot_supply_existing_call_exception(field, value):
    row = acknowledged_row()
    row["route_snapshot"]["provider_acknowledgement"][field] = value
    assert lifecycle._proven_call_started_at(row) is None


def test_queued_started_at_and_planned_channel_never_prove_existing_effect():
    assert lifecycle._proven_call_started_at(call_row()) is None
    row = acknowledged_row()
    assert lifecycle._proven_call_started_at(row) is not None


def install_authority_db(monkeypatch, rows, policies, *, owner=True):
    from app.core import container as container_module, db_utils
    from tests.unit.test_call_guard_formal_authority import authority_row
    state = SimpleNamespace(owner=owner)
    state.is_telephony_owner = lambda: state.owner
    adapter = SimpleNamespace(name="asterisk")
    queries = []
    class Conn:
        async def fetch(self, query, *args):
            queries.append((query, args))
            if "FROM calls c" in query:
                return [r for r in rows if args[0] is None or r["id"] > args[0]][:args[2]]
            return list(policies.values())
        async def fetchrow(self, query, *args):
            queries.append((query, args))
            if "FROM calls" in query:
                return next((r for r in rows if str(r["id"]) == str(args[0])), None)
            return policies.get(str(args[0]), authority_row())
    conn = Conn()
    @asynccontextmanager
    async def acquire(_pool, tenant, **_kwargs):
        assert tenant is None  # Formal parent/hold RLS source is service-scoped.
        yield conn
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(container_module, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_account_hold_scan_after", None)
    return SimpleNamespace(conn=conn, state=state, adapter=adapter, queries=queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("proof,expected", [(True, True), (False, False)])
async def test_non_immediate_hold_preserves_only_proved_existing_call(monkeypatch, proof, expected):
    from tests.unit.test_call_guard_formal_authority import TENANT, deferred_row
    row = acknowledged_row() if proof else call_row()
    install_authority_db(monkeypatch, [row], {TENANT: deferred_row()})
    if expected:
        await lifecycle._require_call_authority(TENANT, pool=object(), existing_call_id=row["id"])
    else:
        with pytest.raises(HTTPException) as error:
            await lifecycle._require_call_authority(TENANT, pool=object(), existing_call_id=row["id"])
        assert error.value.status_code == 409
    # The exact same held account can never start a new provider call.
    with pytest.raises(HTTPException):
        await lifecycle._require_call_authority(TENANT, pool=object())


@pytest.mark.asyncio
async def test_plain_subscription_suspension_does_not_newly_terminate_existing_calls(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row
    row = call_row()
    install_authority_db(monkeypatch, [row], {TENANT: authority_row(subscription_status="suspended")})
    await lifecycle._require_call_authority(TENANT, pool=object(), existing_call_id=row["id"])
    with pytest.raises(HTTPException):
        await lifecycle._require_call_authority(TENANT, pool=object())


@pytest.mark.asyncio
async def test_slow_pbx_does_not_block_next_policy_scan_or_duplicate_owned_task(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold
    rows = [call_row(), call_row(id="00000000-0000-0000-0000-000000000602", status="ended")]
    fixture = install_authority_db(monkeypatch, rows, {TENANT: authority_row(active_holds=[hold()])})
    started = []
    release = asyncio.Event()
    async def terminate(row, **_kwargs):
        started.append(row["id"])
        await release.wait()
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", terminate)
    try:
        assert await asyncio.wait_for(lifecycle._reconcile_account_call_authority(), .2) == 2
        await asyncio.sleep(0)
        assert len(started) == 2
        assert await asyncio.wait_for(lifecycle._reconcile_account_call_authority(), .2) == 0
        assert len([q for q, _ in fixture.queries if "FROM calls c" in q]) == 2
        # Terminal parent rows with active legs are explicitly in the SQL, not
        # excluded from policy just because a stale status says ended.
        assert "FROM call_legs" in fixture.queries[0][0]
    finally:
        release.set()
        await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_policy_scan_rotates_without_old_unconfirmed_call_starvation(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold
    rows = [call_row(id=f"00000000-0000-0000-0000-{n:012d}") for n in range(1, 6)]
    install_authority_db(monkeypatch, rows, {TENANT: authority_row(active_holds=[hold()])})
    monkeypatch.setattr(lifecycle, "_CALL_AUTHORITY_BATCH", 2)
    visited = []
    async def terminate(row, **_kwargs):
        visited.append(row["id"])
        return False
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", terminate)
    for _ in range(3):
        await lifecycle._reconcile_account_call_authority()
        await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
        await asyncio.sleep(0)
    assert visited == [r["id"] for r in rows]
    assert lifecycle._account_hold_scan_after is None


@pytest.mark.asyncio
async def test_nonowner_cannot_scan_or_dispatch(monkeypatch):
    fixture = install_authority_db(monkeypatch, [call_row()], {}, owner=False)
    assert await lifecycle._reconcile_account_call_authority() == 0
    assert fixture.queries == []


@pytest.mark.asyncio
async def test_lost_owner_during_policy_read_cannot_dispatch(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold
    fixture = install_authority_db(monkeypatch, [call_row()], {TENANT: authority_row(active_holds=[hold()])})
    original = fixture.conn.fetch
    async def fetch(*args):
        result = await original(*args)
        fixture.state.owner = False
        return result
    fixture.conn.fetch = fetch
    assert await lifecycle._reconcile_account_call_authority() == 0


@pytest.mark.asyncio
async def test_authority_timeout_is_unavailable_and_never_dispatches(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT
    fixture = install_authority_db(monkeypatch, [], {})
    async def stalled(*_args):
        await asyncio.Event().wait()
    fixture.conn.fetchrow = stalled
    monkeypatch.setattr(lifecycle, "_CALL_AUTHORITY_READ_TIMEOUT_S", .01)
    with pytest.raises(HTTPException) as error:
        await lifecycle._require_call_authority(TENANT, pool=object())
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_owner_watchdog_observes_before_first_sleep_and_restarts_at_first_batch(monkeypatch):
    entered = asyncio.Event()
    scans = []
    async def scan():
        scans.append(lifecycle._account_hold_scan_after)
        entered.set()
    monkeypatch.setattr(lifecycle, "_account_hold_scan_after", "previous-process-cursor")
    monkeypatch.setattr(lifecycle, "_reconcile_account_call_authority", scan)
    task = asyncio.create_task(lifecycle._account_call_authority_watchdog())
    await asyncio.wait_for(entered.wait(), .2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert scans == [None]
    assert lifecycle._account_hold_termination_tasks == {}


def inbound_boundary(monkeypatch):
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
    from app.domain.services.telephony import state_backend
    from tests.unit.test_asterisk_preanswer_admission import _allowed
    from tests.unit.pbx_identity_helpers import INFO
    adapter = AsteriskAdapter()
    adapter._control_backend_system = INFO["system"]["entity_id"]
    adapter._control_backend_startup = INFO["status"]["startup_time"]
    from app.domain.services.telephony.control_backend_identity import ControlBackendIdentityError
    async def verify(expected):
        if adapter.control_backend_identity != expected:
            raise ControlBackendIdentityError("control_backend_identity_mismatch")
    adapter._verify_control_backend = verify
    state = SimpleNamespace(register_answer_intent_cleanup_obligation=AsyncMock())
    monkeypatch.setattr(state_backend, "get_state_backend", lambda: state)
    adapter.set_inbound_admission_callback(AsyncMock(return_value=_allowed()))
    adapter.set_inbound_admission_finalizer(AsyncMock())
    adapter.set_inbound_answered_persist_callback(AsyncMock())
    adapter.set_inbound_terminal_proof_persist_callback(AsyncMock())
    adapter._ari = AsyncMock(return_value={})
    adapter._alloc_rtp_port = AsyncMock(side_effect=AssertionError("media must remain unallocated"))
    adapter._cleanup_failed_inbound_setup_now_or_schedule = AsyncMock()
    adapter._schedule_failed_inbound_setup_cleanup = lambda *args, **kwargs: None
    return adapter, state


@pytest.mark.asyncio
async def test_hold_racing_answer_converges_after_actual_answer_is_persisted(monkeypatch):
    from tests.unit.test_asterisk_preanswer_admission import _event
    adapter, _ = inbound_boundary(monkeypatch)
    events = []
    async def authority(_tenant, **kwargs):
        if kwargs.get("existing_call_id"):
            events.append("post_check")
            raise HTTPException(409, detail={"error": "call_authority_denied"})
        events.append("pre_check")
    async def persist(*_args, **kwargs):
        events.append("persisted_actual_answer")
        return kwargs["answered_at"]
    monkeypatch.setattr(lifecycle, "_require_call_authority", authority)
    adapter.set_inbound_answered_persist_callback(persist)
    await adapter._on_stasis_start("inbound-1", _event())
    assert events == ["pre_check", "persisted_actual_answer", "post_check"]
    adapter._ari.assert_awaited_once_with("POST", "/channels/inbound-1/answer", ok=(200, 204))
    adapter._alloc_rtp_port.assert_not_awaited()
    adapter._cleanup_failed_inbound_setup_now_or_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_pbx_during_answer_intent_does_not_answer_replacement(monkeypatch):
    from tests.unit.test_asterisk_preanswer_admission import _event
    adapter, state = inbound_boundary(monkeypatch)
    async def replace(*_args, **_kwargs):
        adapter._ari_host = "replacement.example"
    state.register_answer_intent_cleanup_obligation = replace
    monkeypatch.setattr(lifecycle, "_require_call_authority", AsyncMock())
    await adapter._on_stasis_start("inbound-1", _event())
    adapter._ari.assert_not_awaited()
    adapter._cleanup_failed_inbound_setup_now_or_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_pbx_during_answer_persist_cannot_allocate_replacement_media(monkeypatch):
    from tests.unit.test_asterisk_preanswer_admission import _event
    adapter, _ = inbound_boundary(monkeypatch)
    async def replace(*_args, **kwargs):
        adapter._ari_host = "replacement.example"
        return kwargs["answered_at"]
    adapter.set_inbound_answered_persist_callback(replace)
    monkeypatch.setattr(lifecycle, "_require_call_authority", AsyncMock())
    await adapter._on_stasis_start("inbound-1", _event())
    adapter._ari.assert_awaited_once_with("POST", "/channels/inbound-1/answer", ok=(200, 204))
    adapter._alloc_rtp_port.assert_not_awaited()
    adapter._cleanup_failed_inbound_setup_now_or_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_postanswer_authority_await_keeps_original_setup_guard(monkeypatch):
    from tests.unit.test_asterisk_preanswer_admission import _event
    from app.domain.services.telephony.control_backend_identity import ControlBackendIdentityError
    adapter, _ = inbound_boundary(monkeypatch)
    reached = []
    adapter._connect_control_backend = AsyncMock()
    async def authority(_tenant, **kwargs):
        if kwargs.get("existing_call_id"):
            with pytest.raises(ControlBackendIdentityError, match="reconfiguration_busy"):
                await adapter.connect({"ari_host": "replacement.example"})
            reached.append("post_answer")
    monkeypatch.setattr(lifecycle, "_require_call_authority", authority)
    await adapter._on_stasis_start("inbound-1", _event())
    assert reached == ["post_answer"]
    adapter._connect_control_backend.assert_not_awaited()
    assert adapter._control_backend_operations == 0


@pytest.mark.asyncio
async def test_cancelled_preanswer_policy_cannot_send_answer(monkeypatch):
    from tests.unit.test_asterisk_preanswer_admission import _event
    adapter, _ = inbound_boundary(monkeypatch)
    monkeypatch.setattr(lifecycle, "_require_call_authority", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await adapter._on_stasis_start("inbound-1", _event())
    adapter._ari.assert_not_awaited()
    assert not adapter._inbound_admissions["inbound-1"].get("_answer_intent_at_utc")


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_actual_owner_fence_preserves_all_leg_original_backend_and_pending_truth(monkeypatch, confirmed):
    from app.domain.services.telephony import termination
    from tests.unit.pbx_identity_helpers import BINDING
    row = call_row()
    provider_id = row["provider_call_id"]
    state = SimpleNamespace(is_telephony_owner=lambda: True, register_cleanup_obligation=AsyncMock())
    original = SimpleNamespace(name="asterisk")
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: original)
    context = termination.TerminationContext(
        call_id=row["id"], tenant_id=row["tenant_id"], provider_call_id=provider_id,
        previous_status=row["status"], provider_leg_ids=("child",),
        provider="asterisk", control_backend=BINDING, provider_legs=(("child", "asterisk"),))
    mark = AsyncMock(return_value=context)
    proof = AsyncMock(return_value=termination.HangupProof(True, confirmed, "confirmed" if confirmed else "confirmation_timeout"))
    finalize = AsyncMock(return_value=True)
    receipt = AsyncMock(return_value=TerminationReceiptResult(True, True, "saved"))
    monkeypatch.setattr(termination, "mark_termination_pending_and_load_context", mark)
    monkeypatch.setattr(termination, "request_confirmed_hangup", proof)
    monkeypatch.setattr(lifecycle, "_on_call_ended", finalize)
    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", receipt)
    try:
        result = await lifecycle._fence_inbound_call_after_lease_loss(
            SimpleNamespace(db_pool=object()), pbx_call_id=provider_id,
            durable_call_id=row["id"], admission=row, require_owner=True,
        )
        assert result is confirmed
        mark.assert_awaited_once()
        assert proof.await_args.args[0] is original
        assert proof.await_args.kwargs["expected_backend"] == BINDING
        assert proof.await_args.kwargs["provider_legs"] == (("child", "asterisk"),)
        assert proof.await_args.kwargs["require_backend_identity"] is True
        assert finalize.await_count == int(confirmed)
        assert receipt.await_count == int(confirmed)
        if confirmed:
            assert receipt.await_args.args[1] is context
            assert receipt.await_args.kwargs == {"requested": True}
        assert (provider_id in lifecycle._orphan_recovery_contexts_by_call) is not confirmed
    finally:
        lifecycle._orphan_recovery_contexts_by_call.pop(provider_id, None)
        lifecycle._orphan_recovery_in_flight.discard(provider_id)


@pytest.mark.asyncio
async def test_owner_loss_after_durable_fence_keeps_intent_without_pbx_effect(monkeypatch):
    from app.domain.services.telephony import termination
    from tests.unit.pbx_identity_helpers import BINDING
    row = call_row()
    state = SimpleNamespace(owner=True, register_cleanup_obligation=AsyncMock())
    state.is_telephony_owner = lambda: state.owner
    adapter = SimpleNamespace(name="asterisk")
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    async def mark(*_args, **_kwargs):
        state.owner = False
        return SimpleNamespace(provider_call_id=row["provider_call_id"], provider_leg_ids=(),
            provider="asterisk", control_backend=BINDING, provider_legs=())
    monkeypatch.setattr(termination, "mark_termination_pending_and_load_context", mark)
    proof = AsyncMock()
    monkeypatch.setattr(termination, "request_confirmed_hangup", proof)
    try:
        assert not await lifecycle._fence_inbound_call_after_lease_loss(
            SimpleNamespace(db_pool=object()), pbx_call_id=row["provider_call_id"],
            durable_call_id=row["id"], admission=row, require_owner=True,
        )
        proof.assert_not_awaited()
        assert row["provider_call_id"] in lifecycle._orphan_recovery_contexts_by_call
    finally:
        lifecycle._orphan_recovery_contexts_by_call.pop(row["provider_call_id"], None)


@pytest.mark.asyncio
async def test_changed_or_unknown_backend_cleanup_cannot_delete_replacement(monkeypatch):
    adapter, _ = inbound_boundary(monkeypatch)
    original_binding = adapter.control_backend_identity
    adapter._inbound_admissions["inbound-1"] = {"_control_backend": original_binding}
    adapter._ari_host = "replacement.example"
    adapter._gateway = AsyncMock()
    adapter.hangup_confirmed = AsyncMock(return_value=True)
    assert not await adapter._attempt_failed_inbound_setup_cleanup(
        "inbound-1", session_id="session", ext_channel_id="external", bridge_id="bridge",
    )
    adapter._ari.assert_not_awaited()
    adapter._gateway.assert_not_awaited()
    adapter.hangup_confirmed.assert_not_awaited()
    adapter._inbound_admissions["inbound-1"] = {}
    assert not await adapter._attempt_failed_inbound_setup_cleanup("inbound-1")
    adapter.hangup_confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_tenant_does_not_starve_other_199_calls_in_bounded_scan(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold
    bad_tenant = "00000000-0000-0000-0000-000000000599"
    rows = [call_row(id=f"00000000-0000-0000-0000-{n:012d}") for n in range(1, 201)]
    rows[0]["tenant_id"] = bad_tenant
    install_authority_db(monkeypatch, rows, {
        TENANT: authority_row(active_holds=[hold()]),
        bad_tenant: authority_row(id=bad_tenant, active_holds="malformed"),
    })
    released = asyncio.Event()
    async def slow_proof(*_args, **_kwargs):
        await released.wait()
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", slow_proof)
    try:
        # A supported 200-call page is observed independently of all pending
        # provider operations; this measures synthetic scheduling, not PG/PBX SLA.
        assert await asyncio.wait_for(lifecycle._reconcile_account_call_authority(), .5) == 199
        assert rows[0]["id"] not in lifecycle._account_hold_termination_tasks
    finally:
        released.set()
        await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_full_final_page_wraps_without_an_empty_polling_tick(monkeypatch):
    from tests.unit.test_call_guard_formal_authority import TENANT, authority_row, hold
    rows = [call_row(id=f"00000000-0000-0000-0000-{n:012d}") for n in range(1, 3)]
    fixture = install_authority_db(monkeypatch, rows, {TENANT: authority_row(active_holds=[hold()])})
    monkeypatch.setattr(lifecycle, "_CALL_AUTHORITY_BATCH", 2)
    visited = []
    async def terminate(row, **_kwargs):
        visited.append(row["id"])
    monkeypatch.setattr(lifecycle, "_terminate_account_restricted_call", terminate)
    for _ in range(2):
        assert await lifecycle._reconcile_account_call_authority() == 2
        await asyncio.gather(*list(lifecycle._account_hold_termination_tasks.values()))
        await asyncio.sleep(0)
    assert visited == [r["id"] for r in rows] * 2
    assert len([q for q, _ in fixture.queries if "FROM calls c" in q]) == 3
