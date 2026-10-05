"""Actual admin route/resolver/provider reads against synthetic DB/HTTP boundaries.

No provider writes, database writes, token refresh, or receipt adjudication.
This is not live-provider, real-database, or browser acceptance.
"""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Response

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin import calls
from app.infrastructure.connectors.crm.hubspot import HubSpotConnector
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector  # noqa: F401 - canonical factory registration
from app.services import connector_resolver

CALL = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
CONNECTOR = "33333333-3333-3333-3333-333333333333"
ACCOUNT = "44444444-4444-4444-4444-444444444444"
OTHER = "55555555-5555-5555-5555-555555555555"
HTTP_CLIENT = httpx.AsyncClient


class ReadDB:
    def __init__(self, provider="hubspot"):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.rows = {
            "calls": [{"id": CALL, "tenant_id": TENANT, "status": "completed",
                       "created_at": future, "phone_number": "+15555550100"}],
            "crm_deliveries": [{"tenant_id": TENANT, "call_id": CALL, "provider": provider,
                "phase": "creating_call", "status": "unknown", "updated_at": future,
                "destination_connector_id": CONNECTOR, "destination_account_id": "original-org",
                "remote_contact_id": "contact-1", "remote_call_id": None,
                "contact_effect": {"arguments": {"email": "never-show@example.invalid"}},
                "last_error": "Bearer must-never-display"}],
            "connectors": [{"id": CONNECTOR, "tenant_id": TENANT, "provider": provider,
                "type": "crm", "status": "active", "created_at": future,
                "config": {"instance_url": "https://synthetic.my.salesforce.com"}}],
            "connector_accounts": [{"id": ACCOUNT, "connector_id": CONNECTOR,
                "tenant_id": TENANT, "status": "active", "external_account_id": "original-org",
                "access_token_encrypted": "synthetic-ciphertext", "refresh_token_encrypted": "never-decrypt",
                "token_expires_at": future, "last_refreshed_at": future}],
            "tenants": [{"id": TENANT, "business_name": "Synthetic business"}],
            "call_legs": [],
        }
        self.reads = []
        self.fail_table = None

    def table(self, name):
        return ReadQuery(self, name)


class ReadQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.one, self.maximum = db, table, [], False, None

    def select(self, columns):
        self.columns = columns
        return self

    def eq(self, key, value):
        self.filters.append((key, str(value)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def single(self):
        self.one = True
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def execute(self):
        self.db.reads.append((self.table, self.columns, tuple(self.filters), self.maximum))
        if self.table == self.db.fail_table:
            return SimpleNamespace(data=None, error="synthetic-secret DB error")
        rows = [copy.deepcopy(row) for row in self.db.rows[self.table]
                if all(str(row.get(key)) == value for key, value in self.filters)]
        if self.maximum is not None:
            rows = rows[:self.maximum]
        return SimpleNamespace(data=(rows[0] if rows else None) if self.one else rows, error=None)

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database operation: {name}")


@pytest.fixture
def boundaries(monkeypatch):
    def decrypt(value):
        assert value == "synthetic-ciphertext", "Inspection must never decrypt refresh tokens"
        return "synthetic-read-token"

    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: SimpleNamespace(decrypt=decrypt))
    refresh = AsyncMock(side_effect=AssertionError("Inspection must not refresh tokens"))
    monkeypatch.setattr(connector_resolver, "_refresh_and_store", refresh)
    requests = []

    def install(provider="hubspot", status=200, body=None, error=None):
        if body is None:
            body = {"results": [{"id": "activity-1"}]} if provider == "hubspot" else {"records": [{"Id": "activity-1"}]}

        def respond(request):
            requests.append(request)
            if provider == "hubspot":
                assert request.method == "POST" and request.url.path == "/crm/v3/objects/calls/search"
                assert json.loads(request.content) == {
                    "filterGroups": [{"filters": [{"propertyName": "hs_call_title", "operator": "EQ",
                                                  "value": f"Talky.ai call {CALL}"}]}],
                    "properties": ["hs_call_title"], "limit": 2,
                }
            else:
                assert request.method == "GET" and request.url.path.endswith("/query")
                assert request.url.host == "synthetic.my.salesforce.com"
                assert request.url.params["q"] == f"SELECT Id FROM Task WHERE Subject = 'Talky.ai call {CALL}' LIMIT 2"
            assert request.headers["Authorization"] == "Bearer synthetic-read-token"
            if error:
                raise error
            return httpx.Response(status, json=body)

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(
            transport=httpx.MockTransport(respond), **kwargs))
    return install, requests, refresh


async def inspect(db, provider="hubspot"):
    before = copy.deepcopy(db.rows)
    response = Response()
    observed = await calls.inspect_admin_crm_delivery(
        UUID(CALL), provider, response, SimpleNamespace(), db)
    assert db.rows == before, "Inspection must not mutate saved state"
    assert response.headers["cache-control"] == "no-store"
    assert observed.call_id == CALL and observed.provider == provider
    assert datetime.fromisoformat(observed.observed_at).tzinfo is not None
    assert "synthetic-ciphertext" not in observed.model_dump_json()
    assert "synthetic-read-token" not in observed.model_dump_json()
    return observed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
async def test_actual_original_account_reference_read_is_observation_only(boundaries, provider):
    install, requests, refresh = boundaries
    install(provider)
    db = ReadDB(provider)
    observed = await inspect(db, provider)
    assert observed.outcome == "observed_reference"
    assert observed.reason == "reference_observed_only" and observed.observed_remote_id == "activity-1"
    assert db.rows["crm_deliveries"][0]["status"] == "unknown"
    assert len(requests) == 1
    refresh.assert_not_awaited()
    account_query = next(row for row in db.reads if row[0] == "connector_accounts")
    assert set(account_query[2]) == {("tenant_id", TENANT), ("connector_id", CONNECTOR),
                                   ("status", "active"), ("external_account_id", "original-org")}
    assert account_query[3] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("shape,outcome,reason", [
    ("empty", "no_match", "absence_is_inconclusive"),
    ("duplicate", "ambiguous", "multiple_references"),
    ("malformed", "unavailable", "provider_read_unavailable"),
])
async def test_provider_search_outcomes_do_not_adjudicate_receipt(boundaries, provider, shape, outcome, reason):
    install, requests, _ = boundaries
    key, id_key = ("results", "id") if provider == "hubspot" else ("records", "Id")
    rows = [] if shape == "empty" else [{id_key: "one"}, {id_key: "two"}] if shape == "duplicate" else [{}]
    install(provider, body={key: rows})
    observed = await inspect(ReadDB(provider), provider)
    assert (observed.outcome, observed.reason) == (outcome, reason)
    assert observed.observed_remote_id is None and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("status", [401, 403, 408, 429, 500])
async def test_provider_rejection_is_unavailable_without_refresh_retry_or_health_write(boundaries, provider, status):
    install, requests, refresh = boundaries
    install(provider, status=status, body={"error": "private synthetic provider response"})
    observed = await inspect(ReadDB(provider), provider)
    assert observed.outcome == "unavailable" and observed.reason == "provider_read_unavailable"
    assert "private" not in observed.model_dump_json() and len(requests) == 1
    refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", [
    "missing_connector", "inactive_connector", "foreign_connector", "wrong_provider",
    "missing_account", "inactive_account", "foreign_account", "replaced_account",
    "duplicate_account", "expired", "near_expiry", "missing_expiry", "malformed_expiry",
    "bad_ciphertext", "connector_lookup_error", "account_lookup_error",
])
async def test_original_authorization_must_be_unique_active_and_nonexpired(boundaries, issue):
    install, requests, refresh = boundaries
    install()
    db = ReadDB()
    parent, account = db.rows["connectors"][0], db.rows["connector_accounts"][0]
    if issue == "missing_connector": db.rows["connectors"] = []
    elif issue == "inactive_connector": parent["status"] = "disconnected"
    elif issue == "foreign_connector": parent["tenant_id"] = OTHER
    elif issue == "wrong_provider": parent["provider"] = "salesforce"
    elif issue == "missing_account": db.rows["connector_accounts"] = []
    elif issue == "inactive_account": account["status"] = "revoked"
    elif issue == "foreign_account": account["tenant_id"] = OTHER
    elif issue == "replaced_account": account["external_account_id"] = "replacement-org"
    elif issue == "duplicate_account": db.rows["connector_accounts"].append({**account, "id": OTHER})
    elif issue == "expired": account["token_expires_at"] = "2020-01-01T00:00:00Z"
    elif issue == "near_expiry": account["token_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    elif issue == "missing_expiry": account["token_expires_at"] = None
    elif issue == "malformed_expiry": account["token_expires_at"] = "not-a-time"
    elif issue == "bad_ciphertext": account["access_token_encrypted"] = "bad-ciphertext"
    elif issue == "connector_lookup_error": db.fail_table = "connectors"
    elif issue == "account_lookup_error": db.fail_table = "connector_accounts"
    observed = await inspect(db)
    assert (observed.outcome, observed.reason) == ("unavailable", "original_authorization_unavailable")
    assert not requests
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_newer_different_connector_and_account_are_never_substituted(boundaries):
    install, requests, _ = boundaries
    install()
    db = ReadDB()
    db.rows["connectors"].insert(0, {**db.rows["connectors"][0], "id": OTHER})
    db.rows["connector_accounts"].insert(0, {**db.rows["connector_accounts"][0], "id": OTHER,
                                         "external_account_id": "new-org"})
    observed = await inspect(db)
    assert observed.outcome == "observed_reference" and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("issue", ["missing", "duplicate", "foreign_tenant", "foreign_call", "missing_account",
                                   "missing_connector", "invalid_connector", "legacy_phase", "contact_phase",
                                   "complete_phase", "pending_status", "receipt_lookup_error"])
async def test_uninspectable_receipt_never_resolves_current_credentials(boundaries, issue):
    install, requests, _ = boundaries
    install()
    db = ReadDB()
    receipt = db.rows["crm_deliveries"][0]
    if issue == "missing": db.rows["crm_deliveries"] = []
    elif issue == "duplicate": db.rows["crm_deliveries"].append(copy.deepcopy(receipt))
    elif issue == "foreign_tenant": receipt["tenant_id"] = OTHER
    elif issue == "foreign_call": receipt["call_id"] = OTHER
    elif issue == "missing_account": receipt["destination_account_id"] = None
    elif issue == "missing_connector": receipt["destination_connector_id"] = None
    elif issue == "invalid_connector": receipt["destination_connector_id"] = "not-a-uuid"
    elif issue == "legacy_phase": receipt["phase"] = "legacy_unverified"
    elif issue == "contact_phase": receipt["phase"] = "creating_contact"
    elif issue == "complete_phase": receipt["phase"] = "complete"
    elif issue == "pending_status": receipt["status"] = "pending"
    elif issue == "receipt_lookup_error": db.fail_table = "crm_deliveries"
    observed = await inspect(db)
    assert observed.reason == "original_receipt_unavailable"
    assert all(row[0] not in {"connectors", "connector_accounts"} for row in db.reads)
    assert not requests


@pytest.mark.asyncio
async def test_arbitrary_value_error_is_not_duplicate_reference_ambiguity(boundaries, monkeypatch):
    boundaries[0]()
    monkeypatch.setattr(HubSpotConnector, "find_call_by_reference", AsyncMock(side_effect=ValueError("secret invalid config")))
    observed = await inspect(ReadDB())
    assert observed.outcome == "unavailable" and observed.reason == "provider_read_unavailable"
    assert "secret" not in observed.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("invalid_id", [None, True, False, 17, "", "   ", "bad\nidentifier", "x" * 257])
async def test_malformed_provider_id_never_becomes_an_observed_reference(boundaries, provider, invalid_id):
    install, requests, _ = boundaries
    key, id_key = ("results", "id") if provider == "hubspot" else ("records", "Id")
    install(provider, body={key: [{id_key: invalid_id}]})
    observed = await inspect(ReadDB(provider), provider)
    assert observed.outcome == "unavailable" and observed.reason == "provider_read_unavailable"
    assert observed.observed_remote_id is None and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("rows", [[{}, {}], [None, None], ["one", "two"]])
async def test_malformed_multirow_response_is_not_duplicate_reference_ambiguity(boundaries, provider, rows):
    install, requests, _ = boundaries
    install(provider, body={"results" if provider == "hubspot" else "records": rows})
    observed = await inspect(ReadDB(provider), provider)
    assert observed.outcome == "unavailable" and observed.reason == "provider_read_unavailable"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
@pytest.mark.parametrize("invalid_id", ["bad\nidentifier", "x" * 257])
async def test_invalid_multirow_ids_are_unavailable_before_duplicate_classification(boundaries, provider, invalid_id):
    install, requests, _ = boundaries
    key, id_key = ("results", "id") if provider == "hubspot" else ("records", "Id")
    install(provider, body={key: [{id_key: invalid_id}, {id_key: "valid-id"}]})
    observed = await inspect(ReadDB(provider), provider)
    assert observed.outcome == "unavailable" and observed.reason == "provider_read_unavailable"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_canonical_postgres_uuid_values_preserve_exact_original_identity(boundaries):
    boundaries[0]()
    db = ReadDB()
    for rows in db.rows.values():
        for row in rows:
            for key in ("id", "tenant_id", "call_id", "connector_id", "destination_connector_id"):
                if key in row:
                    row[key] = UUID(row[key])
    observed = await inspect(db)
    assert observed.outcome == "observed_reference"
    detail = await calls.get_admin_call_detail(CALL, SimpleNamespace(), db)
    assert detail.crm_deliveries[0].destination_connector_id == CONNECTOR


@pytest.mark.asyncio
async def test_timeout_is_inconclusive_and_external_cancellation_propagates(boundaries, monkeypatch):
    install, requests, _ = boundaries
    install(error=httpx.ReadTimeout("synthetic timeout"))
    observed = await inspect(ReadDB())
    assert observed.outcome == "unavailable" and len(requests) == 1
    entered = asyncio.Event()

    async def wait_forever(_self, _reference):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(HubSpotConnector, "find_call_by_reference", wait_forever)
    task = asyncio.create_task(inspect(ReadDB()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_saved_detail_projection_has_no_provider_call_raw_arguments_or_errors(boundaries):
    boundaries[0]()
    db = ReadDB()
    before = copy.deepcopy(db.rows)
    detail = await calls.get_admin_call_detail(CALL, SimpleNamespace(), db)
    assert detail.crm_receipts_available is True and len(detail.crm_deliveries) == 1
    receipt = detail.crm_deliveries[0]
    assert receipt.inspection_available is True and receipt.status == "unknown"
    assert receipt.destination_account_id == "original-org"
    assert "never-show" not in detail.model_dump_json() and "must-never-display" not in detail.model_dump_json()
    assert not boundaries[1] and db.rows == before
    db.fail_table = "crm_deliveries"
    detail = await calls.get_admin_call_detail(CALL, SimpleNamespace(), db)
    assert detail.crm_receipts_available is False and detail.crm_deliveries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role,status", [("platform_admin", 200), ("tenant_admin", 403),
                                         ("partner_admin", 403), ("user", 403)])
async def test_actual_route_requires_platform_admin_and_ignores_supplied_identity(boundaries, role, status):
    install, requests, _ = boundaries
    install()
    db = ReadDB()
    app = FastAPI()
    app.include_router(calls.router, prefix="/admin")
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id=OTHER, tenant_id=OTHER, email="operator@example.invalid", role=role)
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    async with HTTP_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/admin/calls/{CALL}/crm-deliveries/hubspot/inspection",
            params={"tenant_id": OTHER, "connector_id": OTHER, "external_account_id": "replacement", "reference": OTHER})
    assert response.status_code == status
    assert len(requests) == (1 if status == 200 else 0)
    if status == 200:
        assert response.json()["call_id"] == CALL and response.headers["cache-control"] == "no-store"
        assert db.reads[1][2] == (("tenant_id", TENANT), ("call_id", CALL))
    else:
        assert db.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"force_refresh": True}, {"connector_id": None}, {"provider": None},
    {"external_account_id": None}, {"reviewed_authorization": True},
])
async def test_read_only_resolver_rejects_incomplete_or_refreshing_requests_before_db(boundaries, kwargs):
    db = ReadDB()
    pins = {"provider": "hubspot", "connector_id": CONNECTOR, "external_account_id": "original-org", "read_only": True}
    pins.update(kwargs)
    with pytest.raises(connector_resolver.ConnectorNotConnectedError):
        await connector_resolver.resolve_active_connector(db, TENANT, "crm", **pins)
    assert db.reads == []
