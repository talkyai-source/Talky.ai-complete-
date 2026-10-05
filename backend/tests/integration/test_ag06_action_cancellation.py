"""Actual migrated PG cancellation/dispatch barriers; no external provider effects."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.api.v1.endpoints.admin import actions
from app.core import postgres_adapter
from app.services.action_execution import DurableActionExecutor
from app.services.voice_callback_service import callback_job_id, drain_voice_callbacks
from tests.integration.test_ag05_lead_evidence import lead_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def cancel_db(lead_db, monkeypatch):  # noqa: F811 - imported pytest fixture
    db = lead_db
    await db.admin.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON assistant_actions,dialer_jobs TO "{db.role}"')
    await db.admin.execute(
        "UPDATE campaigns SET status='running',script_config=$2::jsonb WHERE id=$1",
        db.campaigns[0], json.dumps({"campaign_brief": {"approved_next_actions": ["schedule_callback"]}}),
    )
    # Legacy Admin reads use the adapter's separate synchronous connections.
    # Explicitly bind those too to the validated synthetic loopback DSN.
    import os
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    db.client = postgres_adapter.Client(db.pool)
    db.actor = SimpleNamespace(id=str(uuid4()), role="platform_admin", tenant_id=None)
    try:
        yield db
    finally:
        await db.admin.execute("DELETE FROM assistant_actions WHERE tenant_id=ANY($1::uuid[])", db.tenants)
        await db.admin.execute("DELETE FROM dialer_jobs WHERE tenant_id=ANY($1::uuid[])", db.tenants)


async def seed_action(db, *, callback=False):
    identity = uuid4()
    parameters = {"parameters": {"phone": "+15550001001"}} if callback else {"to": "synthetic@example.org"}
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    await db.admin.execute(
        """INSERT INTO assistant_actions(id,tenant_id,campaign_id,lead_id,type,status,triggered_by,
            idempotency_key,input_data,output_data,scheduled_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,'{}',NOW()-INTERVAL '1 second')""",
        identity, db.tenants[0], db.campaigns[0], db.leads[0],
        "schedule_callback" if callback else "send_email", "scheduled" if callback else "pending",
        "voice" if callback else "assistant", "ag06-" + str(identity),
        json.dumps({"request_hash": digest, "parameters": parameters}),
    )
    return identity, parameters


async def cancel(db, identity):
    return await actions.cancel_action(str(identity), db.actor, db.client)


async def stored(db, identity):
    return await db.admin.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", identity)


async def test_cancel_unclaimed_execution_prevents_later_provider_entry(cancel_db):
    db = cancel_db
    identity, parameters = await seed_action(db)
    result = await cancel(db, identity)
    assert result["new_status"] == "cancelled"
    provider = AsyncMock()
    replay = await DurableActionExecutor(db.pool).execute(
        tenant_id=str(db.tenants[0]), idempotency_key="ag06-" + str(identity),
        action="send_email", payload=parameters, executor=provider,
    )
    provider.assert_not_awaited()
    assert replay["status"] == "cancelled" and replay["confirmation_allowed"] is False
    assert (await stored(db, identity))["status"] == "cancelled"


async def test_cancel_after_committed_claim_does_not_replace_running_receipt(cancel_db):
    db = cancel_db
    identity, parameters = await seed_action(db)
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider_boundary():
        entered.set()
        await release.wait()
        return {"success": True, "status": "provider_accepted", "message_id": "synthetic-receipt"}

    execution = asyncio.create_task(DurableActionExecutor(db.pool).execute(
        tenant_id=str(db.tenants[0]), idempotency_key="ag06-" + str(identity),
        action="send_email", payload=parameters, executor=provider_boundary,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(HTTPException) as caught:
            await cancel(db, identity)
        assert caught.value.status_code == 409
        assert (await stored(db, identity))["status"] == "running"
    finally:
        release.set()
        result = await asyncio.wait_for(execution, timeout=5)
    assert result["message_id"] == "synthetic-receipt"
    assert (await stored(db, identity))["status"] == "completed"


async def test_callback_cancel_before_reservation_prevents_job_and_queue(cancel_db):
    db = cancel_db
    identity, _ = await seed_action(db, callback=True)
    assert (await cancel(db, identity))["new_status"] == "cancelled"
    queue = SimpleNamespace(schedule_job_once=AsyncMock(), confirm_retry_once=AsyncMock())
    await drain_voice_callbacks(db.pool, queue)
    queue.schedule_job_once.assert_not_awaited()
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM dialer_jobs WHERE tenant_id=$1)", db.tenants[0])


async def test_callback_queue_barrier_cannot_be_reported_cancelled(cancel_db):
    db = cancel_db
    identity, _ = await seed_action(db, callback=True)
    entered, release = asyncio.Event(), asyncio.Event()
    submissions = []

    async def publish(job, **_kwargs):
        submissions.append(job.job_id)
        entered.set()
        await release.wait()
        return True

    queue = SimpleNamespace(schedule_job_once=publish, confirm_retry_once=AsyncMock())
    drain = asyncio.create_task(drain_voice_callbacks(db.pool, queue))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        row = await stored(db, identity)
        assert row["status"] == "scheduled"
        assert json.loads(row["output_data"])["dialer_job_id"] == callback_job_id(identity)
        with pytest.raises(HTTPException) as caught:
            await cancel(db, identity)
        assert caught.value.status_code == 409
    finally:
        release.set()
        await asyncio.wait_for(drain, timeout=5)
    assert submissions == [callback_job_id(identity)]
    row = await stored(db, identity)
    assert row["status"] == "completed" and json.loads(row["output_data"])["status"] == "queued"
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1::uuid", callback_job_id(identity)) == "queued"


async def test_ambiguous_queue_handoff_stays_reserved_and_reuses_same_job(cancel_db):
    db = cancel_db
    identity, _ = await seed_action(db, callback=True)
    published, attempts = set(), []

    async def publish(job, *, idempotency_key):
        published.add((job.job_id, idempotency_key))
        attempts.append(job.job_id)
        return len(attempts) > 1  # Simulate accepted queue write with a lost acknowledgment.

    queue = SimpleNamespace(schedule_job_once=publish, confirm_retry_once=AsyncMock())
    await drain_voice_callbacks(db.pool, queue)
    with pytest.raises(HTTPException) as caught:
        await cancel(db, identity)
    assert caught.value.status_code == 409
    await drain_voice_callbacks(db.pool, queue)
    assert len(attempts) == 2 and len(published) == 1
    assert await db.admin.fetchval("SELECT COUNT(*) FROM dialer_jobs WHERE tenant_id=$1", db.tenants[0]) == 1
    assert (await stored(db, identity))["status"] == "completed"


async def test_legacy_reserved_job_without_json_marker_is_not_cancellable(cancel_db):
    db = cancel_db
    identity, _ = await seed_action(db, callback=True)
    await db.admin.execute(
        """INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status)
           VALUES($1::uuid,$2,$3,$4,'+15550001001','pending')""",
        callback_job_id(identity), db.tenants[0], db.campaigns[0], db.leads[0],
    )
    with pytest.raises(HTTPException) as caught:
        await cancel(db, identity)
    assert caught.value.status_code == 409
    assert (await stored(db, identity))["status"] == "scheduled"


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
async def test_admin_receipt_reads_and_cancel_pin_the_authenticated_tenant(cancel_db, role):
    db = cancel_db
    identity, _ = await seed_action(db)
    foreign = SimpleNamespace(id=str(uuid4()), role=role, tenant_id=str(db.tenants[1]))
    for endpoint in (actions.get_admin_action_detail, actions.retry_action, actions.cancel_action):
        with pytest.raises(HTTPException) as caught:
            await endpoint(str(identity), foreign, db.client)
        assert caught.value.status_code == 404
    listing = await actions.get_admin_actions(
        page=1, page_size=20, status=None, type=None, tenant_id=None,
        from_date=None, to_date=None, search=None, admin_user=foreign, db_client=db.client,
    )
    assert listing.items == [] and listing.total == 0
    with pytest.raises(HTTPException) as caught:
        await actions.get_admin_actions(
            page=1, page_size=20, status=None, type=None, tenant_id=str(db.tenants[0]),
            from_date=None, to_date=None, search=None, admin_user=foreign, db_client=db.client,
        )
    assert caught.value.status_code == 403
    owner = SimpleNamespace(id=str(uuid4()), role=role, tenant_id=str(db.tenants[0]))
    detail = await actions.get_admin_action_detail(str(identity), owner, db.client)
    assert detail.id == str(identity) and detail.is_cancellable
    with pytest.raises(HTTPException) as caught:
        await actions.retry_action(str(identity), owner, db.client)
    assert caught.value.status_code == 501 and caught.value.detail["retryable"] is False
    assert (await actions.cancel_action(str(identity), owner, db.client))["new_status"] == "cancelled"
