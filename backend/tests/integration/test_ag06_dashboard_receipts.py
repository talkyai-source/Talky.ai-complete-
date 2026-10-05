"""AG06 receipt recovery on the actual migrated schema with a NOBYPASSRLS role.

Synthetic local effects only; no provider or network delivery is claimed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints import assistant_ws
from app.services.action_execution import DurableActionExecutor, find_owned_action_receipt
from tests.integration.test_ag05_lead_evidence import lead_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def receipt_db(lead_db):  # noqa: F811 - inject the imported shared pytest fixture
    db = lead_db
    await db.admin.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON assistant_actions TO "{db.role}"')
    db.actors = [uuid4(), uuid4(), uuid4()]
    for index, actor in enumerate(db.actors):
        await db.admin.execute(
            "INSERT INTO user_profiles(id,email,tenant_id) VALUES($1,$2,$3)",
            actor,
            f"{actor}@example.test",
            db.tenants[0 if index < 2 else 1],
        )
    db.client = SimpleNamespace(pool=db.pool)
    try:
        yield db
    finally:
        await db.admin.execute(
            "DELETE FROM assistant_actions WHERE tenant_id=ANY($1::uuid[])", db.tenants
        )
        await db.admin.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", db.actors)


def request(db, provider, proposal="prop_synthetic"):
    return dict(
        tenant_id=str(db.tenants[0]),
        user_id=str(db.actors[0]),
        idempotency_key=f"assistant:{db.actors[0]}:{proposal}",
        action="send_email",
        payload={"to": ["synthetic@example.test"], "body": "PRIVATE-SYNTHETIC-CONTENT"},
        executor=provider,
    )


async def lookup(db, proposal="prop_synthetic", tenant=None, actor=None):
    return await find_owned_action_receipt(
        db.client,
        tenant_id=str(tenant or db.tenants[0]),
        user_id=str(actor or db.actors[0]),
        proposal_id=proposal,
    )


async def test_committed_concurrent_claim_and_lost_response_recovery(receipt_db):
    db = receipt_db
    assert db.migration_head in {"0058_crm_contact_effect", "0059_auth_identity_contract"}
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def provider():
        effects.append("synthetic-provider-acceptance")
        entered.set()
        await release.wait()
        return {
            "success": True,
            "confirmation_allowed": True,
            "status": "accepted",
            "provider": "gmail",
            "message_id": "synthetic-message",
            "action_id": "inner-receipt",
        }

    kwargs = request(db, provider)
    task = asyncio.create_task(DurableActionExecutor(db.pool).execute(**kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        running = await lookup(db)
        assert running["status"] == "running" and not running["success"]
        duplicate = await DurableActionExecutor(db.pool).execute(**kwargs)
        assert not duplicate["success"] and len(effects) == 1
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    # The original result is deliberately ignored (lost response).
    saved = await lookup(db)
    assert saved["success"] and saved["status"] == "completed"
    assert saved["receipt"]["message_id"] == "synthetic-message"
    assert saved["receipt"]["child_action_id"] == "inner-receipt"
    replay = await DurableActionExecutor(db.pool).execute(**kwargs)
    assert replay["replayed"] and len(effects) == 1
    assert "PRIVATE-SYNTHETIC-CONTENT" not in str(saved)


async def test_recovery_denies_other_actor_and_other_tenant(receipt_db):
    db = receipt_db
    result = await DurableActionExecutor(db.pool).execute(
        **request(db, AsyncMock(return_value={"success": True, "message_id": "receipt"}))
    )
    assert await lookup(db, actor=db.actors[1]) is None
    assert await lookup(db, tenant=db.tenants[1], actor=db.actors[0]) is None
    assert (
        await find_owned_action_receipt(
            db.client,
            tenant_id=str(db.tenants[0]),
            user_id=str(db.actors[1]),
            action_id=result["action_id"],
        )
        is None
    )
    assert (await lookup(db))["action_id"] == result["action_id"]


async def test_unknown_with_reference_stays_fenced_after_new_executor(receipt_db, monkeypatch):
    db = receipt_db
    store = DurableActionExecutor(db.pool)
    original_save = store._save
    saves = 0

    async def save(*args):
        nonlocal saves
        saves += 1
        if saves == 1:
            raise RuntimeError("synthetic local receipt loss")
        return await original_save(*args)

    monkeypatch.setattr(store, "_save", save)
    provider = AsyncMock(
        return_value={
            "success": True,
            "message_id": "provider-accepted-before-save",
            "external_account_id": "account-a",
        }
    )
    kwargs = request(db, provider)
    result = await store.execute(**kwargs)
    assert result["status"] == "unknown"
    receipt = await lookup(db)
    assert receipt["status"] == "unknown" and not receipt["confirmation_allowed"]
    assert receipt["receipt"]["message_id"] == "provider-accepted-before-save"
    await DurableActionExecutor(db.pool).execute(**kwargs)
    provider.assert_awaited_once()


async def test_failed_receipt_save_retains_running_no_resend_fence(receipt_db, monkeypatch):
    db = receipt_db
    store = DurableActionExecutor(db.pool)
    monkeypatch.setattr(store, "_save", AsyncMock(side_effect=RuntimeError("synthetic outage")))
    provider = AsyncMock(return_value={"success": True, "message_id": "accepted"})
    kwargs = request(db, provider)
    await store.execute(**kwargs)
    receipt = await lookup(db)
    assert receipt["status"] == "running" and not receipt["success"]
    await DurableActionExecutor(db.pool).execute(**kwargs)
    provider.assert_awaited_once()


async def test_http_activity_filters_totals_and_owner_receipt_no_payload_leak(receipt_db):
    db = receipt_db
    first = await DurableActionExecutor(db.pool).execute(
        **request(
            db,
            AsyncMock(
                return_value={
                    "success": True,
                    "message_id": "safe-reference",
                    "access_token": "PRIVATE-SYNTHETIC-TOKEN",
                }
            ),
        )
    )
    await DurableActionExecutor(db.pool).execute(
        **request(
            db, AsyncMock(return_value={"success": False, "status": "unknown"}), "prop_unknown"
        )
    )
    app = FastAPI()
    app.include_router(assistant_ws.router)
    user = CurrentUser(
        id=str(db.actors[0]),
        tenant_id=str(db.tenants[0]),
        email="synthetic@example.test",
        role="tenant_admin",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: db.client
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid"
    ) as client:
        response = await client.get(
            "/assistant/actions",
            params={"status": "completed", "type": "send_email", "page_size": 1},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1 and len(payload["actions"]) == 1
        assert payload["actions"][0]["receipt"]["message_id"] == "safe-reference"
        assert "PRIVATE-SYNTHETIC" not in response.text
        assert (await client.get(f"/assistant/actions/{first['action_id']}")).status_code == 200
        user.id = str(db.actors[1])
        assert (await client.get(f"/assistant/actions/{first['action_id']}")).status_code == 404
        # Same-tenant activity remains metadata-only, matching the existing contract.
        assert (await client.get("/assistant/actions")).json()["total"] == 2
        user.id, user.tenant_id = str(db.actors[2]), str(db.tenants[1])
        assert (await client.get("/assistant/actions")).json()["total"] == 0
        assert (
            await client.get("/assistant/actions", params={"sort_by": "input_data"})
        ).status_code == 422
