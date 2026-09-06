"""Connectors endpoint — the Salesforce card lives beside HubSpot without
sharing its rows: card keys, provider-scoped disconnect, callback redirects."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import connectors as ce  # noqa: E402

TENANT = "44444444-4444-4444-4444-444444444444"


def _resp(data=None, error=None):
    return SimpleNamespace(data=data, error=error)


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name, self.op, self.filters, self.payload = db, table, "select", [], None

    def select(self, *a, **k):
        self.op = "select"; return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload; return self

    def update(self, payload):
        self.op, self.payload = "update", payload; return self

    def delete(self):
        self.op = "delete"; return self

    def eq(self, col, val):
        self.filters.append((col, val)); return self

    def neq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        return self

    def execute(self):
        self.db.calls.append((self.table_name, self.op, list(self.filters), self.payload))
        queue = self.db.responses.get((self.table_name, self.op)) or []
        return queue.pop(0) if queue else _resp(data=[])


class FakeDB:
    def __init__(self, responses=None):
        self.responses = {k: list(v) for k, v in (responses or {}).items()}
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def test_card_providers_map_salesforce_to_a_crm_row():
    assert ce.CARD_PROVIDERS["salesforce"] == ("crm", "salesforce")
    assert ce.CARD_PROVIDERS["crm"] == ("crm", "hubspot")
    assert ce.CARD_KEY_BY_PROVIDER["salesforce"] == "salesforce"
    assert ce.CARD_KEY_BY_PROVIDER["hubspot"] == "crm"
    # Legacy map is untouched for anything still importing it.
    assert ce.DEFAULT_PROVIDER_BY_TYPE["crm"] == "hubspot"


def test_card_key_for_row_routes_by_provider_then_type():
    assert ce._card_key_for_row({"type": "crm", "provider": "salesforce"}) == "salesforce"
    assert ce._card_key_for_row({"type": "crm", "provider": "hubspot"}) == "crm"
    assert ce._card_key_for_row({"type": "crm", "provider": "zoho"}) == "crm"
    assert ce._card_key_for_row({"type": "sms", "provider": "twilio"}) is None


def test_frontend_callback_url_reports_the_salesforce_card_key():
    url = ce._frontend_callback_url("https://app.example", status="success", provider="salesforce")
    assert url == "https://app.example/connectors/callback?status=success&type=salesforce&provider=salesforce"
    hub = ce._frontend_callback_url("https://app.example", status="success", provider="hubspot")
    assert "type=crm" in hub and "provider=hubspot" in hub


def test_salesforce_card_is_advertised_only_when_the_server_can_run_oauth(monkeypatch):
    monkeypatch.delenv("SALESFORCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SALESFORCE_CLIENT_SECRET", raising=False)
    assert ce._salesforce_card_advertised([]) is False
    # ...or when the tenant already has Salesforce rows to manage.
    assert ce._salesforce_card_advertised([{"provider": "salesforce", "type": "crm"}]) is True
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "x")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "y")
    assert ce._salesforce_card_advertised([]) is True


def _user():
    return SimpleNamespace(tenant_id=TENANT, id="user-1")


def test_status_lists_hubspot_and_salesforce_as_separate_cards(monkeypatch):
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "x")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "y")
    db = FakeDB({
        ("connectors", "select"): [_resp([
            {"id": "c-sf", "type": "crm", "provider": "salesforce", "status": "error", "created_at": "2026-09-07T10:00:00Z"},
            {"id": "c-hs", "type": "crm", "provider": "hubspot", "status": "expired", "created_at": "2026-09-06T10:00:00Z"},
        ])],
    })
    out = asyncio.get_event_loop().run_until_complete(ce.list_connector_statuses(current_user=_user(), db_client=db))
    by_type = {item.type: item for item in out.items}
    assert set(by_type) == {"calendar", "email", "crm", "drive", "salesforce"}
    assert by_type["crm"].provider == "hubspot" and by_type["crm"].status == "expired"
    assert by_type["salesforce"].provider == "salesforce" and by_type["salesforce"].status == "error"


def test_status_hides_the_salesforce_card_when_the_server_is_not_configured(monkeypatch):
    monkeypatch.delenv("SALESFORCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SALESFORCE_CLIENT_SECRET", raising=False)
    db = FakeDB({("connectors", "select"): [_resp([])]})
    out = asyncio.get_event_loop().run_until_complete(ce.list_connector_statuses(current_user=_user(), db_client=db))
    assert [i.type for i in out.items] == ["calendar", "email", "crm", "drive"]


def test_disconnect_crm_card_only_removes_hubspot_rows():
    db = FakeDB({("connectors", "select"): [_resp([{"id": "c-hs", "provider": "hubspot"}])]})
    out = asyncio.get_event_loop().run_until_complete(
        ce.disconnect_connector_by_type("crm", current_user=_user(), db_client=db)
    )
    assert out["removed"] == 1
    select = db.calls[0]
    assert ("type", "crm") in select[2] and ("provider", "hubspot") in select[2]
    deleted = [c for c in db.calls if c[1] == "delete"]
    assert [c[0] for c in deleted] == ["connector_accounts", "connectors"]
    assert ("connector_id", "c-hs") in deleted[0][2] and ("id", "c-hs") in deleted[1][2]


def test_disconnect_salesforce_card_targets_salesforce_rows_only():
    db = FakeDB({("connectors", "select"): [_resp([])]})
    out = asyncio.get_event_loop().run_until_complete(
        ce.disconnect_connector_by_type("salesforce", current_user=_user(), db_client=db)
    )
    assert out["removed"] == 0
    assert ("type", "crm") in db.calls[0][2] and ("provider", "salesforce") in db.calls[0][2]


def test_unknown_card_key_is_a_400():
    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop().run_until_complete(
            ce.disconnect_connector_by_type("zoho", current_user=_user(), db_client=FakeDB())
        )
    assert exc.value.status_code == 400


def test_authorize_salesforce_without_server_credentials_is_a_503(monkeypatch):
    monkeypatch.delenv("SALESFORCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SALESFORCE_CLIENT_SECRET", raising=False)
    request = SimpleNamespace(base_url="http://testserver/")
    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop().run_until_complete(
            ce.authorize_connector_by_type(
                "salesforce", request, redirect_uri="http://app/connectors/salesforce/callback",
                current_user=_user(), db_client=FakeDB(),
            )
        )
    assert exc.value.status_code == 503
    assert "SALESFORCE_CLIENT_ID" in str(exc.value.detail)


def test_authorize_salesforce_inserts_a_crm_row_for_the_salesforce_provider(monkeypatch):
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "sec")

    class FakeStateManager:
        async def create_state(self, **kw):
            return {"state": "S", "code_verifier": "V", "code_challenge": "C", "code_challenge_method": "S256"}

    monkeypatch.setattr(ce, "get_oauth_state_manager", lambda: FakeStateManager())
    db = FakeDB({("connectors", "insert"): [_resp([{"id": "new-sf"}])]})
    request = SimpleNamespace(base_url="http://testserver/")
    out = asyncio.get_event_loop().run_until_complete(
        ce.authorize_connector_by_type(
            "salesforce", request, redirect_uri="http://app/connectors/salesforce/callback",
            current_user=_user(), db_client=db,
        )
    )
    inserted = db.calls[0][3]
    assert inserted["type"] == "crm" and inserted["provider"] == "salesforce" and inserted["name"] == "Salesforce"
    assert out.authorization_url.startswith("https://login.salesforce.com/services/oauth2/authorize?")
    assert "code_challenge=C" in out.authorization_url
