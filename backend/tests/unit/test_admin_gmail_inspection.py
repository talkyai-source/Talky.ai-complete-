"""Saved Gmail observations through actual routes/resolver/provider, synthetic IO only."""
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
from app.api.v1.endpoints.admin import actions
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.services import connector_resolver

ACTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
CONNECTOR = "33333333-3333-3333-3333-333333333333"
ACCOUNT = "44444444-4444-4444-4444-444444444444"
OTHER = "55555555-5555-5555-5555-555555555555"
MESSAGE = "saved-message-1"
HTTP_CLIENT = httpx.AsyncClient


def proof():
    return {"identity_version": "authorization_row_v1", "tenant_id": TENANT,
            "provider": "gmail", "connector_id": CONNECTOR, "account_row_id": ACCOUNT}


class ReadDB:
    def __init__(self, shape="inner"):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        output = {**proof(), "message_id": MESSAGE, "thread_id": "thread-1", "recipient_count": 1}
        intent = {"reviewed_connector": proof(), "subject": "private subject", "to": ["private@example.invalid"]}
        if shape != "inner":
            output.update(action_id=ACTION, child_action_id=OTHER, status="accepted", success=True)
            intent = {"request_hash": "synthetic-existing-hash", "parameters": {"_reviewed_connector": proof()}}
        if shape == "nested":
            output = {"action_id": ACTION, "status": "unknown", "success": False, "provider_result": output}
        self.rows = {
            "assistant_actions": [{"id": ACTION, "tenant_id": TENANT, "type": "send_email", "status": "unknown",
                "connector_id": None, "input_data": intent, "output_data": output, "created_at": future,
                "tenants": {"business_name": "Synthetic business"}}],
            "connectors": [{"id": CONNECTOR, "tenant_id": TENANT, "provider": "gmail", "type": "email",
                "status": "active", "created_at": future, "config": {}}],
            "connector_accounts": [{"id": ACCOUNT, "connector_id": CONNECTOR, "tenant_id": TENANT,
                "external_account_id": None, "status": "active", "access_token_encrypted": "synthetic-ciphertext",
                "refresh_token_encrypted": "never-decrypt", "token_expires_at": future, "last_refreshed_at": future}],
        }
        self.reads, self.fail_table = [], None

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
        self.db.reads.append((self.table, self.columns, tuple(self.filters)))
        if self.table == self.db.fail_table:
            return SimpleNamespace(data=None, error="private synthetic DB failure")
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
        assert value == "synthetic-ciphertext", "Never decrypt a refresh token or replacement account"
        return "synthetic-original-token"

    monkeypatch.setattr(connector_resolver, "get_encryption_service", lambda: SimpleNamespace(decrypt=decrypt))
    refresh = AsyncMock(side_effect=AssertionError("Inspection cannot refresh/write"))
    monkeypatch.setattr(connector_resolver, "_refresh_and_store", refresh)
    requests = []

    def install(status=200, body=None, error=None):
        if body is None:
            body = {"id": MESSAGE, "threadId": "private-thread", "labelIds": ["SENT"],
                    "snippet": "private email body", "payload": {"headers": [
                        {"name": "Subject", "value": "private subject"},
                        {"name": "To", "value": "private@example.invalid"}]}}

        def respond(request):
            requests.append(request)
            assert request.method == "GET" and request.url.path.endswith(f"/users/me/messages/{MESSAGE}")
            assert dict(request.url.params) == {"format": "full"}
            assert request.headers["Authorization"] == "Bearer synthetic-original-token"
            if error:
                raise error
            return httpx.Response(status, json=body)

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(
            transport=httpx.MockTransport(respond), **kwargs))
    install()
    return install, requests, refresh


async def inspect(db):
    before, response = copy.deepcopy(db.rows), Response()
    result = await actions.inspect_admin_email_action(UUID(ACTION), response, SimpleNamespace(), db)
    assert db.rows == before
    assert response.headers["cache-control"] == "no-store"
    assert result.action_id == ACTION
    assert datetime.fromisoformat(result.observed_at).tzinfo is not None
    assert "private" not in result.model_dump_json() and "synthetic-original-token" not in result.model_dump_json()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["inner", "outer", "nested"])
async def test_complete_saved_bundle_uses_exact_existing_get_without_writes(boundaries, shape):
    db = ReadDB(shape)
    result = await inspect(db)
    assert result.outcome == "observed_message" and result.reason == "exact_message_observed_only"
    assert result.observed_message_id == MESSAGE and len(boundaries[1]) == 1
    boundaries[2].assert_not_awaited()
    account_read = next(query for query in db.reads if query[0] == "connector_accounts")
    assert set(account_read[2]) == {("connector_id", CONNECTOR), ("tenant_id", TENANT), ("status", "active"), ("id", ACCOUNT)}
    assert [query[0] for query in db.reads].count("assistant_actions") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["partial_top", "partial_nested", "conflicting_id", "conflicting_row", "conflicting_external",
    "missing_id", "missing_row", "missing_version", "wrong_tenant", "wrong_action", "wrong_connector_column",
    "inner_intent_conflict", "outer_intent_conflict", "malformed_intent", "legacy_proof", "smtp", "calendar",
    "voice_partial", "voice_form", "bulk", "message_ids", "invalid_nested", "split_proof", "bad_id_path", "bad_id_type", "wrong_wrapper_action"])
async def test_ambiguous_or_incomplete_saved_evidence_never_loads_credentials(boundaries, case):
    db = ReadDB("nested" if case in {"partial_top", "split_proof", "wrong_wrapper_action"} else "inner")
    row = db.rows["assistant_actions"][0]
    output = row["output_data"]
    if case == "partial_top": output["provider"] = "gmail"
    elif case == "partial_nested": output["provider_result"] = {"provider": "gmail"}
    elif case in {"conflicting_id", "conflicting_row", "conflicting_external"}:
        output["provider_result"] = {**proof(), "message_id": MESSAGE}
        key, value = {"conflicting_id": ("message_id", "different"), "conflicting_row": ("account_row_id", OTHER),
                      "conflicting_external": ("external_account_id", "changed")}[case]
        output["provider_result"][key] = value
    elif case == "missing_id": output.pop("message_id")
    elif case == "missing_row": output.pop("account_row_id")
    elif case == "missing_version": output.pop("identity_version")
    elif case == "wrong_tenant": output["tenant_id"] = OTHER
    elif case == "wrong_action": output["action_id"] = OTHER
    elif case == "wrong_wrapper_action": output["action_id"] = OTHER
    elif case == "wrong_connector_column": row["connector_id"] = OTHER
    elif case == "inner_intent_conflict": row["input_data"]["reviewed_connector"]["account_row_id"] = OTHER
    elif case == "outer_intent_conflict": row["input_data"] = {"parameters": {"_reviewed_connector": {**proof(), "account_row_id": OTHER}}}
    elif case == "malformed_intent": row["input_data"]["reviewed_connector"] = []
    elif case == "legacy_proof": output.pop("identity_version"); output.pop("account_row_id"); output["external_account_id"] = "legacy-real-id"
    elif case == "smtp": output["provider"] = "smtp"
    elif case == "calendar": row["type"] = "book_meeting"
    elif case == "voice_partial": row["triggered_by"] = "voice"; row["output_data"] = {"provider": "gmail", "message_id": MESSAGE}
    elif case == "voice_form": row["type"] = "submit_form"
    elif case == "bulk": output["receipts"] = [{"message_id": MESSAGE}]
    elif case == "message_ids": output["message_ids"] = [MESSAGE]
    elif case == "invalid_nested": output["provider_result"] = "not-an-object"
    elif case == "split_proof": output["message_id"] = output["provider_result"].pop("message_id")
    elif case == "bad_id_path": output["message_id"] = "../profile"
    elif case == "bad_id_type": output["message_id"] = [MESSAGE]
    result = await inspect(db)
    assert result.outcome == "unavailable" and result.reason == "saved_proof_unavailable"
    assert not boundaries[1]
    assert all(query[0] == "assistant_actions" for query in db.reads)


@pytest.mark.asyncio
async def test_equivalent_complete_bundles_and_json_encoded_rows_are_supported(boundaries):
    db = ReadDB()
    row = db.rows["assistant_actions"][0]
    row["output_data"]["provider_result"] = copy.deepcopy(row["output_data"])
    row["input_data"] = json.dumps(row["input_data"])
    row["output_data"] = json.dumps(row["output_data"])
    assert (await inspect(db)).outcome == "observed_message"


@pytest.mark.asyncio
async def test_original_older_active_authorization_is_not_redirected_to_newer_account(boundaries):
    db = ReadDB()
    db.rows["connector_accounts"].insert(0, {**db.rows["connector_accounts"][0], "id": OTHER,
        "external_account_id": "replacement", "access_token_encrypted": "must-not-decrypt"})
    assert (await inspect(db)).outcome == "observed_message"
    assert len(boundaries[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing_parent", "revoked_parent", "foreign_parent", "wrong_provider", "missing_row",
    "revoked_row", "foreign_row", "expired", "near_expiry", "missing_expiry", "bad_expiry", "bad_ciphertext",
    "null_to_external", "external_to_null", "external_changed", "lookup_error"])
async def test_unavailable_original_authorization_has_no_provider_read(boundaries, case):
    db = ReadDB()
    row, parent = db.rows["connector_accounts"][0], db.rows["connectors"][0]
    action = db.rows["assistant_actions"][0]
    if case.startswith("external_"):
        action["output_data"]["external_account_id"] = "original"
        action["input_data"]["reviewed_connector"]["external_account_id"] = "original"
    if case == "missing_parent": db.rows["connectors"] = []
    elif case == "revoked_parent": parent["status"] = "revoked"
    elif case == "foreign_parent": parent["tenant_id"] = OTHER
    elif case == "wrong_provider": parent["provider"] = "smtp"
    elif case == "missing_row": row["id"] = OTHER
    elif case == "revoked_row": row["status"] = "revoked"
    elif case == "foreign_row": row["tenant_id"] = OTHER
    elif case == "expired": row["token_expires_at"] = "2020-01-01T00:00:00Z"
    elif case == "near_expiry": row["token_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    elif case == "missing_expiry": row["token_expires_at"] = None
    elif case == "bad_expiry": row["token_expires_at"] = "invalid"
    elif case == "bad_ciphertext": row["access_token_encrypted"] = "bad"
    elif case == "null_to_external": row["external_account_id"] = "new-external"
    elif case == "external_changed": row["external_account_id"] = "replacement"
    elif case == "lookup_error": db.fail_table = "connector_accounts"
    result = await inspect(db)
    assert result.outcome == "unavailable" and result.reason == "original_authorization_unavailable"
    assert not boundaries[1]
    boundaries[2].assert_not_awaited()


@pytest.mark.asyncio
async def test_native_postgres_uuid_values_and_original_external_id_are_supported(boundaries):
    db = ReadDB()
    row = db.rows["assistant_actions"][0]
    row["output_data"]["external_account_id"] = "original"
    row["input_data"]["reviewed_connector"]["external_account_id"] = "original"
    db.rows["connector_accounts"][0]["external_account_id"] = "original"
    for rows in db.rows.values():
        for item in rows:
            for key in ("id", "tenant_id", "connector_id"):
                if item.get(key): item[key] = UUID(item[key])
    assert (await inspect(db)).outcome == "observed_message"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 408, 429, 500])
async def test_actual_typed_provider_status_is_sanitized_and_never_retried(boundaries, status):
    install, requests, refresh = boundaries
    install(status, {"error": {"message": "private error body"}})
    result = await inspect(ReadDB())
    assert result.outcome == ("not_observed" if status == 404 else "unavailable")
    assert result.reason == ("absence_is_inconclusive" if status == 404 else "provider_read_unavailable")
    assert result.observed_message_id is None and len(requests) == 1
    refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("404 private message"),
    ConnectorProviderError(provider="gmail", operation="other", category="not_found", status_code=404, message="private"),
    ConnectorProviderError(provider="other", operation="get_email", category="not_found", status_code=404, message="private")])
async def test_only_actual_gmail_message_404_is_not_observed(boundaries, monkeypatch, failure):
    monkeypatch.setattr(GmailConnector, "get_email", AsyncMock(side_effect=failure))
    assert (await inspect(ReadDB())).outcome == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_id", ["different", None, True, [MESSAGE]])
async def test_malformed_or_different_provider_id_is_unavailable(boundaries, returned_id):
    boundaries[0](body={"id": returned_id})
    assert (await inspect(ReadDB())).outcome == "unavailable"


@pytest.mark.asyncio
async def test_timeout_and_cancellation_never_complete_or_retry(boundaries, monkeypatch):
    boundaries[0](error=httpx.ReadTimeout("private timeout"))
    assert (await inspect(ReadDB())).outcome == "unavailable"
    entered = asyncio.Event()

    async def wait_forever(_self, _id):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(GmailConnector, "get_email", wait_forever)
    task = asyncio.create_task(inspect(ReadDB()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("role,allowed", [("platform_admin", True), ("tenant_admin", False), ("partner_admin", False)])
async def test_detail_availability_and_inspection_authority_are_platform_only(boundaries, role, allowed):
    db, app = ReadDB(), FastAPI()
    app.include_router(actions.router, prefix="/admin")
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id=OTHER, tenant_id=TENANT, email="operator@example.invalid", role=role)
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    async with HTTP_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        detail = await client.get(f"/admin/actions/{ACTION}")
        assert detail.status_code == 200 and detail.json()["email_inspection_available"] is allowed
        assert not boundaries[1]
        response = await client.get(f"/admin/actions/{ACTION}/email-inspection",
            params={"tenant_id": OTHER, "account_id": OTHER, "connector_id": OTHER, "message_id": "wrong"})
    assert response.status_code == (200 if allowed else 403)
    assert len(boundaries[1]) == (1 if allowed else 0)
    if allowed:
        assert response.json()["observed_message_id"] == MESSAGE
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_missing_saved_proof_keeps_detail_button_unavailable(boundaries):
    db = ReadDB()
    db.rows["assistant_actions"][0]["output_data"].pop("message_id")
    detail = await actions.get_admin_action_detail(ACTION, SimpleNamespace(role="platform_admin"), db)
    assert detail.email_inspection_available is False and not boundaries[1]
