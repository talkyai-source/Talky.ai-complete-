"""Normal-auth connector session admission on owned, migrated PostgreSQL rows.

Authentication, JWT/session validation, RLS, encryption, audit and lifecycle SQL
are real. Only the provider boundary is synthetic. Lock-entry observers forward
the actual implementation unchanged and require the exact backend's real wait.
No OAuth/system admission contract or live-provider behavior is asserted here.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints import connectors as tenant_connectors
from app.api.v1.endpoints.admin import connectors as admin_connectors
from app.core import postgres_adapter
from app.core.jwt_security import decode_and_validate_token
from app.domain.services import audit_logger as audit_module
from app.domain.services.audit_logger import AuditLogger
from app.infrastructure.connectors import encryption
from app.infrastructure.connectors.base import OAuthTokens
from app.services import connector_lifecycle as lifecycle
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
ADMIN_WRITERS = ("admin_reconnect", "admin_revoke")
TENANT_WRITERS = (
    "card_disconnect", "delete_connector", "tenant_refresh", "salesforce_settings", "webhook_rotation",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@pytest_asyncio.fixture
async def connector_session_db(crud_db, monkeypatch):  # noqa: F811
    db = crud_db
    db.connector_ids = []
    db.connector_account_ids = []
    db.provider_calls = []
    db.provider_entered = asyncio.Event()
    db.provider_release = asyncio.Event()
    db.provider_release.set()
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT,UPDATE ON connectors,connector_accounts,reminders TO "{role}"')
    await db.owner.execute(f'GRANT SELECT ON meetings TO "{role}"')
    # The fluent adapter opens separate connections. Pin those to the same real
    # restricted role instead of accidentally exercising SQL as the owner.
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL", os.environ["TEST_ADMIN_DATABASE_URL"] + "?role=" + role)
    original_adapter = postgres_adapter.QueryBuilder._execute_with_conn
    db.adapter_reads = []

    async def observed_adapter(query, conn):
        flags = dict(await conn.fetchrow("""SELECT current_user AS role,rolsuper,rolbypassrls
            FROM pg_roles WHERE rolname=current_user"""))
        assert flags["role"] == role and not flags["rolsuper"] and not flags["rolbypassrls"]
        db.adapter_reads.append({"table": query.table_name, **flags})
        return await original_adapter(query, conn)

    monkeypatch.setattr(postgres_adapter.QueryBuilder, "_execute_with_conn", observed_adapter)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", base64.urlsafe_b64encode(
        hashlib.sha256(b"synthetic-connector-session-fixture-key").digest()).decode())
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    monkeypatch.setattr(encryption, "_encryption_service", None)
    db.cipher = encryption.get_encryption_service()

    class SyntheticProvider:
        provider_name = "gmail"

        def apply_config(self, _config):
            pass

        def config_from_tokens(self, _tokens):
            return {}

        async def refresh_tokens(self, token):
            valid_input = token == "synthetic-owned-refresh"
            assert valid_input, "Provider received unexpected synthetic credential"
            db.provider_calls.append({"operation": "refresh", "connector_id": str(db.connector_ids[0])})
            db.provider_entered.set()
            await db.provider_release.wait()
            return OAuthTokens(access_token="synthetic-rotated-access", refresh_token="synthetic-rotated-refresh",
                               expires_at=datetime.now(UTC) + timedelta(hours=1))

    def provider_factory(*, provider, tenant_id, connector_id, **_kwargs):
        assert provider == "gmail" and str(tenant_id) == str(db.tenants[1])
        assert UUID(str(connector_id)) in db.connector_ids
        return SyntheticProvider()

    async def revoke_provider(provider, token, *, tenant_id, connector_id):
        valid_input = provider == "gmail" and token == "synthetic-owned-refresh"
        assert valid_input, "Provider received unexpected synthetic credential"
        assert str(tenant_id) == str(db.tenants[1]) and UUID(str(connector_id)) in db.connector_ids
        db.provider_calls.append({"operation": "revoke", "connector_id": str(connector_id)})
        db.provider_entered.set()
        await db.provider_release.wait()
        return True

    monkeypatch.setattr(lifecycle.ConnectorFactory, "create", staticmethod(provider_factory))
    monkeypatch.setattr(admin_connectors, "revoke_token_at_provider", revoke_provider)
    monkeypatch.setattr(tenant_connectors, "_revoke_token_at_provider", revoke_provider)
    try:
        yield db
    finally:
        db.provider_release.set()
        # No test creates a meeting/reminder. These exact dependent IDs must be
        # removed before the inherited owned-identity/protected-state cleanup.
        async with db.owner.transaction():
            await db.owner.execute("DELETE FROM connector_accounts WHERE id=ANY($1::uuid[]) AND connector_id=ANY($2::uuid[])",
                                   db.connector_account_ids, db.connector_ids)
            await db.owner.execute("DELETE FROM connectors WHERE id=ANY($1::uuid[]) AND tenant_id=$2",
                                   db.connector_ids, db.tenants[1])


async def seed_connector(db, writer):
    connector_id, account_id = uuid4(), uuid4()
    db.connector_ids.append(connector_id)
    db.connector_account_ids.append(account_id)
    salesforce = writer in {"salesforce_settings", "webhook_rotation"}
    await db.owner.execute("""INSERT INTO connectors(id,tenant_id,type,provider,name,status,config)
        VALUES($1,$2,$3,$4,'Synthetic session authority connector','active','{}'::jsonb)""",
        connector_id, db.tenants[1], "crm" if salesforce else "email", "salesforce" if salesforce else "gmail")
    await db.owner.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,status,
        account_email,access_token_encrypted,refresh_token_encrypted,token_expires_at)
        VALUES($1,$2,$3,'active','synthetic-session@example.com',$4,$5,NOW()+INTERVAL '1 hour')""",
        account_id, connector_id, db.tenants[1], db.cipher.encrypt("synthetic-owned-access"),
        db.cipher.encrypt("synthetic-owned-refresh"))
    return connector_id


async def state(db, connector_id):
    parent = await db.owner.fetchrow("SELECT * FROM connectors WHERE id=$1", connector_id)
    accounts = await db.owner.fetch("SELECT * FROM connector_accounts WHERE connector_id=$1 ORDER BY id", connector_id)
    assert parent is not None and len(accounts) == 1
    # Full state is compared only in memory. Evidence receives digests, never
    # encrypted credentials or a newly generated webhook secret.
    return {"connector": dict(parent), "accounts": [dict(row) for row in accounts]}


async def receipts(db, connector_id):
    rows = await db.owner.fetch("""SELECT event_id,action,actor_id,metadata FROM audit_logs
        WHERE tenant_id=$1 AND resource_type='connector' AND resource_id=$2
        ORDER BY event_time,created_at,event_id""", db.tenants[1], connector_id)
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item["metadata"], str):
            item["metadata"] = json.loads(item["metadata"])
        result.append(item)
    return result


async def write(db, actor, writer, connector_id):
    paths = {
        "admin_reconnect": ("POST", f"/admin/connectors/{connector_id}/reconnect"),
        "admin_revoke": ("POST", f"/admin/connectors/{connector_id}/revoke"),
        "card_disconnect": ("POST", "/connectors/email/disconnect"),
        "delete_connector": ("DELETE", f"/connectors/{connector_id}"),
        "tenant_refresh": ("POST", f"/connectors/{connector_id}/refresh"),
        "salesforce_settings": ("PUT", "/connectors/salesforce/settings"),
        "webhook_rotation": ("POST", "/connectors/salesforce/webhook-token"),
    }
    method, path = paths[writer]
    kwargs = {"json": {"log_calls": False}} if writer == "salesforce_settings" else {}
    return await call(db, actor, method, path, **kwargs)


def observe_wait_entry(db, monkeypatch, writer, connector_id, lock_kind):
    entered = asyncio.Event()
    observation = {}
    if lock_kind == "audit":
        original_begin = lifecycle.ConnectorLifecycle._begin
        original_execute = asyncpg.Connection.execute

        async def observed_begin(service, conn, tenant_id, permission=None):
            if (permission is not None and service.actor.source == writer
                    and service.actor.user_id == str(db.users[0]) and str(tenant_id) == str(db.tenants[1])):
                observation["candidate_pid"] = conn.get_server_pid()
            return await original_begin(service, conn, tenant_id, permission)

        async def observed_execute(conn, query, *args, **kwargs):
            if (conn.get_server_pid() == observation.get("candidate_pid")
                    and f"SELECT pg_advisory_xact_lock({audit_module._CHAIN_LOCK_KEY})" in query):
                observation["waiter_pid"] = conn.get_server_pid()
                entered.set()
            return await original_execute(conn, query, *args, **kwargs)

        # Identify the exact request connection, observe its unchanged audit
        # SQL entering asyncpg, then require PostgreSQL's actual blocking wait.
        monkeypatch.setattr(lifecycle.ConnectorLifecycle, "_begin", observed_begin)
        monkeypatch.setattr(asyncpg.Connection, "execute", observed_execute)
    elif lock_kind == "account":
        original_fetch = asyncpg.Connection.fetch

        async def observed_fetch(conn, query, *args, **kwargs):
            if (conn.get_server_pid() != db.owner.get_server_pid()
                    and "FROM connector_accounts" in query and "FOR UPDATE" in query
                    and len(args) == 2 and str(args[0]) == str(connector_id)
                    and str(args[1]) == str(db.tenants[1])):
                observation["waiter_pid"] = conn.get_server_pid()
                entered.set()
            return await original_fetch(conn, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.Connection, "fetch", observed_fetch)
    else:
        original = asyncpg.Connection.fetchrow

        async def observed_fetchrow(conn, query, *args, **kwargs):
            if (conn.get_server_pid() != db.owner.get_server_pid()
                    and "FROM connectors" in query and "FOR UPDATE" in query
                    and len(args) == 2 and str(args[0]) == str(connector_id)
                    and str(args[1]) == str(db.tenants[1])):
                observation["waiter_pid"] = conn.get_server_pid()
                entered.set()
            return await original(conn, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.Connection, "fetchrow", observed_fetchrow)
    return entered, observation


async def prove_wait(db, task, waiter_pid, lock_kind):
    async with asyncio.timeout(2):
        while True:
            await db.owner.execute("SELECT pg_stat_clear_snapshot()")
            waiting = await db.owner.fetchval("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity a
                WHERE a.datname=current_database() AND a.pid=$1 AND a.wait_event_type='Lock'
                  AND $2=ANY(pg_blocking_pids(a.pid))
                  AND (($3='audit' AND position('pg_advisory_xact_lock' IN a.query)>0)
                    OR ($3='parent' AND position('FROM connectors' IN a.query)>0 AND position('FOR UPDATE' IN a.query)>0)
                    OR ($3='account' AND position('FROM connector_accounts' IN a.query)>0 AND position('FOR UPDATE' IN a.query)>0)))""",
                waiter_pid, db.owner.get_server_pid(), lock_kind)
            if waiting:
                return
            assert not task.done(), "Connector operation ended before its real lock wait"
            await asyncio.sleep(0.01)


async def logout_exact_session(db, actor, index):
    claims = decode_and_validate_token(actor.headers["Authorization"].split()[1])
    assert claims["sub"] == str(db.users[index])
    session_id = UUID(claims["sid"])
    result = await call(db, actor, "POST", "/auth/logout")
    assert result.status_code == 200
    assert await db.owner.fetchval("SELECT revoked FROM security_sessions WHERE id=$1 AND user_id=$2",
                                   session_id, db.users[index]) is True


async def logout_while_waiting(db, monkeypatch, writer, lock_kind, index):
    assert lock_kind in {"audit", "parent", "account"}
    connector_id = await seed_connector(db, writer)
    before, before_audits = await state(db, connector_id), await receipts(db, connector_id)
    entered, entry = observe_wait_entry(db, monkeypatch, writer, connector_id, lock_kind)
    evidence = {"case": "logout_before_connector_admission", "writer": writer, "lock_kind": lock_kind,
                "expected_status": 401, "before_sha256": digest(before)}
    RUN_EVIDENCE.append(evidence)
    pending = None
    async with signed_in(db, index) as actor:
        try:
            async with db.owner.transaction():
                if lock_kind == "audit":
                    await AuditLogger.lock_transaction(db.owner)
                elif lock_kind == "account":
                    # Hold only the owned account: the HTTP writer must acquire
                    # its parent normally, then wait on the account fetch.
                    account_id = before["accounts"][0]["id"]
                    locked_id = await db.owner.fetchval("""SELECT id FROM connector_accounts
                        WHERE id=$1 AND connector_id=$2 AND tenant_id=$3 FOR UPDATE""",
                        account_id, connector_id, db.tenants[1])
                    assert locked_id == account_id
                else:
                    await db.owner.fetchrow("SELECT id FROM connectors WHERE id=$1 FOR UPDATE", connector_id)
                pending = asyncio.create_task(write(db, actor, writer, connector_id))
                try:
                    await asyncio.wait_for(entered.wait(), 8)
                except TimeoutError:
                    if pending.done() and not pending.cancelled() and pending.exception() is None:
                        evidence["early_http_status"] = pending.result().status_code
                    raise
                await prove_wait(db, pending, entry["waiter_pid"], lock_kind)
                evidence.update(actual_lock_wait_observed=True, waiter_pid=entry["waiter_pid"])
                await asyncio.wait_for(logout_exact_session(db, actor, index), 3)
                evidence["logout_committed_before_lock_release"] = True
            response = await asyncio.wait_for(pending, 8)
            after, after_audits = await state(db, connector_id), await receipts(db, connector_id)
            same_state, same_audits = after == before, after_audits == before_audits
            evidence.update(http_status=response.status_code, after_sha256=digest(after), state_unchanged=same_state,
                            mutation_audits_unchanged=same_audits, provider_entries=len(db.provider_calls))
            # Response bodies may contain a synthetic rotated webhook token;
            # report only status/digests even when baseline failure is expected.
            assert response.status_code == 401
            assert same_state and same_audits and not db.provider_calls
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def assert_saved_success(db, writer, connector_id, response, index):
    assert response.status_code == 200
    saved, audits = await state(db, connector_id), await receipts(db, connector_id)
    assert audits and all(str(row["actor_id"]) == str(db.users[index]) for row in audits)
    expected = (["connector.refresh_intent", "connector.refresh_installed"]
                if writer in {"admin_reconnect", "tenant_refresh"} else
                ["connector.config_updated"] if writer in {"salesforce_settings", "webhook_rotation"} else
                ["connector.disabled_locally", "connector.provider_revocation_result"])
    assert [row["action"] for row in audits] == expected
    operation_ids = {row["metadata"]["operation_id"] for row in audits}
    assert len(operation_ids) == 1
    parent, account = saved["connector"], saved["accounts"][0]
    if writer in {"admin_reconnect", "tenant_refresh"}:
        assert parent["status"] == "active" and account["refresh_state"] == "idle"
        assert account["credential_generation"] == 1 and account["rotation_count"] == 1
        installed_expected_token = db.cipher.decrypt(account["access_token_encrypted"]) == "synthetic-rotated-access"
        assert installed_expected_token, "Rotated synthetic credential was not installed"
        assert len(db.provider_calls) == 1
    elif writer in {"salesforce_settings", "webhook_rotation"}:
        config = parent["config"] if isinstance(parent["config"], dict) else json.loads(parent["config"])
        assert parent["status"] == account["status"] == "active" and not db.provider_calls
        if writer == "salesforce_settings":
            assert config["log_calls"] is False
        else:
            installed_webhook = bool(config.get("webhook_token_hash") and config.get("webhook_token_encrypted"))
            assert installed_webhook
            readable_webhook = bool(db.cipher.decrypt(config["webhook_token_encrypted"]))
            assert readable_webhook
    else:
        assert parent["status"] == "disconnected" and account["status"] == "revoked"
        assert parent["provider_revocation_status"] == "confirmed"
        assert account["access_token_encrypted"] is None and account["refresh_token_encrypted"] is None
        assert len(db.provider_calls) == 1
    if writer in ADMIN_WRITERS:
        body = response.json()
        assert body["success"] is True and body["connector_id"] == str(connector_id)
        expected_audit = audits[-1] if writer == "admin_reconnect" else audits[0]
        assert body["audit_event_id"] == str(expected_audit["event_id"])
        assert body["operation_id"] in operation_ids
    return {"saved_state_sha256": digest(saved), "actions": expected, "provider_entries": len(db.provider_calls),
            "audit_ids": [str(row["event_id"]) for row in audits], "operation_id": next(iter(operation_ids))}


@pytest.mark.parametrize("writer", ADMIN_WRITERS)
@pytest.mark.parametrize("lock_kind", ["audit", "parent"])
async def test_admin_connector_logout_while_waiting_denies_admission(connector_session_db, monkeypatch, writer, lock_kind):
    await logout_while_waiting(connector_session_db, monkeypatch, writer, lock_kind, 0)


@pytest.mark.parametrize("writer", ADMIN_WRITERS)
async def test_admin_connector_current_session_can_complete(connector_session_db, writer):
    db = connector_session_db
    connector_id = await seed_connector(db, writer)
    async with signed_in(db, 0) as actor:
        response = await write(db, actor, writer, connector_id)
        evidence = await assert_saved_success(db, writer, connector_id, response, 0)
        RUN_EVIDENCE.append({"case": "current_admin_success", "writer": writer, "http_status": response.status_code, **evidence})


@pytest.mark.parametrize("writer", ADMIN_WRITERS)
async def test_admitted_connector_effect_finishes_after_logout_without_replay(connector_session_db, writer):
    db = connector_session_db
    connector_id = await seed_connector(db, writer)
    db.provider_release.clear()
    pending = None
    async with signed_in(db, 0) as actor:
        try:
            pending = asyncio.create_task(write(db, actor, writer, connector_id))
            await asyncio.wait_for(db.provider_entered.wait(), 8)
            admitted = await receipts(db, connector_id)
            assert len(admitted) == 1 and len(db.provider_calls) == 1
            await logout_exact_session(db, actor, 0)
            me = await call(db, actor, "GET", "/auth/me")
            assert me.status_code == 401
            db.provider_release.set()
            response = await asyncio.wait_for(pending, 8)
            evidence = await assert_saved_success(db, writer, connector_id, response, 0)
            assert evidence["audit_ids"][0] == str(admitted[0]["event_id"])
            assert evidence["operation_id"] == admitted[0]["metadata"]["operation_id"]
            RUN_EVIDENCE.append({"case": "admitted_effect_finishes_after_logout", "writer": writer,
                                 "fresh_identity_status": 401, "http_status": response.status_code, **evidence})
        finally:
            db.provider_release.set()
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("writer", TENANT_WRITERS)
async def test_human_connector_writer_logout_during_parent_wait_denies_admission(connector_session_db, monkeypatch, writer):
    await logout_while_waiting(connector_session_db, monkeypatch, writer, "parent", 1)


@pytest.mark.parametrize("writer", TENANT_WRITERS)
async def test_human_connector_writer_current_session_can_complete(connector_session_db, writer):
    db = connector_session_db
    connector_id = await seed_connector(db, writer)
    async with signed_in(db, 1) as actor:
        response = await write(db, actor, writer, connector_id)
        evidence = await assert_saved_success(db, writer, connector_id, response, 1)
        RUN_EVIDENCE.append({"case": "current_tenant_success", "writer": writer, "http_status": response.status_code, **evidence})


@pytest.mark.parametrize("writer,index", [
    ("admin_reconnect", 0), ("admin_revoke", 0), ("salesforce_settings", 1),
])
async def test_connector_logout_during_account_wait_denies_admission(connector_session_db, monkeypatch, writer, index):
    await logout_while_waiting(connector_session_db, monkeypatch, writer, "account", index)
