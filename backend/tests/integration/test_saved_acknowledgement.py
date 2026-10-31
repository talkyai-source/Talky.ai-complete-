"""Opt-in private-schema PostgreSQL controls for saved acknowledgement recovery.

Never connect without an explicit disposable localhost *_test DSN. Migration
DDL is namespace-rewritten into one generated schema; no public objects change.
The supplied role must be neither superuser nor BYPASSRLS. No roles are created.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib
import json
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin import actions
from app.core.db_utils import acquire_with_tenant
from app.services.action_execution import DurableActionExecutor, unknown_result
from app.services.saved_acknowledgement import source_digest

pytestmark = pytest.mark.integration
MIGRATION = importlib.import_module("Alembic.versions.0062_saved_acknowledgement")


@pytest_asyncio.fixture
async def recovery_db(monkeypatch):
    dsn = os.getenv("TALKY_ACK_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit private TALKY_ACK_TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Only an explicitly authorized disposable localhost *_test database is permitted")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    schema, pool, created = "ack_recovery_" + uuid4().hex, None, False
    try:
        assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"), "Use a restricted fixture role"
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute("""
          CREATE TABLE tenants(id uuid PRIMARY KEY,business_name text,minutes_allocated int,minutes_used int);
          CREATE TABLE user_profiles(id uuid PRIMARY KEY,email text,name text,role text,tenant_id uuid,
            is_active boolean,is_verified boolean);
          CREATE TABLE roles(id uuid PRIMARY KEY,name text);
          CREATE TABLE tenant_users(user_id uuid,tenant_id uuid,role_id uuid,status text);
          CREATE TABLE security_sessions(id uuid PRIMARY KEY,user_id uuid,created_at timestamptz,last_active_at timestamptz,
            expires_at timestamptz,mfa_verified boolean,revoked boolean,requires_verification boolean);
          CREATE TABLE campaigns(id uuid PRIMARY KEY,tenant_id uuid);
          CREATE TABLE calls(id uuid PRIMARY KEY,tenant_id uuid,campaign_id uuid,lead_id uuid);
          CREATE TABLE assistant_actions(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            type text,status text,idempotency_key text,input_data jsonb,output_data jsonb,
            call_id uuid,lead_id uuid,campaign_id uuid,user_id uuid,conversation_id uuid,connector_id uuid,triggered_by text,
            created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,completed_at timestamptz,scheduled_at timestamptz,
            outcome_status text,error text,duration_ms int);
          CREATE UNIQUE INDEX assistant_action_original_key ON assistant_actions(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
        """)
        statements = []
        monkeypatch.setattr(MIGRATION, "op", SimpleNamespace(execute=statements.append))
        MIGRATION.upgrade()
        async with conn.transaction():
            for sql in statements:
                await conn.execute(sql.replace("public.", f'"{schema}".'))
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=10,
                                         server_settings={"search_path": schema})
        db = SimpleNamespace(conn=conn, pool=pool, schema=schema, tenant=str(uuid4()), actor=str(uuid4()), session=str(uuid4()))
        await conn.execute("INSERT INTO tenants VALUES($1::uuid,'Synthetic',0,0)", db.tenant)
        await conn.execute("INSERT INTO user_profiles VALUES($1::uuid,'admin@example.invalid','Synthetic','platform_admin',$2::uuid,true,true)", db.actor, db.tenant)
        await conn.execute("INSERT INTO security_sessions VALUES($1::uuid,$2::uuid,now(),now(),now()+interval '1 hour',true,false,false)", db.session, db.actor)
        yield db
    finally:
        if pool:
            await pool.close()
        if created:
            await conn.execute("SET search_path TO public")
            await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def saved(db, *, voice=False):
    aid, child = str(uuid4()), str(uuid4())
    proof = dict(identity_version="authorization_row_v1", tenant_id=db.tenant, provider="gmail",
                 connector_id=str(uuid4()), account_row_id=str(uuid4()))
    payload = dict(to=["saved@example.invalid"], subject="Résumé café", body="Private original café body", confirm=True, _reviewed_connector=proof)
    result = dict(proof, message_id="saved-message", action="send_email", action_id=aid, child_action_id=child,
                  success=True, confirmation_allowed=True, status="accepted", recipients=payload["to"], recipient_count=1)
    call, campaign, lead = None, None, None
    key, triggered = f"assistant:{db.actor}:prop_{uuid4().hex[:16]}", "assistant"
    if voice:
        call, campaign, lead = [str(uuid4()) for _ in range(3)]
        await db.conn.execute("INSERT INTO campaigns VALUES($1::uuid,$2::uuid)", campaign, db.tenant)
        await db.conn.execute("INSERT INTO calls VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid)", call, db.tenant, campaign, lead)
        request = str(uuid4())
        payload = {"parameters": {"recipient": "saved@example.invalid", "subject": "Saved", "body": "Saved body"},
                   "confirmation": {"request_id": request, "turn": "2", "caller_text_hash": "a" * 64}}
        result.update(version=1, status="provider_accepted")
        result.pop("recipients")
        result.pop("recipient_count")
        key, triggered = "voice-" + request, "voice"
    intent = {"parameters": payload, "request_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()}
    outer = {**unknown_result("send_email", aid), "provider_result": result}
    row = await db.conn.fetchrow("""INSERT INTO assistant_actions
      (id,tenant_id,type,status,idempotency_key,input_data,output_data,user_id,triggered_by,started_at,completed_at,call_id,campaign_id,lead_id)
      VALUES($1::uuid,$2::uuid,'send_email','unknown',$3,$4::jsonb,$5::jsonb,$6::uuid,$7,now(),now(),$8::uuid,$9::uuid,$10::uuid) RETURNING *""",
      aid, db.tenant, key, json.dumps(intent), json.dumps(outer), db.actor, triggered, call, campaign, lead)
    return dict(row), {"request_id": str(uuid4()), "expected_source_digest": source_digest(dict(row)), "reason": "Reviewed saved original provider acceptance"}


@asynccontextmanager
async def client(db):
    app = FastAPI()
    app.include_router(actions.router, prefix="/admin")
    async def current(request: Request):
        request.state.authenticated_user_id, request.state.authenticated_session_id = db.actor, db.session
        return dependencies.CurrentUser(id=db.actor, email="admin@example.invalid", tenant_id=db.tenant, role="platform_admin")
    app.dependency_overrides[dependencies.get_current_user] = current
    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=db.pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as http:
        yield http


async def post(http, row, body):
    return await http.post(f"/admin/actions/{row['id']}/recover-acknowledgement", json=body)


async def events(db):
    async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
        return await conn.fetch("SELECT * FROM assistant_action_resolutions")


@pytest.mark.parametrize("voice", [False, True])
async def test_real_atomic_recovery_concurrent_replay_and_lost_response(recovery_db, voice):
    db = recovery_db
    row, body = await saved(db, voice=voice)
    async with client(db) as http:
        responses = await asyncio.gather(*(post(http, row, body) for _ in range(3)))
        assert [r.status_code for r in responses] == [200] * 3, [r.text for r in responses]
        assert all(r.json() == responses[0].json() for r in responses)
        assert (await post(http, row, body)).json() == responses[0].json()  # Lost response retry
        assert (await post(http, row, {**body, "reason": "Changed reason"})).status_code == 409
        assert (await post(http, row, {**body, "request_id": str(uuid4())})).status_code == 409
    current = dict(await db.conn.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", row["id"]))
    assert current["status"] == "completed"
    assert {k: v for k, v in current.items() if k not in {"status", "output_data"}} == {k: v for k, v in row.items() if k not in {"status", "output_data"}}
    recorded = await events(db)
    assert len(recorded) == 1 and json.loads(recorded[0]["original_output"]) == json.loads(row["output_data"])
    async def never():
        raise AssertionError("Recovered executor receipt must not dispatch")
    replay = await DurableActionExecutor(db.pool).execute(tenant_id=db.tenant, action="send_email",
        idempotency_key=row["idempotency_key"], payload=json.loads(row["input_data"])["parameters"], executor=never)
    assert replay["replayed"] is True and replay["success"] is True


@pytest.mark.parametrize("target", ["assistant_action_resolutions", "assistant_actions"])
async def test_real_audit_or_update_failure_rolls_back_both(recovery_db, target):
    db = recovery_db
    row, body = await saved(db)
    await db.conn.execute("""CREATE FUNCTION fail_test_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic write failure'; END $$""")
    operation = "INSERT" if target == "assistant_action_resolutions" else "UPDATE"
    await db.conn.execute(f"CREATE TRIGGER synthetic_failure BEFORE {operation} ON {target} FOR EACH ROW EXECUTE FUNCTION fail_test_write()")
    async with client(db) as http:
        assert (await post(http, row, body)).status_code == 503
    assert dict(await db.conn.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", row["id"])) == row
    assert await events(db) == []


@pytest.mark.parametrize("change", ["session", "role", "stale"])
async def test_real_row_lock_wait_does_not_extend_authority_or_source(recovery_db, change, monkeypatch):
    db = recovery_db
    row, body = await saved(db)
    checked = asyncio.Event()
    load = actions.load_session_principal
    async def observed(conn, claims):
        result = await load(conn, claims)
        checked.set()
        return result
    monkeypatch.setattr(actions, "load_session_principal", observed)
    async with client(db) as http:
        async with db.conn.transaction():
            await db.conn.fetchrow("SELECT id FROM assistant_actions WHERE id=$1 FOR UPDATE", row["id"])
            task = asyncio.create_task(post(http, row, body))
            await asyncio.wait_for(checked.wait(), 2)
            if change == "session":
                await db.conn.execute("UPDATE security_sessions SET revoked=true WHERE id=$1::uuid", db.session)
            elif change == "role":
                await db.conn.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1::uuid", db.actor)
            else:
                await db.conn.execute("UPDATE assistant_actions SET error='changed' WHERE id=$1", row["id"])
        response = await asyncio.wait_for(task, 3)
    assert response.status_code in {401, 403, 409}
    await db.conn.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1::uuid", db.actor)
    assert await events(db) == []
    assert await db.conn.fetchval("SELECT status FROM assistant_actions WHERE id=$1", row["id"]) == "unknown"


async def test_migration_rls_actor_binding_immutability_and_retention(recovery_db):
    db = recovery_db
    row, body = await saved(db)
    async with client(db) as http:
        assert (await post(http, row, body)).status_code == 200
    original = (await events(db))[0]
    assert await db.conn.fetchval("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='assistant_action_resolutions'::regclass")
    async def insert_copy(conn, *, actor=None, tenant=None, action=None):
        await conn.execute("""INSERT INTO assistant_action_resolutions
          (id,tenant_id,action_id,actor_id,actor_role,request_id,source_digest,reason,original_status,
           original_output,original_timestamps,recovered_status,provider_status)
          VALUES($1,$2,$3,$4,'platform_admin',$5,$6,'Synthetic review','unknown','{}','{}','completed','accepted')""",
          uuid4(), tenant or original["tenant_id"], action or original["action_id"],
          actor or original["actor_id"], uuid4(), "a" * 64)

    # Bare worker bypass cannot read or insert an event.
    async with acquire_with_tenant(db.pool, None) as conn:
        assert await conn.fetchval("SELECT count(*) FROM assistant_action_resolutions") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await insert_copy(conn)
    for role in ("tenant_admin", "partner_admin"):
        await db.conn.execute("UPDATE user_profiles SET role=$1 WHERE id=$2::uuid", role, db.actor)
        async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
            assert await conn.fetchval("SELECT count(*) FROM assistant_action_resolutions") == 0
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await insert_copy(conn)
    await db.conn.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1::uuid", db.actor)
    async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await insert_copy(conn, actor=uuid4())
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await insert_copy(conn, tenant=uuid4())
    # No UPDATE/DELETE policy normally exposes a row to mutation. Temporary
    # test policies additionally exercise the append-only trigger itself.
    await db.conn.execute("CREATE POLICY test_update ON assistant_action_resolutions FOR UPDATE USING(true); CREATE POLICY test_delete ON assistant_action_resolutions FOR DELETE USING(true)")
    for statement in ("UPDATE assistant_action_resolutions SET reason='changed'", "DELETE FROM assistant_action_resolutions"):
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            async with acquire_with_tenant(db.pool, None, user_id=db.actor) as conn:
                await conn.execute(statement)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.conn.execute("DELETE FROM assistant_actions WHERE id=$1", row["id"])
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.conn.execute("DELETE FROM tenants WHERE id=$1::uuid", db.tenant)
    # Actor is a snapshot, so deleting that profile is not newly restricted.
    await db.conn.execute("DELETE FROM user_profiles WHERE id=$1::uuid", db.actor)
    await db.conn.execute("INSERT INTO user_profiles VALUES($1::uuid,'new@example.invalid','New','platform_admin',$2::uuid,true,true)", db.actor, db.tenant)
    assert (await events(db))[0] == original
    with pytest.raises(RuntimeError, match="retained"):
        MIGRATION.downgrade()
