"""Real PostgreSQL action fences; explicit disposable localhost database only."""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.services.action_execution import DurableActionExecutor


@pytest_asyncio.fixture
async def action_db():
    dsn = os.getenv("TALKY_ACTION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TALKY_ACTION_TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Only a disposable local *_test database is permitted")
    schema = "actions_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        await admin.execute(f"SET search_path TO {schema}")
        await admin.execute("""
            CREATE TABLE assistant_actions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL,
                type TEXT, status TEXT, idempotency_key TEXT, input_data JSONB, output_data JSONB,
                call_id UUID, lead_id UUID, campaign_id UUID, user_id UUID, triggered_by TEXT, conversation_id UUID,
                started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, scheduled_at TIMESTAMPTZ, error TEXT);
            CREATE UNIQUE INDEX idx_assistant_actions_idempotency ON assistant_actions(tenant_id,idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            CREATE TABLE campaigns (id UUID PRIMARY KEY,tenant_id UUID,direction TEXT,status TEXT,script_config JSONB);
            CREATE TABLE leads (id UUID PRIMARY KEY,tenant_id UUID,campaign_id UUID,phone_number TEXT,do_not_call BOOLEAN,status TEXT);
            CREATE TABLE dialer_jobs (id UUID PRIMARY KEY,tenant_id UUID,campaign_id UUID,lead_id UUID,phone_number TEXT,status TEXT,scheduled_at TIMESTAMPTZ);
            CREATE TABLE calls (id UUID PRIMARY KEY,tenant_id UUID,campaign_id UUID,lead_id UUID,provider_call_id TEXT,external_call_uuid TEXT,
                provider TEXT,status TEXT,talklee_call_id TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),
                direction VARCHAR(12) NOT NULL DEFAULT 'outbound',
                route_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                admission_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                processing_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                billing_status VARCHAR(16) NOT NULL DEFAULT 'none',
                reserved_seconds INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE connectors (id UUID PRIMARY KEY,tenant_id UUID,type TEXT,status TEXT);
            CREATE TABLE connector_accounts (connector_id UUID,tenant_id UUID,status TEXT);
        """)
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, server_settings={"search_path": schema})
        yield admin, pool
    finally:
        if pool:
            await pool.close()
        await admin.execute("SET search_path TO public")
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


def request(executor, tenant=None):
    return dict(tenant_id=tenant or str(uuid4()), idempotency_key="test-request", action="send_email",
                payload={"recipient": "synthetic@example.test", "revision": 1}, executor=executor)


async def test_duplicate_claim_restart_conflict_and_tenant_are_durable(action_db):
    admin, pool = action_db
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    async def send():
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        return {"success": True, "status": "accepted", "confirmation_allowed": True, "message_id": "synthetic-receipt"}
    kwargs = request(send)
    first = asyncio.create_task(DurableActionExecutor(pool).execute(**kwargs))
    await entered.wait()
    replay_during_write = await DurableActionExecutor(pool).execute(**kwargs)
    assert replay_during_write["status"] == "unknown" and count == 1
    release.set()
    result = await first
    assert result["message_id"] == "synthetic-receipt"
    replay = await DurableActionExecutor(pool).execute(**kwargs)
    assert replay["replayed"] and count == 1
    conflict = await DurableActionExecutor(pool).execute(**{**kwargs, "payload": {"recipient": "other@example.test"}})
    assert conflict["status"] == "request_conflict" and count == 1
    await DurableActionExecutor(pool).execute(**{**kwargs, "tenant_id": str(uuid4())})
    assert count == 2


async def test_cancellation_and_post_send_receipt_failure_never_resend(action_db, monkeypatch):
    admin, pool = action_db
    entered = asyncio.Event()
    async def unknown_send():
        entered.set()
        await asyncio.Event().wait()
    kwargs = request(unknown_send)
    task = asyncio.create_task(DurableActionExecutor(pool).execute(**kwargs))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await admin.fetchval("SELECT status FROM assistant_actions") == "unknown"
    never = AsyncMock()
    assert (await DurableActionExecutor(pool).execute(**{**kwargs, "executor": never}))["status"] == "unknown"
    never.assert_not_awaited()
    store = DurableActionExecutor(pool)
    monkeypatch.setattr(store, "_save", AsyncMock(side_effect=RuntimeError("simulated DB loss after send")))
    once = AsyncMock(return_value={"success": True, "status": "accepted", "message_id": "remote-accepted"})
    second = request(once)
    assert (await store.execute(**second))["status"] == "unknown"
    assert (await DurableActionExecutor(pool).execute(**second))["status"] == "unknown"
    assert once.await_count == 1


async def test_callback_db_outbox_replays_same_job_after_queue_success(action_db):
    from app.services.voice_callback_service import drain_voice_callbacks
    admin, pool = action_db
    tenant, campaign, lead = [str(uuid4()) for _ in range(3)]
    await admin.execute("INSERT INTO campaigns VALUES ($1::uuid,$2::uuid,'outbound','running',$3::jsonb)",
                        campaign, tenant, json.dumps({"campaign_brief": {"approved_next_actions": ["schedule_callback"]}}))
    await admin.execute("INSERT INTO leads VALUES ($1::uuid,$2::uuid,$3::uuid,'+14155552671',false,'pending')", lead, tenant, campaign)
    when = datetime.now(timezone.utc) - timedelta(seconds=1)
    payload = {"parameters": {"phone": "+14155552671", "scheduled_at": when.isoformat()}, "confirmation": {"revision": 1}}
    async def schedule():
        return {"success": True, "status": "scheduled", "confirmation_allowed": True, "scheduled_at": when.isoformat()}
    await DurableActionExecutor(pool).execute(tenant_id=tenant,idempotency_key="voice-callback",action="schedule_callback",
        payload=payload,executor=schedule,campaign_id=campaign,lead_id=lead)
    queued = []
    async def publish(job, **kwargs):
        queued.append(job.job_id)
        return len(queued) > 1  # First ambiguous queue response leaves outbox scheduled.
    queue = SimpleNamespace(schedule_job_once=publish,confirm_retry_once=AsyncMock())
    await drain_voice_callbacks(pool, queue)
    assert await admin.fetchval("SELECT status FROM assistant_actions") == "scheduled"
    await drain_voice_callbacks(pool, queue)
    assert len(set(queued)) == 1
    assert await admin.fetchval("SELECT COUNT(*) FROM dialer_jobs") == 1
    assert await admin.fetchval("SELECT status FROM assistant_actions") == "completed"


async def test_changed_callback_policy_prevents_dispatch(action_db):
    from app.services.voice_callback_service import drain_voice_callbacks
    admin, pool = action_db
    tenant, campaign, lead, action = [str(uuid4()) for _ in range(4)]
    await admin.execute("INSERT INTO campaigns VALUES ($1::uuid,$2::uuid,'inbound','active','{}')",campaign,tenant)
    await admin.execute("INSERT INTO leads VALUES ($1::uuid,$2::uuid,$3::uuid,'+14155552671',false,'pending')",lead,tenant,campaign)
    await admin.execute("""INSERT INTO assistant_actions (id,tenant_id,campaign_id,lead_id,type,status,triggered_by,scheduled_at,input_data,output_data)
         VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,'schedule_callback','scheduled','voice',NOW(),$5::jsonb,'{}')""",
         action,tenant,campaign,lead,json.dumps({"parameters":{"parameters":{"phone":"+14155552671"}}}))
    queue = SimpleNamespace(schedule_job_once=AsyncMock(),confirm_retry_once=AsyncMock())
    await drain_voice_callbacks(pool,queue)
    queue.schedule_job_once.assert_not_awaited()
    assert await admin.fetchval("SELECT status FROM assistant_actions") == "failed"


async def test_voice_email_real_scope_confirmation_revision_and_replay(action_db, monkeypatch):
    from app.domain.services.voice_pipeline.action_execution import execute_connected_voice_action, prepare_voice_action_context
    from app.domain.services.voice_pipeline.contact_capture import ContactCaptureState, CaptureStatus
    from app.services.scripts.call_state_tracker import CallState
    from app.services.email_service import EmailService
    admin, pool = action_db
    tenant, campaign, lead, call, connector = [str(uuid4()) for _ in range(5)]
    brief = {"approved_next_actions": ["send_email"], "email_action": {"subject": "Requested details", "body": "Approved company information."}}
    await admin.execute("INSERT INTO campaigns VALUES ($1::uuid,$2::uuid,'outbound','running',$3::jsonb)",campaign,tenant,json.dumps({"campaign_brief":brief}))
    await admin.execute("INSERT INTO leads VALUES ($1::uuid,$2::uuid,$3::uuid,'+14155552671',false,'pending')",lead,tenant,campaign)
    await admin.execute("INSERT INTO calls (id,tenant_id,campaign_id,lead_id,provider_call_id,provider,status) VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,'channel-test','asterisk','in_progress')",call,tenant,campaign,lead)
    await admin.execute("INSERT INTO connectors VALUES ($1::uuid,$2::uuid,'email','active')",connector,tenant)
    await admin.execute("INSERT INTO connector_accounts VALUES ($1::uuid,$2::uuid,'active')",connector,tenant)
    capture = ContactCaptureState(kind="email",status=CaptureStatus.CONFIRMED,normalized_value="caller@example.test",
        confirmed_at=datetime.now(timezone.utc),from_caller=True)
    session = SimpleNamespace(tenant_id=tenant,campaign_id=campaign,call_id=call,lead_id=lead,turn_id=1,
        captured_slots=CallState(email="caller@example.test",email_confirmed=True,email_capture=capture),_voice_action_pool=pool)
    sent = AsyncMock(return_value={"success":True,"message_id":"provider-receipt","provider":"gmail"})
    monkeypatch.setattr(EmailService,"send_email",sent)
    assert "send_email" in await prepare_voice_action_context(session)
    first = await execute_connected_voice_action(session,"send_email",{},"Please send the email.")
    assert first["status"] == "needs_confirmation"
    same_turn = await execute_connected_voice_action(session,"send_email",{},"yes")
    assert same_turn["status"] == "needs_confirmation"
    session.turn_id += 1
    # No proof that the summary played: a bare yes remains insufficient.
    assert (await execute_connected_voice_action(session,"send_email",{},"yes"))["status"] == "needs_confirmation"
    session._voice_action_delivered_text = first["confirmation_summary"]
    result = await execute_connected_voice_action(session,"send_email",{},"yes")
    assert result["status"] == "provider_accepted" and sent.await_count == 1
    assert sent.await_args.kwargs["to"] == ["caller@example.test"]
    assert sent.await_args.kwargs["body"] == brief["email_action"]["body"]
    session.turn_id += 1
    assert (await execute_connected_voice_action(session,"send_email",{},"yes"))["replayed"]
    assert sent.await_count == 1
    # Tenant ownership is checked by SQL before proposals or sending.
    session.tenant_id = str(uuid4())
    assert (await execute_connected_voice_action(session,"send_email",{},"yes"))["status"] == "unavailable"
    assert sent.await_count == 1


async def test_dashboard_call_preview_then_real_queue_and_no_duplicate_lead(action_db, monkeypatch):
    from app.infrastructure.assistant.tools.calls import initiate_call
    from app.infrastructure.assistant.proposals import is_preview_result, store_proposal, pop_proposal
    from app.core import container
    admin, pool = action_db
    tenant,campaign,lead = [str(uuid4()) for _ in range(3)]
    await admin.execute("INSERT INTO campaigns VALUES ($1::uuid,$2::uuid,'outbound','running','{}')",campaign,tenant)
    await admin.execute("INSERT INTO leads VALUES ($1::uuid,$2::uuid,$3::uuid,'+14155552671',false,'pending')",lead,tenant,campaign)
    queue = SimpleNamespace(schedule_job_once=AsyncMock(return_value=True),confirm_retry_once=AsyncMock())
    monkeypatch.setattr(container,"get_container",lambda:SimpleNamespace(queue_service=queue))
    client = SimpleNamespace(pool=pool)
    kwargs = dict(tenant_id=tenant,db_client=client,phone_number="+14155552671",campaign_id=campaign,lead_id=lead)
    preview = await initiate_call(**{**kwargs, "phone_number": "+1 (415) 555-2671", "lead_id": None})
    assert preview["status"] == "preview" and is_preview_result(preview)
    actor = str(uuid4())
    proposal = store_proposal(tool="initiate_call", args={"phone_number": "untrusted later value"},
                             result=preview, tenant_id=tenant, actor_user_id=actor)
    assert proposal["args"] == {"phone_number": "+14155552671", "campaign_id": campaign, "lead_id": lead}
    assert pop_proposal(proposal["proposal_id"], tenant, actor) == proposal
    assert await admin.fetchval("SELECT COUNT(*) FROM dialer_jobs") == 0
    queue.schedule_job_once.assert_not_awaited()
    result = await initiate_call(**kwargs,confirm=True)
    assert result["success"] and result["status"] == "queued"
    assert await admin.fetchval("SELECT status FROM dialer_jobs") == "queued"
    assert not (await initiate_call(**kwargs,confirm=True))["success"]
    assert queue.schedule_job_once.await_count == 1
    assert not (await initiate_call(**{**kwargs,"tenant_id":str(uuid4())},confirm=True))["success"]


async def test_dashboard_workflow_freezes_real_call_preview_without_queuing(action_db, monkeypatch):
    from app.infrastructure.assistant.tools import dispatch
    from app.infrastructure.assistant.tools.workflow import execute_action_plan
    from app.infrastructure.assistant.proposals import is_preview_result
    admin, pool = action_db
    tenant, campaign, lead, actor = [str(uuid4()) for _ in range(4)]
    await admin.execute("INSERT INTO campaigns VALUES ($1::uuid,$2::uuid,'outbound','running','{}')", campaign, tenant)
    await admin.execute("INSERT INTO leads VALUES ($1::uuid,$2::uuid,$3::uuid,'+14155552671',false,'pending')", lead, tenant, campaign)
    # Authorization is tested separately. Keep the real dispatcher, child tool,
    # normalization, scoped SQL and workflow preview contract in this regression.
    authorize = AsyncMock(return_value=None)
    monkeypatch.setattr(dispatch, "_authorize_action_tool", authorize)
    result = await execute_action_plan(
        tenant, SimpleNamespace(pool=pool), "Call this lead",
        [{"type": "initiate_call", "phone_number": "+1 (415) 555-2671", "campaign_id": campaign}],
        actor_user_id=actor,
    )
    assert is_preview_result(result), result
    assert result["_apply_args"]["actions"] == [{
        "type": "initiate_call", "phone_number": "+14155552671", "campaign_id": campaign,
        "lead_id": lead, "condition": "always",
    }]
    assert any(change["field"] == "Step 1 · Call number" for change in result["changes"])
    assert await admin.fetchval("SELECT COUNT(*) FROM dialer_jobs") == 0
    assert authorize.await_count == 2
