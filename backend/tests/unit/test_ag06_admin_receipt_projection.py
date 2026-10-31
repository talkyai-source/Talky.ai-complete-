"""Actual Admin detail HTTP projection with synthetic, tenant-filtered storage."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.admin import actions


class ReceiptDB:
    def __init__(self, row):
        self.row = row
        self.filters = []

    def table(self, name):
        assert name == "assistant_actions"
        return self

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def single(self):
        return self

    def execute(self):
        matches = all(str(self.row.get(key)) == str(value) for key, value in self.filters)
        return SimpleNamespace(data=deepcopy(self.row) if matches else None)


async def detail(*, status="unknown", output=None, role="tenant_admin", foreign=False):
    tenant_id, action_id = str(uuid4()), str(uuid4())
    row = {
        "id": action_id, "tenant_id": tenant_id, "type": "send_email", "status": status,
        "created_at": "2026-10-05T12:00:00Z", "tenants": {"business_name": "Synthetic"},
        "connectors": {"name": "Current display name is not the original account"},
        "connector_id": str(uuid4()),
        "input_data": {"recipient": "private@example.invalid", "token": "synthetic-private"},
        "output_data": output or {},
    }
    db = ReceiptDB(row)
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[actions.require_admin] = lambda: SimpleNamespace(
        id=str(uuid4()), role=role, tenant_id=str(uuid4()) if foreign else tenant_id)
    app.dependency_overrides[actions.get_db_client] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
        response = await client.get(f"/actions/{action_id}")
    return response, db


@pytest.mark.parametrize("status", ["unknown", "scheduled", "completed"])
async def test_detail_retains_status_and_sanitized_unconfirmed_original_receipt(status):
    response, db = await detail(status=status, output={
        "success": True, "confirmation_allowed": False, "status": "accepted",
        "provider_result": {"provider": "gmail", "connector_id": "original-connector",
                            "external_account_id": "original-account", "message_id": "original-message",
                            "access_token": "synthetic-private", "recipient": "private@example.invalid"},
        "body": "synthetic-private", "credentials": {"token": "synthetic-private"},
    })
    assert response.status_code == 200
    body = response.json()
    receipt = body["saved_receipt"]
    assert body["status"] == receipt["status"] == status
    assert receipt["action_id"] == db.row["id"]
    assert receipt["confirmation_allowed"] is False
    assert receipt["receipt"]["external_account_id"] == "original-account"
    assert receipt["receipt"]["connector_id"] == "original-connector"
    assert receipt["receipt"]["message_id"] == "original-message"
    assert "synthetic-private" not in str(receipt)
    assert "private@example.invalid" not in str(receipt)
    assert "Current display name" not in str(receipt)
    assert ("tenant_id", db.row["tenant_id"]) in db.filters
    assert body["is_retryable"] is False


@pytest.mark.parametrize("confirmed", [None, False, True])
async def test_completed_status_does_not_invent_confirmation_or_account(confirmed):
    output = {"success": True}
    if confirmed is not None:
        output["confirmation_allowed"] = confirmed
    response, _db = await detail(status="completed", output=output)
    receipt = response.json()["saved_receipt"]
    assert receipt["confirmation_allowed"] is (confirmed is True)
    assert receipt["receipt"] == {}


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
async def test_foreign_tenant_receipt_is_not_projected(role):
    response, db = await detail(role=role, foreign=True)
    assert response.status_code == 404
    assert any(key == "tenant_id" and value != db.row["tenant_id"] for key, value in db.filters)
