"""Old external effect proof must not settle changed/unreadable current identity."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin import calls
from app.domain.services.telephony import lifecycle, termination
from app.domain.services.telephony.termination_receipts import TerminationReceiptResult
from tests.unit.test_inbound_lease_inventory_proof import path  # noqa: F401
from tests.unit.test_admin_operational_media_controls import _TerminateConn, _Adapter, _Audit, _stub_admin_termination_context
from tests.unit.pbx_identity_helpers import BINDING


@pytest.mark.parametrize("reason", ["identity_changed", "identity_unavailable", "inventory_unavailable"])
async def test_owned_lease_loss_retains_callback_fence_after_old_pbx_proof(path, monkeypatch, reason):  # noqa: F811
    path.db_available = True
    save = AsyncMock(return_value=TerminationReceiptResult(False, False, reason))
    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", save)
    assert await path.invoke() is False
    assert "/channels/" + path.child in path.deletes
    save.assert_awaited_once()
    assert path.entries == []
    assert path.parent in lifecycle._orphan_recovery_contexts_by_call
    assert await path.ended(path.parent) is False


@pytest.mark.parametrize("reason", ["identity_changed", "identity_unavailable", "inventory_unavailable"])
async def test_orphan_recovery_keeps_ledger_and_guard_when_current_identity_unverified(monkeypatch, reason):
    from app.core import container
    original = {"durable_call_id": str(uuid4()), "tenant_id": str(uuid4()),
                "provider": "asterisk", "provider_call_id": "parent",
                "route_snapshot": {"control_backend": BINDING}}
    context = termination.TerminationContext(call_id=original["durable_call_id"], tenant_id=original["tenant_id"],
        provider="asterisk", provider_call_id="parent", previous_status="in_call", provider_leg_ids=("child",),
        provider_legs=(("child", "asterisk"),), control_backend=BINDING,
        leg_inventory=((str(uuid4()), "asterisk", "child"),))
    finalize = AsyncMock()
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(db_pool=object()))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: object())
    monkeypatch.setattr(termination, "mark_termination_pending_and_load_context", AsyncMock(return_value=context))
    monkeypatch.setattr(termination, "request_confirmed_hangup", AsyncMock(return_value=termination.HangupProof(True, True, "confirmed")))
    monkeypatch.setattr(lifecycle, "_save_confirmed_termination_receipt", AsyncMock(return_value=TerminationReceiptResult(False, False, reason)))
    monkeypatch.setattr(lifecycle, "_on_call_ended", finalize)
    assert await lifecycle._force_end_and_hangup("parent", recovery_context=original) is False
    assert original["_awaiting_all_leg_absence_proof"] is True
    assert "_pbx_all_leg_absence_confirmed" not in original
    finalize.assert_not_awaited()


@pytest.mark.parametrize("reason,verified", [("identity_changed", False), ("identity_unavailable", False),
                                          ("inventory_unavailable", False), ("storage_unavailable", True)])
async def test_admin_receipt_outcome_separates_prior_effect_from_current_settlement(monkeypatch, reason, verified):
    call_id = str(uuid4())
    conn = _TerminateConn({"id": call_id, "tenant_id": uuid4(), "external_call_uuid": "parent",
        "provider_call_id": "parent", "provider": "asterisk", "direction": "inbound",
        "status": "in_call", "answered_at": datetime.now(timezone.utc)})
    @asynccontextmanager
    async def acquire(*_, **__):
        yield conn
    adapter, finalize = _Adapter(), AsyncMock()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    _stub_admin_termination_context(monkeypatch, conn)
    monkeypatch.setattr(calls, "finalize_proven_inbound_termination", finalize)
    monkeypatch.setattr(calls, "_persist_admin_termination_receipt", AsyncMock(
        return_value=TerminationReceiptResult(False, verified, reason)))
    operation = calls.terminate_call(call_id, CurrentUser(id=str(uuid4()), email="operator@example.invalid",
        tenant_id=None, role="platform_admin"), SimpleNamespace(pool=object()), _Audit())
    if verified:
        result = await operation
        assert result["provider_hangup_confirmed"] is True
        assert result["termination_receipt_saved"] is False
        finalize.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as failure:
            await operation
        detail = failure.value.detail
        assert failure.value.status_code == 503
        assert detail["prior_identity_hangup_confirmed"] is True
        assert detail["provider_hangup_confirmed"] is False
        assert detail["termination_status"] == "requested" and detail["settlement_deferred"] is True
        assert detail["reason"] == reason
        assert conn.call_row["status"] == "termination_pending"
        assert not any("duration_seconds" in query for query, _ in conn.queries)
        finalize.assert_not_awaited()
    assert adapter.hung_up == ["parent"]
