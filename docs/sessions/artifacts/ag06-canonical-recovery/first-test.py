"""Recovery on the actual migrated schema, JWT/session and adapter boundaries.

Opt-in runner owns the fresh database. Provider/credential ports are synthetic;
no auth, adapter, metadata, principal, recovery, or executor path is replaced.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin import actions
from app.core import postgres_adapter
from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.core.jwt_security import encode_access_token
from app.core.tenant_middleware import TenantMiddleware
from app.domain.services.voice_pipeline import action_execution as voice
from app.domain.services.voice_pipeline.contact_capture import CaptureStatus, ContactCaptureState
from app.services import email_service
from app.services.action_execution import DurableActionExecutor
from app.services.connector_resolver import reviewed_authorization_identity
from app.services.saved_acknowledgement import source_digest

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def canonical(monkeypatch):
    dsn, owner_dsn = os.getenv("TALKY_ACK_CANONICAL_DSN"), os.getenv("TALKY_ACK_CANONICAL_OWNER_DSN")
    if not dsn or not owner_dsn:
        pytest.skip("Reviewed fresh canonical database runner required")
    a, b = urlparse(dsn), urlparse(owner_dsn)
    assert a.hostname == b.hostname == "127.0.0.1" and a.port == b.port == 55434
    assert a.path == b.path and re.fullmatch(r"/ack_canonical_[0-9a-f]{32}_test", a.path)
    owner = await asyncpg.connect(owner_dsn, timeout=5, command_timeout=15)
    await _register_jsonb_codecs(owner)
    await owner.execute("SET app.bypass_rls='true'; SET app.current_tenant_id='00000000-0000-0000-0000-000000000000'")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, command_timeout=10, init=_register_jsonb_codecs)
    try:
        async with pool.acquire() as conn:
            assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0062_saved_acknowledgement"
            assert not await conn.fetchval("SELECT pg_has_role(current_user,relowner,'MEMBER') FROM pg_class WHERE oid='assistant_action_resolutions'::regclass")
        postgres_adapter._TABLE_COLUMN_TYPES_CACHE.clear()
        monkeypatch.setattr(postgres_adapter, "_DATABASE_URL", dsn)
        monkeypatch.setattr(dependencies, "get_db_pool_from_container", lambda: pool)
        db = SimpleNamespace(owner=owner, pool=pool, tenant=str(uuid4()), actor=str(uuid4()), session=str(uuid4()),
                             connector=str(uuid4()), account=str(uuid4()), campaign=str(uuid4()), lead=str(uuid4()), call=str(uuid4()))
        await owner.execute("INSERT INTO tenants(id,business_name) VALUES($1::uuid,'Canonical synthetic tenant')", db.tenant)
        await owner.execute("""INSERT INTO user_profiles(id,email,tenant_id,role,is_active,is_verified,email_verified_at)
          VALUES($1::uuid,$2,$3::uuid,'platform_admin',true,true,now())""", db.actor, db.actor + "@example.invalid", db.tenant)
        await owner.execute("""INSERT INTO security_sessions(id,user_id,session_token_hash,expires_at,mfa_verified)
          VALUES($1::uuid,$2::uuid,$3,now()+interval '1 hour',true)""", db.session, db.actor, "synthetic-" + db.session)
        await owner.execute("""INSERT INTO connectors(id,tenant_id,type,provider,name,status)
          VALUES($1::uuid,$2::uuid,'email','gmail','Synthetic Gmail','active')""", db.connector, db.tenant)
        await owner.execute("INSERT INTO connector_accounts(id,connector_id,tenant_id,status) VALUES($1::uuid,$2::uuid,$3::uuid,'active')", db.account, db.connector, db.tenant)
        brief = {"campaign_brief": {"approved_next_actions": ["send_email"], "email_action": {"subject": "Résumé café", "body": "Synthetic approved café body"}}}
        await owner.execute("""INSERT INTO campaigns(id,tenant_id,name,status,direction,script_config)
          VALUES($1::uuid,$2::uuid,'Canonical synthetic campaign','active','outbound',$3::jsonb)""", db.campaign, db.tenant, brief)
        await owner.execute("INSERT INTO leads(id,tenant_id,campaign_id,phone_number) VALUES($1::uuid,$2::uuid,$3::uuid,'+15555550123')", db.lead, db.tenant, db.campaign)
        await owner.execute("""INSERT INTO calls(id,tenant_id,campaign_id,lead_id,phone_number,status)
          VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,'+15555550123','in_progress')""", db.call, db.tenant, db.campaign, db.lead)
        db.provider = SimpleNamespace(tenant_id=db.tenant, account_row_id=db.account, external_account_id=None,
            send_email=AsyncMock(return_value=SimpleNamespace(id="canonical-message", thread_id="synthetic-thread")))
        db.proof = reviewed_authorization_identity(db.provider, db.connector, "gmail")
        async def connector(_self, tenant_id, **_kwargs):
            assert tenant_id == db.tenant
            return db.provider, db.connector, "gmail"
        monkeypatch.setattr(email_service, "get_encryption_service", lambda: None)
        monkeypatch.setattr(email_service.EmailService, "_get_active_email_connector", connector)
        monkeypatch.setattr(email_service, "check_reviewed_authorization_current", lambda *_: None)
        db.service = email_service.EmailService(pool)
        db.token = encode_access_token(user_id=db.actor, email=db.actor + "@example.invalid", role="platform_admin",
                                       tenant_id=db.tenant, session_id=db.session)
        yield db
    finally:
        postgres_adapter._TABLE_COLUMN_TYPES_CACHE.clear()
        await pool.close()
        await owner.close()


async def create_unknown(db, monkeypatch, *, source="assistant"):
    original_save = DurableActionExecutor._save
    first = True
    async def fail_once(self, *args):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("Synthetic final local receipt-save failure")
        return await original_save(self, *args)
    with monkeypatch.context() as patch:
        patch.setattr(DurableActionExecutor, "_save", fail_once)
        if source == "voice":
            capture = ContactCaptureState(kind="email", status=CaptureStatus.CONFIRMED, normalized_value="saved@example.invalid",
                confirmed_at=datetime.now(timezone.utc), from_caller=True)
            session = SimpleNamespace(tenant_id=db.tenant, campaign_id=db.campaign, call_id=db.call, turn_id=1,
                captured_slots=SimpleNamespace(email_capture=capture), _voice_action_pool=db.pool)
            preview = await voice.execute_connected_voice_action(session, "send_email", {}, "Please email me.")
            assert preview["status"] == "needs_confirmation", preview
            session.turn_id = 2
            session._voice_action_delivered_text = preview["confirmation_summary"]
            result = await voice.execute_connected_voice_action(session, "send_email", {}, "yes")
        else:
            payload = {"to": ["saved@example.invalid"], "subject": "Résumé café", "body": "Saved café body", "confirm": True, "_reviewed_connector": db.proof}
            result = await DurableActionExecutor(db.pool).execute(tenant_id=db.tenant, action="send_email", payload=payload,
                user_id=db.actor, triggered_by="assistant", idempotency_key=f"assistant:{db.actor}:prop_{uuid4().hex[:16]}",
                executor=lambda: db.service.send_email(tenant_id=db.tenant, to=payload["to"], subject=payload["subject"], body=payload["body"], reviewed_connector=db.proof))
    assert result["status"] == "unknown" and result["provider_result"]["success"] is True
    assert db.provider.send_email.await_count == 1
    row = dict(await db.owner.fetchrow("SELECT * FROM assistant_actions WHERE id=$1::uuid", result["action_id"]))
    assert await db.owner.fetchval("SELECT status FROM assistant_actions WHERE id=$1::uuid", result["provider_result"]["child_action_id"]) == "completed"
    return row


@asynccontextmanager
async def client(db):
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    app.include_router(actions.router, prefix="/api/v1/admin")
    # Only the pool port is injected; actual JWT decode, get_current_user,
    # role dependency, session/principal reads and PostgresClient execute.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic",
                                 headers={"Authorization": "Bearer " + db.token}) as http:
        yield http


def path(row):
    return f"/api/v1/admin/actions/{row['id']}"


async def review(http, row):
    response = await http.get(path(row))
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["id"] == str(row["id"]) and detail["status"] == "unknown"
    digest = detail["acknowledgement_recovery"]["source_digest"]
    assert digest == source_digest(row), "Real adapter/detail must agree with native locked-row digest"
    return {"request_id": str(uuid4()), "expected_source_digest": digest, "reason": "Reviewed original canonical saved acceptance"}


async def events(db, row):
    async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
        return await conn.fetch("SELECT * FROM assistant_action_resolutions WHERE action_id=$1", row["id"])


@pytest.mark.parametrize("source", ["assistant", "voice"])
async def test_canonical_detail_digest_recovery_refetch_and_executor_replay(canonical, monkeypatch, source):
    db = canonical
    row = await create_unknown(db, monkeypatch, source=source)
    async with client(db) as http:
        body = await review(http, row)
        responses = await asyncio.gather(*(http.post(path(row) + "/recover-acknowledgement", json=body) for _ in range(3)))
        assert [r.status_code for r in responses] == [200] * 3, [r.text for r in responses]
        assert all(r.json() == responses[0].json() for r in responses)
        detail = (await http.get(path(row))).json()
        assert detail["status"] == detail["saved_receipt"]["status"] == "completed"
        assert detail["saved_receipt"]["success"] is True and detail["saved_receipt"]["confirmation_allowed"] is True
        assert detail["acknowledgement_recovery"] is None and detail["acknowledgement_recovery_record"] == responses[0].json()
        assert (await http.post(path(row) + "/recover-acknowledgement", json=body)).json() == responses[0].json()
    audit = await events(db, row)
    assert len(audit) == 1 and audit[0]["original_output"] == row["output_data"]
    after = dict(await db.owner.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", row["id"]))
    assert {k: v for k, v in after.items() if k not in {"status", "output_data"}} == {k: v for k, v in row.items() if k not in {"status", "output_data"}}
    never = AsyncMock(side_effect=AssertionError("Replay must not send"))
    replay = await DurableActionExecutor(db.pool).execute(tenant_id=db.tenant, action="send_email", payload=row["input_data"]["parameters"],
        idempotency_key=row["idempotency_key"], executor=never)
    assert replay["replayed"] is True and replay["success"] is True and db.provider.send_email.await_count == 1
    never.assert_not_awaited()
    assert postgres_adapter._TABLE_COLUMN_TYPES_CACHE["assistant_actions"]["id"] == "uuid"


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin"])
async def test_actual_current_role_and_foreign_tenant_limits(canonical, monkeypatch, role):
    db = canonical
    row = await create_unknown(db, monkeypatch)
    async with client(db) as http:
        body = await review(http, row)
        rid = await db.owner.fetchval("INSERT INTO roles(name,level) VALUES($1,1) ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name RETURNING id", role)
        await db.owner.execute("INSERT INTO tenant_users(user_id,tenant_id,role_id,status) VALUES($1::uuid,$2::uuid,$3,'active')", db.actor, db.tenant, rid)
        await db.owner.execute("UPDATE user_profiles SET role=$1 WHERE id=$2::uuid", role, db.actor)
        detail = (await http.get(path(row))).json()
        assert detail["acknowledgement_recovery"] is None and detail["acknowledgement_recovery_record"] is None
        assert (await http.post(path(row) + "/recover-acknowledgement", json=body)).status_code == 403
        foreign = await db.owner.fetchval("INSERT INTO tenants(business_name) VALUES('Foreign synthetic') RETURNING id")
        foreign_action = await db.owner.fetchval("INSERT INTO assistant_actions(tenant_id,type,status) VALUES($1,'send_email','unknown') RETURNING id", foreign)
        assert (await http.get(f"/api/v1/admin/actions/{foreign_action}")).status_code == 404
    assert db.provider.send_email.await_count == 1


@pytest.mark.parametrize("target", ["assistant_action_resolutions", "assistant_actions"])
async def test_canonical_route_audit_or_status_failure_rolls_back(canonical, monkeypatch, target):
    db = canonical
    row = await create_unknown(db, monkeypatch)
    async with client(db) as http:
        body = await review(http, row)
        await db.owner.execute("CREATE OR REPLACE FUNCTION ack_test_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic write failure'; END $$")
        operation, field = ("INSERT", "action_id") if target == "assistant_action_resolutions" else ("UPDATE", "id")
        await db.owner.execute(f"CREATE TRIGGER ack_test_fail BEFORE {operation} ON {target} FOR EACH ROW WHEN (NEW.{field}='{row['id']}'::uuid) EXECUTE FUNCTION ack_test_fail()")
        try:
            assert (await http.post(path(row) + "/recover-acknowledgement", json=body)).status_code == 503
            assert dict(await db.owner.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", row["id"])) == row
            assert await events(db, row) == []
            detail = (await http.get(path(row))).json()
            assert detail["status"] == "unknown" and detail["acknowledgement_recovery"]["source_digest"] == body["expected_source_digest"]
        finally:
            await db.owner.execute(f"DROP TRIGGER ack_test_fail ON {target}")
            await db.owner.execute("DROP FUNCTION ack_test_fail()")


@pytest.mark.parametrize("change", ["session", "role", "source"])
async def test_canonical_lock_wait_does_not_extend_auth_or_evidence(canonical, monkeypatch, change):
    db = canonical
    row = await create_unknown(db, monkeypatch)
    checked = asyncio.Event()
    load = actions.load_session_principal
    async def observe(conn, claims):
        result = await load(conn, claims)
        checked.set()
        return result
    monkeypatch.setattr(actions, "load_session_principal", observe)
    async with client(db) as http:
        body = await review(http, row)
        async with db.owner.transaction():
            await db.owner.fetchrow("SELECT id FROM assistant_actions WHERE id=$1 FOR UPDATE", row["id"])
            task = asyncio.create_task(http.post(path(row) + "/recover-acknowledgement", json=body))
            await asyncio.wait_for(checked.wait(), 3)
            if change == "session":
                await db.owner.execute("UPDATE security_sessions SET revoked=true,revoked_at=now() WHERE id=$1::uuid", db.session)
            elif change == "role":
                await db.owner.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1::uuid", db.actor)
            else:
                await db.owner.execute("UPDATE assistant_actions SET error='source changed' WHERE id=$1", row["id"])
        assert (await asyncio.wait_for(task, 4)).status_code in {401, 403, 409}
    await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1::uuid", db.actor)
    assert await events(db, row) == [] and db.provider.send_email.await_count == 1


async def test_canonical_constraints_restrict_retention_without_worker_audit_access(canonical, monkeypatch):
    db = canonical
    row = await create_unknown(db, monkeypatch)
    async with client(db) as http:
        body = await review(http, row)
        assert (await http.post(path(row) + "/recover-acknowledgement", json=body)).status_code == 200
    before = deepcopy((await events(db, row))[0])
    async with acquire_with_tenant(db.pool, None) as conn:
        assert await conn.fetchval("SELECT count(*) FROM assistant_action_resolutions") == 0
    async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
        assert await conn.execute("UPDATE assistant_action_resolutions SET reason='changed' WHERE action_id=$1", row["id"]) == "UPDATE 0"
        assert await conn.execute("DELETE FROM assistant_action_resolutions WHERE action_id=$1", row["id"]) == "DELETE 0"
    for sql, value in (("DELETE FROM assistant_actions WHERE id=$1", row["id"]), ("DELETE FROM tenants WHERE id=$1::uuid", db.tenant)):
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await db.owner.execute(sql, value)
    assert (await events(db, row))[0] == before
