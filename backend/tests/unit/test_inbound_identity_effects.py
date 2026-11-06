"""F5-10 downstream effect boundaries using synthetic, tenant-aware DB ports."""
from __future__ import annotations

import copy
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest


TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "99999999-9999-4999-8999-999999999999"
CALL = "22222222-2222-4222-8222-222222222222"
ACTION = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def synthetic_settings(monkeypatch):
    from app.core import config, dotenv_compat
    import dotenv
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings.model_construct(environment="test"))


def call_row(*, direction="inbound", verified=False, tenant=TENANT):
    return {"id": CALL, "tenant_id": tenant, "direction": direction,
            "phone_number": "+15550001000", "caller_ani": "+15550001000",
            "route_snapshot": {"caller_identity": {
                "version": 1, "caller_id_verified": verified, "attestation": "A" if verified else None,
                "verstat": "TN-Validation-Passed" if verified else None,
                "source": "trusted_carrier" if verified else "missing"}}}


class Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class Connection:
    def __init__(self, call):
        self.call = call
        self.call_queries = []
        self.contact_writes = []
        self.action = None
        self.failure = None

    def transaction(self):
        return Context(self)

    async def execute(self, sql, *args):
        statement = " ".join(sql.split())
        if statement.startswith("SET LOCAL "):
            return "SET"
        if statement.startswith("UPDATE assistant_actions SET"):
            assert "tenant_id=$1" in statement and "id=$2" in statement
            assert tuple(map(str, args[:2])) == (TENANT, ACTION)
            return "UPDATE 1"
        raise AssertionError("Unexpected unobserved write: " + statement)

    async def fetchrow(self, sql, *args):
        if "INSERT INTO assistant_actions" in sql:
            self.action = {"id": ACTION, "type": args[1], "status": "pending", "input_data": args[3]}
            return dict(self.action)
        if "INSERT INTO call_lead_details" in sql:
            self.contact_writes.append(args)
            return {"id": ACTION}
        if "FROM calls" in sql:
            self.call_queries.append((sql, args))
            assert "tenant_id" in sql and "WHERE" in sql and "id=" in sql.replace(" ", "")
            assert TENANT in [str(arg) for arg in args] and CALL in [str(arg) for arg in args]
            if self.failure:
                raise self.failure
            return copy.deepcopy(self.call) if self.call and self.call["tenant_id"] == TENANT else None
        if "FROM assistant_actions" in sql:
            return dict(self.action) if self.action else None
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        assert "UPDATE assistant_actions" in sql
        self.action["status"] = "running"
        return ACTION


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return Context(self.connection)


async def execute_callback(conn, *, triggered_by="voice", action="schedule_callback"):
    from app.services.action_execution import DurableActionExecutor
    effect = AsyncMock(return_value={"success": True, "status": "scheduled", "confirmation_allowed": True})
    result = await DurableActionExecutor(Pool(conn)).execute(
        tenant_id=TENANT, call_id=CALL, idempotency_key="synthetic-effect", action=action,
        payload={"caller_id_verified": True, "confirmation": "caller said yes"},
        executor=effect, triggered_by=triggered_by,
    )
    return result, effect


@pytest.mark.parametrize("snapshot", [{}, {"caller_identity": {"caller_id_verified": "true"}},
    {"caller_identity": {"version": 1, "caller_id_verified": True, "attestation": "B", "source": "trusted_carrier"}},
    {"caller_identity": {"version": 1, "caller_id_verified": True, "attestation": "A", "source": "untrusted"}}])
async def test_unverified_inbound_callback_never_runs_executor_even_with_claimed_payload_flag(snapshot):
    call = call_row()
    call["route_snapshot"] = snapshot
    conn = Connection(call)
    result, effect = await execute_callback(conn)
    assert result["success"] is False and result["confirmation_allowed"] is False
    assert result["status"] == "caller_id_unverified"
    effect.assert_not_awaited()
    assert conn.call_queries, "Callback decision must read durable tenant-scoped call state"


@pytest.mark.parametrize("row", [None, call_row(tenant=OTHER)])
async def test_missing_or_foreign_call_cannot_authorize_callback(row):
    result, effect = await execute_callback(Connection(row))
    assert result["success"] is False
    effect.assert_not_awaited()


@pytest.mark.parametrize("direction,verified,triggered", [
    ("inbound", True, "voice"), ("outbound", False, "voice"), ("inbound", False, "dashboard")])
async def test_verified_inbound_outbound_and_manual_effects_preserve_existing_contract(direction, verified, triggered):
    result, effect = await execute_callback(Connection(call_row(direction=direction, verified=verified)), triggered_by=triggered)
    assert result["success"] is True
    effect.assert_awaited_once()


async def test_opt_out_is_not_blocked_by_unverified_identity():
    result, effect = await execute_callback(Connection(call_row()), action="opt_out")
    assert result["success"] is True
    effect.assert_awaited_once()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_call_authority_read_failure_never_executes_callback(cancelled):
    from app.services.action_execution import DurableActionExecutor
    conn = Connection(call_row(verified=True))
    conn.failure = asyncio.CancelledError() if cancelled else RuntimeError("synthetic unavailable call store")
    effect = AsyncMock(return_value={"success": True, "status": "scheduled"})
    operation = DurableActionExecutor(Pool(conn)).execute(
        tenant_id=TENANT, call_id=CALL, idempotency_key="synthetic-read-failure",
        action="schedule_callback", payload={}, executor=effect, triggered_by="voice")
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        try:
            result = await operation
        except RuntimeError:
            pass
        else:
            assert result["success"] is False
    effect.assert_not_awaited()


async def test_actual_caller_opt_out_writer_remains_permanent_without_identity_prerequisite():
    from app.domain.services.dnc_service import DNCService

    class DNCConnection(Connection):
        async def fetchrow(self, sql, *args):
            assert "INSERT INTO dnc_entries" in sql
            assert args[:3] == (TENANT, "+14155552671", "caller_opt_out")
            assert args[5] is None
            self.contact_writes.append(args)
            return {"id": ACTION, "tenant_id": TENANT, "normalized_number": args[1],
                    "source": args[2], "reason": args[3], "expires_at": None, "created_at": None}

    conn = DNCConnection(call_row())
    entry = await DNCService(Pool(conn)).add_caller_opt_out(
        tenant_id=TENANT, e164="+14155552671", call_id=CALL)
    assert entry.source == "caller_opt_out" and entry.expires_at is None
    assert len(conn.contact_writes) == 1 and conn.call_queries == []


async def _capture(conn, field="email", *, source="caller_stated"):
    from app.domain.services.lead_capture_service import LeadCaptureService
    field_type = field if field in {"email", "phone"} else "text"
    value = {"email": "synthetic@example.com", "phone": "+15550001000", "full_name": "Synthetic Person",
             "company_name": "Synthetic Company", "budget": "under review"}[field]
    return await LeadCaptureService(Pool(conn)).capture(
        tenant_id=TENANT, call_id=CALL, field_key=field, value=value, source=source,
        field_type=field_type, confirmed=True,
        **({"validation_status": "confirmed", "normalized_value": value, "raw_value": value}
           if field_type in {"email", "phone"} else {}),
    )


@pytest.mark.parametrize("field", ["email", "phone", "full_name", "company_name"])
async def test_unverified_inbound_cannot_automatically_change_contact_fields(field):
    conn = Connection(call_row())
    assert await _capture(conn, field) is False
    assert conn.contact_writes == [] and conn.call_queries


@pytest.mark.parametrize("direction,verified,source", [
    ("inbound", True, "caller_stated"), ("outbound", False, "caller_stated"),
    ("inbound", False, "manual_edit")])
async def test_contact_policy_preserves_verified_outbound_and_manual_corrections(direction, verified, source):
    conn = Connection(call_row(direction=direction, verified=verified))
    assert await _capture(conn, source=source) is True
    assert len(conn.contact_writes) == 1


async def test_nonidentity_business_capture_is_not_silently_disabled():
    conn = Connection(call_row())
    assert await _capture(conn, "budget") is True


async def test_foreign_call_is_not_a_contact_write_authority():
    conn = Connection(call_row(tenant=OTHER))
    assert await _capture(conn) is False
    assert conn.contact_writes == []


@pytest.mark.parametrize("verified,direction,allowed", [(False, "inbound", False), (True, "inbound", True),
                                                       (False, "outbound", True)])
async def test_crm_independently_refuses_automatic_contact_creation(monkeypatch, verified, direction, allowed):
    from app.services.crm_sync_service import CRMSyncService
    service = CRMSyncService(db_client=object(), db_pool=object())
    connector = NS(external_account_id="synthetic-account", connector_id=ACTION,
                   search_contact=AsyncMock(return_value=None), create_contact=AsyncMock(return_value={"id": "new"}))

    async def fake_auth(tenant, provider, selected, op, **kwargs):
        return await op(selected)
    monkeypatch.setattr(service, "_with_auth_retry", fake_auth)
    monkeypatch.setattr(service, "_remember_contact_id", AsyncMock())
    result = await service._resolve_contact(TENANT, "salesforce", connector,
        call_row(direction=direction, verified=verified), None, {}, body_for_new="synthetic call")
    assert bool(result) is allowed
    assert connector.create_contact.await_count == int(allowed)
    if not allowed:
        service._remember_contact_id.assert_not_awaited()


async def test_unverified_crm_search_does_not_automatically_change_local_contact_link(monkeypatch):
    from app.services.crm_sync_service import CRMSyncService
    service = CRMSyncService(db_client=object(), db_pool=object())
    connector = NS(external_account_id="synthetic-account", connector_id=ACTION,
                   search_contact=AsyncMock(return_value={"id": "other-person"}), create_contact=AsyncMock())
    async def fake_auth(tenant, provider, selected, op, **kwargs):
        return await op(selected)
    monkeypatch.setattr(service, "_with_auth_retry", fake_auth)
    monkeypatch.setattr(service, "_remember_contact_id", AsyncMock())
    result = await service._resolve_contact(TENANT, "salesforce", connector, call_row(),
        {"id": ACTION, "phone_number": "+15550001000"}, {}, body_for_new="synthetic call")
    assert result is None
    service._remember_contact_id.assert_not_awaited()
    connector.create_contact.assert_not_awaited()
