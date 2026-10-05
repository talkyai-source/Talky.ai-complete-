"""Exact saved calendar observations: real routes/resolver/providers, synthetic IO."""
import copy
import socket
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Response

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin import actions
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
from app.services import connector_resolver
from tests.unit.test_admin_gmail_inspection import ReadDB, ACTION, TENANT, CONNECTOR, ACCOUNT, OTHER

HTTP_CLIENT = httpx.AsyncClient
EVENT = "saved/event+reference=?#%"
PROVIDERS = {"google_calendar": GoogleCalendarConnector, "outlook_calendar": OutlookCalendarConnector}


def database(provider="google_calendar", action="book_meeting", shape="inner"):
    db = ReadDB(shape)
    row = db.rows["assistant_actions"][0]
    row["type"] = action
    output = row["output_data"]
    if shape == "nested":
        output = output["provider_result"]
    output["provider"] = provider
    output["external_event_id"] = EVENT
    output.pop("message_id")
    intent = row["input_data"]
    (intent["reviewed_connector"] if shape == "inner" else intent["parameters"]["_reviewed_connector"])["provider"] = provider
    db.rows["connectors"][0].update(provider=provider, type="calendar")
    return db


@pytest.fixture(autouse=True)
async def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("No real network or DB connection is permitted")
    with monkeypatch.context() as patch:
        for name in ("connect", "connect_ex"):
            patch.setattr(socket.socket, name, denied)
        patch.setattr(socket, "getaddrinfo", denied)
        yield


@pytest.fixture
def boundaries(monkeypatch):
    def decrypt(value):
        assert value == "synthetic-ciphertext"
        return "synthetic-original-token"
    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: SimpleNamespace(decrypt=decrypt))
    refresh = AsyncMock(side_effect=AssertionError("No refresh/write"))
    monkeypatch.setattr(connector_resolver, "_refresh_and_store", refresh)
    requests = []

    def install(status=200, body=None, error=None):
        if body is None:
            body = {"id": EVENT, "status": "cancelled", "isCancelled": True, "body": "private content"}
        def respond(request):
            requests.append(request)
            assert request.method == "GET" and request.content == b""
            assert request.headers["Authorization"] == "Bearer synthetic-original-token"
            assert "Prefer" not in request.headers
            if error:
                raise error
            return httpx.Response(status, json=body)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    install()
    return install, requests, refresh


async def inspect(db):
    before, response = copy.deepcopy(db.rows), Response()
    result = await actions.inspect_admin_calendar_action(UUID(ACTION), response, SimpleNamespace(), db)
    assert db.rows == before and response.headers["cache-control"] == "no-store"
    assert result.action_id == ACTION and datetime.fromisoformat(result.observed_at).tzinfo is not None
    assert "private" not in result.model_dump_json() and "synthetic-original-token" not in result.model_dump_json()
    assert set(result.model_dump()) == {"action_id", "outcome", "reason", "observed_at", "observed_event_id"}
    return result


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_exact_provider_reference_is_read_only_and_encoded(boundaries, provider):
    connector = PROVIDERS[provider](TENANT, CONNECTOR)
    await connector.set_access_token("synthetic-original-token")
    assert await connector.get_event_reference(EVENT) == EVENT
    request = boundaries[1][0]
    prefix = "/calendar/v3/calendars/primary/events/" if provider == "google_calendar" else "/v1.0/me/calendar/events/"
    assert request.url.raw_path.split(b"?")[0] == (prefix + "saved%2Fevent%2Breference%3D%3F%23%25").encode()
    assert dict(request.url.params) == ({"fields": "id"} if provider == "google_calendar" else {"$select": "id"})
    assert len(boundaries[1]) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("action", ["book_meeting", "update_meeting", "cancel_meeting"])
@pytest.mark.parametrize("shape", ["inner", "outer", "nested"])
async def test_saved_bundles_observe_original_authorization_without_status_changes(boundaries, provider, action, shape):
    db = database(provider, action, shape)
    result = await inspect(db)
    assert (result.outcome, result.reason, result.observed_event_id) == ("observed_event", "exact_event_observed_only", EVENT)
    boundaries[2].assert_not_awaited()
    assert len(boundaries[1]) == 1
    assert {read[0] for read in db.reads} == {"assistant_actions", "connectors", "connector_accounts"}
    account_read = next(query for query in db.reads if query[0] == "connector_accounts")
    assert set(account_read[2]) == {("connector_id", CONNECTOR), ("tenant_id", TENANT), ("status", "active"), ("id", ACCOUNT)}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("status,outcome", [(404, "not_observed"), (410, "not_observed"), (401, "unavailable"), (403, "unavailable"), (429, "unavailable"), (500, "unavailable"), (302, "unavailable")])
async def test_provider_errors_never_prove_action_completion(boundaries, provider, status, outcome):
    boundaries[0](status=status, body={"error": "private diagnostic"})
    result = await inspect(database(provider))
    assert result.outcome == outcome and result.observed_event_id is None
    assert result.reason == ("absence_is_inconclusive" if outcome == "not_observed" else "provider_read_unavailable")
    assert len(boundaries[1]) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("body", [{"id": EVENT}, {"id": "different"}, {}, [], {"id": 1}])
async def test_id_only_tombstone_is_valid_but_malformed_or_mismatched_is_not(boundaries, provider, body):
    boundaries[0](body=body)
    result = await inspect(database(provider))
    assert result.outcome == ("observed_event" if body == {"id": EVENT} else "unavailable")


@pytest.mark.parametrize("variant", ["missing_id", "missing_proof", "wrong_type", "wrong_provider", "wrong_tenant", "wrong_row", "wrong_action", "input_only", "split", "conflicting", "partial_wrapper", "malformed_nested", "bulk", "event_ids", "external_event_ids", "intent_conflict", "blank", "dot", "control"])
async def test_missing_or_conflicting_saved_evidence_never_resolves_or_reads(boundaries, variant):
    db = database()
    row, output = db.rows["assistant_actions"][0], db.rows["assistant_actions"][0]["output_data"]
    if variant == "missing_id": output.pop("external_event_id")
    elif variant == "missing_proof": output.pop("identity_version")
    elif variant == "wrong_type": row["type"] = "send_email"
    elif variant == "wrong_provider": output["provider"] = "gmail"
    elif variant == "wrong_tenant": output["tenant_id"] = OTHER
    elif variant == "wrong_row": row["connector_id"] = OTHER
    elif variant == "wrong_action": output["action_id"] = OTHER
    elif variant == "input_only": row["output_data"] = {"external_event_id": EVENT}
    elif variant == "split": row["output_data"] = {"external_event_id": output.pop("external_event_id"), "provider_result": output}
    elif variant == "conflicting": output["provider_result"] = {**output, "external_event_id": "other"}
    elif variant == "partial_wrapper": row["output_data"] = {"account_row_id": ACCOUNT, "provider_result": output}
    elif variant == "malformed_nested": output["provider_result"] = "bad"
    elif variant == "bulk": output["receipts"] = [copy.deepcopy(output)]
    elif variant in ("event_ids", "external_event_ids"): output[variant] = [EVENT, "other"]
    elif variant == "intent_conflict": row["input_data"]["reviewed_connector"]["account_row_id"] = OTHER
    else: output["external_event_id"] = {"blank": " ", "dot": "..", "control": "event\n"}[variant]
    result = await inspect(db)
    assert result.outcome == "unavailable" and result.reason == "saved_proof_unavailable"
    assert not boundaries[1] and len(db.reads) == 1


@pytest.mark.parametrize("variant", ["expired", "inactive", "replacement", "db_error"])
async def test_original_authorization_unavailable_never_refreshes_or_substitutes(boundaries, variant):
    db = database()
    account = db.rows["connector_accounts"][0]
    if variant == "expired": account["token_expires_at"] = "2020-01-01T00:00:00Z"
    elif variant == "inactive": account["status"] = "revoked"
    elif variant == "replacement": account["id"] = OTHER
    else: db.fail_table = "connector_accounts"
    result = await inspect(db)
    assert result.outcome == "unavailable" and result.reason == "original_authorization_unavailable"
    assert not boundaries[1]
    boundaries[2].assert_not_awaited()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("event_id", ["", " ", ".", "..", "a\n", "a" * 513, None, 1])
async def test_provider_rejects_invalid_path_before_http(boundaries, provider, event_id):
    connector = PROVIDERS[provider](TENANT, CONNECTOR)
    with pytest.raises(ValueError):
        await connector.get_event_reference(event_id)
    assert not boundaries[1]


@pytest.mark.parametrize("role,allowed", [("platform_admin", True), ("tenant_admin", False), ("partner_admin", False)])
async def test_detail_and_route_are_platform_only_with_no_supplied_account_override(boundaries, role, allowed):
    db, app = database(), FastAPI()
    app.include_router(actions.router, prefix="/admin")
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(id=OTHER, tenant_id=TENANT, email="operator@example.invalid", role=role)
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    async with HTTP_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/admin/actions/{ACTION}")
        assert detail.status_code == 200 and detail.json()["calendar_inspection_available"] is allowed
        assert detail.json()["email_inspection_available"] is False and not boundaries[1]
        response = await client.get(f"/admin/actions/{ACTION}/calendar-inspection", params={"account_id": OTHER, "external_event_id": "wrong"})
    assert response.status_code == (200 if allowed else 403)
    assert len(boundaries[1]) == (1 if allowed else 0)


async def test_timeout_and_wrong_typed_error_stay_unavailable(boundaries, monkeypatch):
    boundaries[0](error=httpx.ReadTimeout("private timeout"))
    assert (await inspect(database())).outcome == "unavailable"
    monkeypatch.setattr(GoogleCalendarConnector, "get_event_reference", AsyncMock(side_effect=ConnectorProviderError(provider="gmail", operation="get_email", category="not_found", status_code=404, message="private error")))
    assert (await inspect(database())).outcome == "unavailable"
