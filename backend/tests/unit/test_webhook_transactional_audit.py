"""Webhook secret rotation and its masked receipt commit together."""
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.endpoints import webhooks_secure as endpoint
from app.api.v1.dependencies import get_audit_logger, get_db_client

TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def rotation(monkeypatch):
    state = {"webhook_name": "example", "is_active": False, "secret_key": "OLD-CIPHER"}
    events, receipts = [], []

    class Connection:
        async def fetchrow(self, sql, *args):
            assert "tenant_id = $1" in sql and "webhook_name = $2" in sql
            assert tuple(map(str, args)) == (TENANT, "example")
            assert events == ["lock"]
            return {key: state[key] for key in ("webhook_name", "is_active")}

        async def execute(self, sql, *args):
            assert "INSERT INTO webhook_configs" in sql
            events.append("write")
            state.update(webhook_name=args[1], secret_key=args[2], is_active=True)

    conn = Connection()

    @asynccontextmanager
    async def acquire(pool, tenant):
        assert tenant == TENANT
        before = deepcopy(state)
        try:
            yield conn
        except BaseException:
            state.clear()
            state.update(before)
            receipts.clear()
            raise

    class Audit:
        fail = False

        async def lock_transaction(self, supplied):
            assert supplied is conn
            events.append("lock")

        async def log_in_transaction(self, supplied, **kwargs):
            assert supplied is conn and events == ["lock", "write"]
            if self.fail:
                raise RuntimeError("synthetic audit outage")
            receipts.append(kwargs)

    audit = Audit()
    manager = SimpleNamespace(invalidate_cache=Mock())
    monkeypatch.setattr(endpoint, "acquire_with_tenant", acquire)
    monkeypatch.setattr(endpoint, "generate_webhook_secret", lambda: "NEW-PLAINTEXT")
    monkeypatch.setattr(endpoint, "encrypt_webhook_secret", lambda secret: "NEW-CIPHER")
    monkeypatch.setattr(endpoint, "get_webhook_secret_manager", lambda: manager)
    app = FastAPI()
    app.include_router(endpoint.router)
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=object())
    app.dependency_overrides[get_audit_logger] = lambda: audit
    app.dependency_overrides[endpoint.require_internal_service] = lambda: None
    return SimpleNamespace(app=app, state=state, audit=audit, receipts=receipts, manager=manager)


async def rotate(fixture):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app), base_url="http://test") as client:
        return await client.post("/webhooks/secure/admin/configure",
                                 params={"tenant_id": TENANT, "webhook_name": "example"})


async def test_rotation_commits_one_masked_service_receipt(rotation):
    response = await rotate(rotation)
    assert response.status_code == 200
    assert len(rotation.receipts) == 1
    receipt = rotation.receipts[0]
    assert receipt["actor_type"] == "system" and receipt.get("actor_id") is None
    assert str(receipt["tenant_id"]) == TENANT
    assert receipt["before_state"]["is_active"] is False
    assert receipt["after_state"]["is_active"] is True
    assert all(secret not in json.dumps(receipt, default=str)
               for secret in ("NEW-PLAINTEXT", "OLD-CIPHER", "NEW-CIPHER"))
    rotation.manager.invalidate_cache.assert_called()


async def test_audit_outage_rolls_back_secret_and_leaves_cache(rotation):
    before = deepcopy(rotation.state)
    rotation.audit.fail = True
    response = await rotate(rotation)
    assert response.status_code == 500
    assert rotation.state == before
    rotation.manager.invalidate_cache.assert_not_called()
