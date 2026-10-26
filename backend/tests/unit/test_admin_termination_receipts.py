"""Saved all-leg termination receipts; no provider/network calls in these checks."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import pytest

from app.api.v1.endpoints.admin import calls
from app.domain.services.telephony.termination import TerminationContext
from app.domain.services.telephony import termination_receipts


BINDING = "pbx1:" + "a" * 64
NOW = datetime.now(timezone.utc)
CALL = {"id": "call-A", "tenant_id": "tenant-A", "provider": "asterisk", "provider_call_id": "parent-A", "status": "termination_pending", "route_snapshot": {"control_backend": BINDING}}
LEG = {"id": "leg-row-A", "provider": "asterisk", "provider_leg_id": "child-A", "status": "ended", "created_at": NOW - timedelta(minutes=2)}
RECEIPT = {"version": 2, "control_backend": BINDING, "call_id": "call-A", "tenant_id": "tenant-A", "provider": "asterisk", "provider_call_id": "parent-A",
           "provider_hangup_requested": True, "provider_hangup_confirmed": True, "confirmed_at": (NOW - timedelta(minutes=1)).isoformat(),
           "legs": [{key: LEG[key] for key in ("id", "provider", "provider_leg_id")}]}


class Builder:
    def __init__(self, result): self.result = result
    def __getattr__(self, _name): return lambda *_args, **_kwargs: self
    def execute(self): return self.result


def db_for(receipt=None, *, error=None, legs=None):
    events = [] if receipt is None else [{"call_id": CALL["id"], "event_data": receipt}]
    return SimpleNamespace(table=lambda table: Builder(SimpleNamespace(
        data=events if table == "call_events" else ([LEG] if legs is None else legs), error=error)))


def test_pending_is_not_confirmed_without_saved_receipt():
    result = calls._admin_termination_projection(db_for(), CALL)
    assert result["termination_status"] == "requested"
    assert result["provider_hangup_confirmed"] is False


def test_terminal_parent_timestamp_is_not_all_leg_proof():
    result = calls._admin_termination_projection(db_for(), {**CALL, "status": "ended", "provider_terminated_at": NOW})
    assert result["termination_status"] == "none"
    assert result["provider_hangup_confirmed"] is False


def test_matching_receipt_reconciles_confirmed_effect_while_settlement_is_pending():
    result = calls._admin_termination_projection(db_for(json.dumps(RECEIPT)), CALL)
    assert result["termination_status"] == "confirmed"
    assert result["provider_hangup_confirmed"] is True
    assert result["termination_reconciliation_error"] is None


@pytest.mark.parametrize("key,value", [("id", "other-call"), ("tenant_id", "other-tenant"), ("provider", "freeswitch"), ("provider_call_id", "other-channel"), ("status", "in_call")])
def test_changed_parent_identity_or_reactivation_rejects_receipt(key, value):
    assert calls._receipt_matches({**CALL, key: value}, [LEG], RECEIPT) is False


def test_new_uncovered_leg_even_if_already_ended_rejects_receipt():
    new_leg = {**LEG, "id": "new-leg", "provider_leg_id": "new-child", "created_at": NOW}
    assert calls._receipt_matches(CALL, [LEG, new_leg], RECEIPT) is False


def test_rebound_or_missing_saved_leg_rejects_receipt():
    assert calls._receipt_matches(CALL, [{**LEG, "provider_leg_id": "replacement"}], RECEIPT) is False
    assert calls._receipt_matches(CALL, [], RECEIPT) is False


def test_future_or_malformed_receipt_is_not_confirmation():
    assert calls._receipt_matches(CALL, [LEG], {**RECEIPT, "confirmed_at": (NOW + timedelta(hours=1)).isoformat()}) is False
    assert calls._receipt_matches(CALL, [LEG], {**RECEIPT, "legs": "invalid"}) is False


def test_adapter_error_envelope_is_visible_and_not_confirmed():
    result = calls._admin_termination_projection(db_for(RECEIPT, error="synthetic storage error"), CALL)
    assert result["provider_hangup_confirmed"] is False
    assert result["termination_reconciliation_error"] == "Saved termination confirmation could not be read."


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_confirmed_receipt_persistence_has_explicit_failure_result(monkeypatch, write_fails):
    saved = []
    class Conn:
        async def fetchrow(self, *_args): return CALL
        async def fetch(self, *_args): return [LEG]
        async def execute(self, sql, *args):
            if write_fails: raise RuntimeError("synthetic insert unavailable")
            saved.append((sql, args))
    @asynccontextmanager
    async def acquire(*_args, **_kwargs): yield Conn()
    monkeypatch.setattr(termination_receipts, "acquire_with_tenant", acquire)
    context = TerminationContext(call_id="call-A", tenant_id="tenant-A", provider_call_id="parent-A", previous_status="in_call",
                                 provider_leg_ids=("child-A",), provider="asterisk", provider_legs=(("child-A", "asterisk"),), control_backend=BINDING,
                                 leg_inventory=(("leg-row-A", "asterisk", "child-A"),))
    result = await calls._persist_admin_termination_receipt(object(), context, requested=True)
    assert result.saved is (not write_fails)
    assert result.current_identity_verified is True
    assert result.code == ("storage_unavailable" if write_fails else "saved")
    if not write_fails:
        receipt = json.loads(saved[0][1][3])
        assert receipt["legs"] == RECEIPT["legs"]
        assert receipt["provider_hangup_confirmed"] is True
        assert receipt["scope"] == "saved_control_backend_and_channel_identities"
        assert receipt["version"] == 3
        assert receipt["inventory_coverage"] == "complete_preproof"


def test_complete_inventory_rejects_backdated_terminal_row_that_legacy_receipt_cannot_detect():
    late = {**LEG, "id": "late-row", "provider_leg_id": "late-child", "created_at": NOW - timedelta(days=1)}
    # Historical v2 receipts remain readable with their original narrower
    # guarantee. New v3 proof must not rely on row creation timestamps.
    assert calls._receipt_matches(CALL, [LEG, late], RECEIPT)
    receipt = {**RECEIPT, "version": 3, "leg_inventory": RECEIPT["legs"]}
    assert calls._receipt_matches(CALL, [LEG], receipt)
    assert not calls._receipt_matches(CALL, [LEG, late], receipt)


@pytest.mark.parametrize("inventory", [None, "invalid", [{"id": "x"}], RECEIPT["legs"] * 2])
def test_v3_inventory_must_be_complete_well_formed_and_unique(inventory):
    assert not calls._receipt_matches(CALL, [LEG], {**RECEIPT, "version": 3, "leg_inventory": inventory})


def test_family_only_receipt_and_changed_backend_are_not_confirmed():
    assert not calls._receipt_matches(CALL, [LEG], {**RECEIPT, "version": 1})
    assert not calls._receipt_matches({**CALL, "route_snapshot": {}}, [LEG], RECEIPT)
    assert not calls._receipt_matches(CALL, [LEG], {**RECEIPT, "control_backend": "pbx1:" + "b" * 64})


@pytest.mark.parametrize("version", [None, {}, [], True, "3", 3.0])
def test_malformed_version_is_unknown_without_breaking_projection(version):
    assert not calls._receipt_matches(CALL, [LEG], {**RECEIPT, "version": version})
