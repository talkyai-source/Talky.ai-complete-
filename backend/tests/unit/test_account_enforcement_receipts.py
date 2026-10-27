"""Actual owner/finalizer boundaries; PBX and receipt storage are synthetic."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.domain.services.telephony.termination_receipts import TerminationReceiptResult

from app.domain.services.telephony import lifecycle, termination
from tests.unit.test_inbound_lease_inventory_proof import path  # noqa: F401
from tests.unit.pbx_identity_helpers import BINDING


@pytest.mark.parametrize("receipt_saved", [True, False])
async def test_proved_owner_hangup_attempts_durable_receipt_before_finalization(path, monkeypatch, receipt_saved):  # noqa: F811
    path.db_available = True
    saved = []

    async def save(pool, context, *, requested):
        assert path.entries == []
        assert "/channels/" + path.child in path.deletes
        assert context.provider_call_id == path.parent
        assert context.control_backend == path.adapter.control_backend_identity
        assert context.provider_legs == ((path.child, "asterisk"),)
        saved.append(context.call_id)
        return TerminationReceiptResult(receipt_saved, True, "saved" if receipt_saved else "storage_unavailable")

    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", save, raising=False)
    assert await path.invoke() is True
    assert saved == ["00000000-0000-4000-8000-000000000001"]
    # Failed evidence storage cannot undo known external termination or make
    # callers retry as though the provider effect never happened.
    assert len(path.entries) == 1


async def test_unconfirmed_owner_hangup_never_writes_a_confirmation(path, monkeypatch):  # noqa: F811
    path.db_available = True
    path.child_present = True
    save = AsyncMock()
    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", save, raising=False)
    assert await path.invoke() is False
    save.assert_not_awaited()
    assert path.entries == []


async def test_restart_recovery_saves_exact_proof_before_logical_settlement(monkeypatch):
    from app.core import container
    order = []
    context = {
        "durable_call_id": "00000000-0000-4000-8000-000000000001",
        "tenant_id": "00000000-0000-4000-8000-000000000002",
        "provider": "asterisk", "provider_call_id": "parent",
        "status": "termination_pending", "route_snapshot": {"control_backend": BINDING},
    }
    async def save(pool, receipt_context, *, requested):
        assert receipt_context.call_id == context["durable_call_id"]
        assert receipt_context.provider_call_id == "parent"
        assert receipt_context.provider_legs == (("child", "asterisk"),)
        assert receipt_context.control_backend == BINDING
        order.append("saved")
        return TerminationReceiptResult(True, True, "saved")
    async def finalize(*args, **kwargs):
        order.append("settled")
        return True
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: object())
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(termination, "mark_termination_pending_and_load_context", AsyncMock(
        return_value=termination.TerminationContext(
            call_id=context["durable_call_id"], tenant_id=context["tenant_id"],
            provider="asterisk", provider_call_id="parent", previous_status="in_call",
            provider_leg_ids=("child",), provider_legs=(("child", "asterisk"),), control_backend=BINDING,
        )))
    monkeypatch.setattr(termination, "request_confirmed_hangup", AsyncMock(
        return_value=termination.HangupProof(True, True, "confirmed")))
    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", save, raising=False)
    monkeypatch.setattr(lifecycle, "_on_call_ended", finalize)
    assert await lifecycle._force_end_and_hangup("parent", provider_leg_ids=["child"], recovery_context=context)
    assert order == ["saved", "settled"]


@pytest.mark.parametrize("failure", ["storage", "channel", "backend", "provider"])
async def test_restart_recovery_never_controls_pbx_after_failed_durable_fence(monkeypatch, failure):
    from app.core import container
    from dataclasses import replace
    context = termination.TerminationContext(
        call_id="00000000-0000-4000-8000-000000000001", tenant_id="00000000-0000-4000-8000-000000000002",
        provider="asterisk", provider_call_id="parent", previous_status="in_call",
        provider_leg_ids=("child",), provider_legs=(("child", "asterisk"),), control_backend=BINDING,
    )
    original = {"durable_call_id": context.call_id, "tenant_id": context.tenant_id,
                "provider": "asterisk", "provider_call_id": "parent", "route_snapshot": {"control_backend": BINDING}}
    if failure == "storage":
        fence = AsyncMock(side_effect=RuntimeError("synthetic unavailable database"))
    else:
        changes = {"channel": {"provider_call_id": "replacement"},
                   "backend": {"control_backend": "pbx1:" + "b" * 64},
                   "provider": {"provider": "freeswitch"}}
        fence = AsyncMock(return_value=replace(context, **changes[failure]))
    proof, settle = AsyncMock(), AsyncMock()
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: object())
    monkeypatch.setattr(termination, "mark_termination_pending_and_load_context", fence)
    monkeypatch.setattr(termination, "request_confirmed_hangup", proof)
    monkeypatch.setattr(lifecycle, "_on_call_ended", settle)
    assert not await lifecycle._force_end_and_hangup("parent", recovery_context=original)
    assert original["_awaiting_all_leg_absence_proof"] is True
    proof.assert_not_awaited()
    settle.assert_not_awaited()
