"""Actual call-detail handlers and CRM projection, with synthetic storage only."""
from __future__ import annotations

import copy
import importlib
from types import SimpleNamespace as NS

import pytest

from tests.unit.test_inbound_identity_effects import CALL, TENANT, Connection, Pool, call_row


@pytest.fixture(autouse=True)
def synthetic_settings(monkeypatch):
    from app.core import config, dotenv_compat
    import dotenv
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings.model_construct(environment="test"))


class DetailConnection(Connection):
    async def fetch(self, sql, *args):
        assert "FROM call_legs" in sql
        return []


def detailed_call(verified):
    call = call_row(verified=verified)
    call.update(status="completed", created_at="2026-11-06T00:00:00+00:00", campaign_id=None,
                called_did="+15551234567", billing_status="none", lead_id=None)
    return call


@pytest.mark.parametrize("verified", [False, True])
async def test_tenant_detail_handler_exposes_durable_identity_fields(verified):
    from app.api.v1.endpoints.calls import get_call
    conn = DetailConnection(detailed_call(verified))
    detail = await get_call(CALL, current_user=NS(tenant_id=TENANT), db_client=NS(pool=Pool(conn)))
    payload = detail.model_dump()
    assert payload["caller_id_verified"] is verified
    assert payload["caller_id_attestation"] == ("A" if verified else None)
    assert conn.call_queries, "Detail projection must use its tenant-scoped call query"


@pytest.mark.parametrize("verified", [False, True])
async def test_admin_detail_handler_exposes_same_durable_identity_fields(verified):
    from app.api.v1.endpoints.admin.calls import get_admin_call_detail
    from tests.unit.test_admin_call_currency import _FakeClient
    detail = await get_admin_call_detail(CALL, admin_user=NS(), db_client=_FakeClient(detailed_call(verified), []))
    payload = detail.model_dump()
    assert payload["caller_id_verified"] is verified
    assert payload["caller_id_attestation"] == ("A" if verified else None)


@pytest.mark.parametrize("snapshot", [None, {}, "broken json", {"caller_identity": None},
    {"caller_identity": {"caller_id_verified": "true", "attestation": "A"}},
    {"caller_identity": {"version": 2, "caller_id_verified": True, "attestation": "A", "source": "trusted_carrier"}},
])
def test_legacy_or_malformed_saved_identity_does_not_become_verified(snapshot):
    module = importlib.import_module("app.domain.services.caller_identity")
    call = call_row()
    call["route_snapshot"] = snapshot
    assert module.caller_identity_projection(call)["caller_id_verified"] is False


@pytest.mark.parametrize("verified", [False, True])
def test_crm_activity_body_shows_verification_and_attestation(verified):
    from app.services.crm_sync_service import build_call_body
    body = build_call_body(detailed_call(verified), None, campaign_name=None)
    assert "Caller ID verification: " + ("verified" if verified else "unverified") in body
    assert "Caller ID attestation: " + ("A" if verified else "unknown") in body
    assert "P-Asserted-Identity" not in body


async def test_crm_loads_snapshot_through_existing_tenant_scoped_query():
    from app.services.crm_sync_service import CRMSyncService
    call = detailed_call(True)

    class LoadConnection(Connection):
        async def fetchrow(self, sql, *args):
            assert "FROM calls" in sql and "tenant_id = $2" in sql
            assert tuple(map(str, args)) == (CALL, TENANT)
            result = copy.deepcopy(call)
            if "route_snapshot" not in sql:
                result.pop("route_snapshot")
            return result

    loaded = await CRMSyncService(db_client=object(), db_pool=Pool(LoadConnection(call)))._load_call(TENANT, CALL)
    assert loaded["route_snapshot"] == call["route_snapshot"]


async def test_existing_explicit_crm_link_can_receive_an_unverified_activity_without_contact_mutation(monkeypatch):
    from app.services.crm_sync_service import CRMSyncService
    from unittest.mock import AsyncMock
    service = CRMSyncService(db_client=object(), db_pool=object())
    connector = NS(external_account_id="synthetic-account", connector_id=CALL,
                   search_contact=AsyncMock(), create_contact=AsyncMock())
    linked = {"id": CALL, "custom_fields": {"crm_destinations": {"salesforce": {
        "account_id": "synthetic-account", "contact_id": "explicit-owner-linked-contact"}}}}
    monkeypatch.setattr(service, "_remember_contact_id", AsyncMock())
    result = await service._resolve_contact(TENANT, "salesforce", connector, call_row(), linked, {}, body_for_new="synthetic")
    assert result == "explicit-owner-linked-contact"
    connector.search_contact.assert_not_awaited()
    connector.create_contact.assert_not_awaited()
    service._remember_contact_id.assert_not_awaited()
