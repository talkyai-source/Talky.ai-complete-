"""Restricted-role PostgreSQL races; every provider/encryption port is synthetic.

Requires an explicit migrated disposable localhost database ending in _test.
No provider network calls and no mutations to unrelated fixtures.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from starlette.requests import Request

from app.infrastructure.connectors.base import OAuthTokens
from app.services import connector_lifecycle as lifecycle
from app.services.connector_resolver import _authorization_snapshot_for_row
from app.infrastructure.assistant.tools.inbox import _mark_email_authorization_expired
from app.domain.services.audit_logger import AuditEvent
from app.core.db_utils import acquire_with_tenant

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def connector_db(monkeypatch):
    dsn = os.getenv("TEST_ADMIN_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable local TEST_ADMIN_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.endswith("_test")
    owner = await asyncpg.connect(dsn)
    role, tenant, other, actor_id = "ad03_" + uuid4().hex, uuid4(), uuid4(), uuid4()
    actor_session_id = uuid4()
    pool, triggers = None, []
    enc = NS(encrypt=lambda value: "synthetic:" + value,
             decrypt=lambda value: value.removeprefix("synthetic:"))
    monkeypatch.setattr(lifecycle, "get_encryption_service", lambda: enc)
    monkeypatch.setenv("AUDIT_LOG_SIGNING_KEY", "synthetic-test-only")
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON connectors,connector_accounts,meetings,reminders TO "{role}"')
        await owner.execute(f'GRANT SELECT ON tenants TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT ON audit_logs TO "{role}"')
        await owner.execute(f'GRANT SELECT ON user_profiles,tenant_users,roles,permissions,role_permissions,user_permissions TO "{role}"')
        await owner.execute(f'GRANT SELECT ON security_sessions TO "{role}"')
        for tid in (tenant, other):
            await owner.execute("INSERT INTO tenants(id,business_name) VALUES($1,'AD03 isolated fixture')", tid)
        await owner.execute("""INSERT INTO user_profiles(id,email,role,is_active,is_verified,email_verified_at,tenant_id)
            VALUES($1,$2,'platform_admin',TRUE,TRUE,NOW(),$3)""", actor_id, f"{actor_id}@example.invalid", tenant)
        # Direct lifecycle tests retain real profile/session queries, but this
        # exact owned session is fixture setup, not normal HTTP login evidence.
        await owner.execute("""INSERT INTO security_sessions
            (id,user_id,session_token_hash,expires_at,mfa_verified)
            VALUES($1,$2,$3,NOW()+INTERVAL '1 hour',TRUE)""",
            actor_session_id, actor_id, "synthetic-ad03-session-" + actor_session_id.hex)
        actor_request = Request({"type": "http", "method": "POST", "path": "/connectors", "headers": []})
        actor_request.state.authenticated_user_id = str(actor_id)
        actor_request.state.authenticated_session_id = str(actor_session_id)

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='10s'")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, setup=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert tuple(flags.values()) == (False, False)

        async def new_connector(*, status="active", provider="gmail", config=None, when=None, tid=tenant):
            cid, aid = uuid4(), uuid4()
            ctype = "crm" if provider in {"salesforce", "hubspot"} else "email"
            await owner.execute("""INSERT INTO connectors(id,tenant_id,type,provider,status,config,created_at)
                VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)""", cid, tid, ctype, provider, status,
                json.dumps(config or {}), when or datetime.now(timezone.utc))
            if status != "pending":
                await owner.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,status,
                    access_token_encrypted,refresh_token_encrypted,last_refreshed_at,token_expires_at)
                    VALUES($1,$2,$3,$4,'synthetic:old-access','synthetic:old-refresh',NOW(),NOW()+INTERVAL '1 hour')""",
                    aid, cid, tid, "expired" if status == "expired" else "active")
            return str(cid), str(aid)

        async def trigger_failure(table, cid, condition):
            name = "ad03_fault_" + uuid4().hex
            selector = ("NEW.resource_id" if table == "audit_logs" else
                        "NEW.id" if table == "connectors" else "NEW.connector_id")
            await owner.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF {selector}='{UUID(cid)}'::uuid AND ({condition}) THEN
                    RAISE EXCEPTION 'synthetic AD03 database fault'; END IF; RETURN NEW; END $$""")
            await owner.execute(f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()")
            triggers.append((table, name))

        async def state(cid):
            parent = await owner.fetchrow("SELECT * FROM connectors WHERE id=$1::uuid", cid)
            accounts = await owner.fetch("SELECT * FROM connector_accounts WHERE connector_id=$1::uuid ORDER BY created_at DESC", cid)
            return dict(parent), [dict(row) for row in accounts]

        db = NS(owner=owner, pool=pool, client=NS(pool=pool), tenant=str(tenant), other=str(other),
                actor=NS(id=str(actor_id), role="platform_admin", tenant_id=str(tenant)),
                actor_session_id=str(actor_session_id), actor_request=actor_request,
                new_connector=new_connector, trigger_failure=trigger_failure, state=state)
        db.service = lifecycle.ConnectorLifecycle(db.client, actor=lifecycle.ConnectorActor.system("integration_fixture"))
        db.audit = db.service.audit
        yield db
    finally:
        if pool:
            await pool.close()
        for table, name in triggers:
            await owner.execute(f"DROP TRIGGER {name} ON {table}")
            await owner.execute(f"DROP FUNCTION {name}()")
        await owner.execute("DELETE FROM reminders WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
        await owner.execute("DELETE FROM meetings WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
        await owner.execute("DELETE FROM connectors WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
        await owner.execute("DELETE FROM audit_logs WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
        await owner.execute("DELETE FROM security_sessions WHERE id=$1 AND user_id=$2", actor_session_id, actor_id)
        await owner.execute("DELETE FROM user_profiles WHERE tenant_id=ANY($1::uuid[])", [tenant, other])
        await owner.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", [tenant, other])
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


def provider(*, refresh=None, name="gmail", config=None):
    return NS(provider_name=name, refresh_tokens=refresh or AsyncMock(return_value=tokens()),
              apply_config=lambda value: None, config_from_tokens=lambda value: config)


def tokens():
    return OAuthTokens(access_token="rotated-access", refresh_token="rotated-refresh",
                       expires_at=datetime.now(timezone.utc)+timedelta(hours=1))


def account_data():
    return dict(access_token_encrypted="synthetic:new-access", refresh_token_encrypted="synthetic:new-refresh",
                token_expires_at=datetime.now(timezone.utc)+timedelta(hours=1), scopes=["synthetic"],
                account_email="fixture@example.invalid", external_account_id="synthetic-subject")


async def test_concurrent_refresh_dispatches_only_once(connector_db):
    db = connector_db
    cid, aid = await db.new_connector()
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        started.set()
        await release.wait()
        return tokens()
    first = provider(refresh=AsyncMock(side_effect=delayed))
    second = provider()
    task = asyncio.create_task(db.service.refresh(db.tenant, cid, connector=first))
    try:
        await asyncio.wait_for(started.wait(), 3)
        with pytest.raises(lifecycle.ConnectorLifecycleError, match="already in progress"):
            await db.service.refresh(db.tenant, cid, connector=second)
        second.refresh_tokens.assert_not_awaited()
        release.set()
        _, saved = await task
        assert saved["credential_generation"] == 1 and saved["rotation_count"] == 1
        assert saved["refresh_state"] == "idle" and saved["refresh_claim_id"] is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "save_failure"])
async def test_unknown_refresh_is_never_replayed(connector_db, outcome):
    db = connector_db
    cid, _ = await db.new_connector(config={"preserve": True})
    remote = provider(config={"instance_url": "https://synthetic.example.invalid"})
    if outcome == "timeout":
        remote.refresh_tokens.side_effect = TimeoutError("synthetic provider outcome unknown")
    elif outcome == "cancel":
        remote.refresh_tokens.side_effect = asyncio.CancelledError()
    else:
        await db.trigger_failure("connector_accounts", cid, "NEW.credential_generation > OLD.credential_generation")
    with pytest.raises((Exception, asyncio.CancelledError)):
        await db.service.refresh(db.tenant, cid, connector=remote)
    parent, accounts = await db.state(cid)
    assert accounts[0]["refresh_state"] == "uncertain"
    assert accounts[0]["access_token_encrypted"] == "synthetic:old-access"
    assert json.loads(parent["config"]) == {"preserve": True}
    retry = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError, match="previous refresh outcome is unknown"):
        await db.service.refresh(db.tenant, cid, connector=retry)
    retry.refresh_tokens.assert_not_awaited()


async def test_revoke_wins_over_inflight_refresh_without_false_provider_confirmation(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        started.set()
        await release.wait()
        return tokens()
    task = asyncio.create_task(db.service.refresh(db.tenant, cid, connector=provider(refresh=AsyncMock(side_effect=delayed))))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert not (await db.service.disable(db.tenant, cid, revoke_token=AsyncMock(return_value=True))).provider_revoked
        release.set()
        with pytest.raises(lifecycle.ConnectorLifecycleError, match="changed during refresh"):
            await task
        parent, accounts = await db.state(cid)
        assert parent["status"] == "disconnected" and parent["provider_revocation_status"] == "unconfirmed"
        assert accounts[0]["status"] == "revoked" and accounts[0]["access_token_encrypted"] is None
        assert accounts[0]["refresh_token_encrypted"] is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("table", ["connectors", "connector_accounts"])
async def test_failed_local_disable_rolls_back_everything_and_does_not_revoke_remotely(connector_db, table):
    db = connector_db
    cid, _ = await db.new_connector()
    await db.trigger_failure(table, cid, "NEW.status IN ('disconnected','revoked')")
    remote = AsyncMock(return_value=True)
    with pytest.raises(asyncpg.RaiseError):
        await db.service.disable(db.tenant, cid, revoke_token=remote)
    remote.assert_not_awaited()
    parent, accounts = await db.state(cid)
    assert parent["status"] == accounts[0]["status"] == "active"
    assert accounts[0]["refresh_token_encrypted"] == "synthetic:old-refresh"


@pytest.mark.parametrize("confirmed", [True, False])
async def test_disable_cancels_only_own_pending_reminders_and_is_idempotent(connector_db, confirmed):
    db = connector_db
    cid, _ = await db.new_connector()
    other_cid, _ = await db.new_connector(tid=UUID(db.other))
    reminders = []
    for tid, connector in [(db.tenant, cid), (db.other, other_cid)]:
        meeting, reminder = uuid4(), uuid4()
        await db.owner.execute("""INSERT INTO meetings(id,tenant_id,connector_id,title,start_time,end_time)
            VALUES($1,$2::uuid,$3::uuid,'synthetic',NOW(),NOW()+INTERVAL '1 hour')""", meeting, tid, connector)
        await db.owner.execute("INSERT INTO reminders(id,tenant_id,meeting_id,type,scheduled_at) VALUES($1,$2::uuid,$3,'email',NOW())", reminder, tid, meeting)
        reminders.append(reminder)
    remote = AsyncMock(return_value=confirmed)
    assert (await db.service.disable(db.tenant, cid, revoke_token=remote)).provider_revoked == confirmed
    assert (await db.service.disable(db.tenant, cid, revoke_token=remote)).provider_revoked == confirmed
    remote.assert_awaited_once()
    statuses = [await db.owner.fetchval("SELECT status FROM reminders WHERE id=$1", rid) for rid in reminders]
    assert statuses == ["cancelled", "pending"]
    parent, _ = await db.state(cid)
    assert parent["provider_revocation_status"] == ("confirmed" if confirmed else "unconfirmed")


async def test_disabled_pending_callback_cannot_reactivate(connector_db):
    db = connector_db
    cid, _ = await db.new_connector(status="pending")
    await db.service.disable(db.tenant, cid, revoke_token=AsyncMock())
    with pytest.raises(lifecycle.ConnectorLifecycleError, match="no longer pending"):
        await db.service.activate(db.tenant, cid, "gmail", "email", account_data(), {})
    parent, accounts = await db.state(cid)
    assert parent["status"] == "disconnected" and not accounts


async def test_activation_failure_preserves_old_connection_and_all_config(connector_db):
    db = connector_db
    old, _ = await db.new_connector()
    new, _ = await db.new_connector(status="pending")
    await db.trigger_failure("connectors", new, "NEW.status='active'")
    with pytest.raises(asyncpg.RaiseError):
        await db.service.activate(db.tenant, new, "gmail", "email", account_data(), {})
    previous, previous_accounts = await db.state(old)
    pending, new_accounts = await db.state(new)
    assert previous["status"] == previous_accounts[0]["status"] == "active"
    assert previous_accounts[0]["refresh_token_encrypted"] == "synthetic:old-refresh"
    assert pending["status"] == "pending" and not new_accounts


async def test_salesforce_activation_carries_settings_and_preserves_other_provider(connector_db):
    db = connector_db
    old, _ = await db.new_connector(provider="salesforce", config={"webhook_token_hash": "synthetic", "log_calls": False,
                                                                  "instance_url": "old", "access_token": "never-copy"})
    other, _ = await db.new_connector(provider="hubspot")
    new, _ = await db.new_connector(provider="salesforce", status="pending")
    await db.service.activate(db.tenant, new, "salesforce", "crm", account_data(), {"instance_url": "new"},
                              carry_keys=("webhook_token_hash", "log_calls"))
    parent, accounts = await db.state(new)
    assert json.loads(parent["config"]) == {"webhook_token_hash": "synthetic", "log_calls": False, "instance_url": "new"}
    assert len(accounts) == 1 and accounts[0]["external_account_id"] == "synthetic-subject"
    previous, previous_accounts = await db.state(old)
    assert previous["status"] == previous_accounts[0]["status"] == "revoked"
    assert previous_accounts[0]["refresh_token_encrypted"] is None
    assert (await db.state(other))[0]["status"] == "active"


async def test_concurrent_callbacks_keep_newest_authorization(connector_db):
    db = connector_db
    older, _ = await db.new_connector(status="pending", when=datetime.now(timezone.utc)-timedelta(minutes=1))
    newer, _ = await db.new_connector(status="pending")
    outcomes = await asyncio.gather(
        db.service.activate(db.tenant, older, "gmail", "email", account_data(), {}),
        db.service.activate(db.tenant, newer, "gmail", "email", account_data(), {}), return_exceptions=True)
    assert not isinstance(outcomes[1], Exception)
    assert (await db.state(newer))[0]["status"] == "active"
    assert (await db.state(older))[0]["status"] != "active"
    assert await db.owner.fetchval("SELECT count(*) FROM connectors WHERE tenant_id=$1::uuid AND status='active'", db.tenant) == 1


async def test_tenant_mismatch_cannot_refresh_or_disable(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    remote = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError) as error:
        await db.service.refresh(db.other, cid, connector=remote)
    assert error.value.status_code == 404
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await db.service.disable(db.other, cid, revoke_token=AsyncMock())
    remote.refresh_tokens.assert_not_awaited()
    assert (await db.state(cid))[0]["status"] == "active"


async def test_stale_inbox_failure_cannot_expire_inflight_refresh(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    parent, accounts = await db.state(cid)
    proof = _authorization_snapshot_for_row(db.tenant, cid, "gmail", accounts[0])
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        started.set()
        await release.wait()
        return tokens()
    task = asyncio.create_task(db.service.refresh(db.tenant, cid, connector=provider(refresh=AsyncMock(side_effect=delayed))))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert not await _mark_email_authorization_expired(db.client, db.tenant, cid, authorization=proof)
        release.set()
        await task
        assert not await _mark_email_authorization_expired(db.client, db.tenant, cid, authorization=proof)
        assert (await db.state(cid))[0]["status"] == "active"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_expired_account_can_refresh_but_revoked_account_never_can(connector_db):
    db = connector_db
    cid, _ = await db.new_connector(status="expired")
    await db.service.refresh(db.tenant, cid, connector=provider(), allow_expired=True)
    assert (await db.state(cid))[0]["status"] == "active"
    await db.service.disable(db.tenant, cid, revoke_token=AsyncMock(return_value=True))
    remote = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await db.service.refresh(db.tenant, cid, connector=remote, allow_expired=True)
    remote.refresh_tokens.assert_not_awaited()


async def test_abandoned_claim_is_not_taken_over_even_after_a_day(connector_db):
    db = connector_db
    cid, aid = await db.new_connector()
    await db.owner.execute("""UPDATE connector_accounts SET refresh_state='in_flight',
        refresh_claim_id=$2,refresh_claimed_at=NOW()-INTERVAL '1 day' WHERE id=$1::uuid""", aid, uuid4())
    remote = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError, match="already in progress"):
        await db.service.refresh(db.tenant, cid, connector=remote)
    remote.refresh_tokens.assert_not_awaited()
    replacement, _ = await db.new_connector(status="pending")
    await db.service.activate(db.tenant, replacement, "gmail", "email", account_data(), {})
    parent, accounts = await db.state(cid)
    assert parent["status"] == "revoked" and accounts[0]["refresh_claim_id"] is None


async def test_parent_config_failure_cannot_partially_commit_refreshed_tokens(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    await db.trigger_failure("connectors", cid, "NEW.config ? 'instance_url'")
    with pytest.raises(asyncpg.RaiseError):
        await db.service.refresh(db.tenant, cid, connector=provider(config={"instance_url": "synthetic"}))
    parent, accounts = await db.state(cid)
    assert accounts[0]["credential_generation"] == 0
    assert accounts[0]["access_token_encrypted"] == "synthetic:old-access"
    assert accounts[0]["refresh_state"] == "uncertain"
    assert "instance_url" not in json.loads(parent["config"])


async def test_refresh_and_settings_merge_current_config_without_lost_update(connector_db):
    db = connector_db
    cid, _ = await db.new_connector(provider="salesforce", config={"instance_url": "old", "log_calls": True})
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        started.set()
        await release.wait()
        return tokens()
    task = asyncio.create_task(db.service.refresh(db.tenant, cid, connector=provider(name="salesforce",
        refresh=AsyncMock(side_effect=delayed), config={"instance_url": "new"})))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await db.service.update_config(db.tenant, cid, {"log_calls": False}, provider="salesforce")
        release.set()
        await task
        assert json.loads((await db.state(cid))[0]["config"]) == {"instance_url": "new", "log_calls": False}
        await db.service.disable(db.tenant, cid, revoke_token=AsyncMock(return_value=True))
        with pytest.raises(lifecycle.ConnectorLifecycleError):
            await db.service.update_config(db.tenant, cid, {"log_calls": True}, provider="salesforce")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_actual_callback_and_resolver_refresh_use_the_durable_lifecycle(connector_db, monkeypatch):
    """Actual API/provider-validation code and helper; only lookup/provider ports synthetic."""
    from starlette.requests import Request
    from app.api.v1.endpoints import connectors as endpoint
    from app.services import connector_resolver as resolver
    db = connector_db
    cid, _ = await db.new_connector(status="pending")
    pending, _ = await db.state(cid)
    # This API's existing sync read adapter is an explicit read-only test port;
    # every lifecycle mutation below executes real restricted-role PostgreSQL.
    class Lookup:
        def select(self, *_): return self
        def eq(self, *_): return self
        def single(self): return self
        def execute(self): return NS(data=pending, error=None)
    client = NS(pool=db.pool, table=lambda name: Lookup())
    remote = provider()
    remote.connector_type = "email"
    remote.oauth_scopes = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
    remote.exchange_code = AsyncMock(return_value=tokens())
    remote.set_access_token = AsyncMock()
    remote.get_profile = AsyncMock(return_value={"emailAddress": "fixture@example.invalid"})
    monkeypatch.setattr(endpoint.ConnectorFactory, "create", lambda **_: remote)
    monkeypatch.setattr(endpoint, "get_encryption_service", lifecycle.get_encryption_service)
    monkeypatch.setattr(endpoint, "get_oauth_state_manager", lambda: NS(validate_state=AsyncMock(return_value={
        "tenant_id": db.tenant, "user_id": db.actor.id, "provider": "gmail", "redirect_uri": "https://example.invalid/cb",
        "code_verifier": "synthetic", "connector_id": cid})))
    monkeypatch.setattr("app.core.security.tenant_isolation.set_current_tenant_id", lambda _: None)
    response = await endpoint.oauth_callback(Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
                                            state="synthetic", code="synthetic", error=None, db_client=client)
    assert "status=success" in response.headers["location"]
    parent, accounts = await db.state(cid)
    assert parent["status"] == "active" and len(accounts) == 1
    assert accounts[0]["account_email"] == "fixture@example.invalid" and accounts[0]["external_account_id"] is None
    value = await resolver._refresh_and_store(client, remote, cid, str(accounts[0]["id"]), db.tenant, "ignored-stale-read")
    assert value == "rotated-access"
    remote.refresh_tokens.assert_awaited_once_with("rotated-refresh")
    _, stored = await db.state(cid)
    assert remote._authorization_snapshot == _authorization_snapshot_for_row(db.tenant, cid, "gmail", stored[0])


async def test_migration_downgrade_refuses_unresolved_refresh(connector_db, monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[2] / "alembic/versions/0071_connector_lifecycle.py"
    spec = importlib.util.spec_from_file_location("connector_lifecycle_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.downgrade()
    db = connector_db
    cid, aid = await db.new_connector()
    await db.owner.execute("""UPDATE connector_accounts SET refresh_state='in_flight',
        refresh_claim_id=$2,refresh_claimed_at=NOW() WHERE id=$1::uuid""", aid, uuid4())
    with pytest.raises(asyncpg.RaiseError, match="outcomes are unresolved"):
        async with db.owner.transaction():
            for sql in statements:
                await db.owner.execute(sql)
    assert await db.owner.fetchval("SELECT refresh_state FROM connector_accounts WHERE id=$1::uuid", aid) == "in_flight"


@pytest.mark.parametrize("old_status", ["expired", "error"])
async def test_reauthorization_retires_old_expired_or_error_grant(connector_db, old_status):
    db = connector_db
    old, _ = await db.new_connector(status=old_status)
    new, _ = await db.new_connector(status="pending")
    await db.service.activate(db.tenant, new, "gmail", "email", account_data(), {})
    remote = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError):
        await db.service.refresh(db.tenant, old, connector=remote, allow_expired=True)
    remote.refresh_tokens.assert_not_awaited()
    parent, accounts = await db.state(old)
    assert parent["status"] == accounts[0]["status"] == "revoked"
    assert accounts[0]["refresh_token_encrypted"] is None
    assert (await db.state(new))[0]["status"] == "active"


async def test_admin_list_uses_exact_global_counts_beyond_first_page(connector_db):
    from app.api.v1.endpoints.admin.connectors import list_admin_connectors
    db = connector_db
    args = dict(admin_user=NS(), db_client=db.client, tenant_id=None, status=None, type=None,
                provider=None, page=1, page_size=100)
    baseline = await list_admin_connectors(**args)
    await db.owner.executemany("""INSERT INTO connectors(id,tenant_id,type,provider,status)
        VALUES($1,$2,'email','gmail','pending')""", [(uuid4(), UUID(db.tenant)) for _ in range(105)])
    cid, aid = await db.new_connector()
    await db.owner.execute("UPDATE connector_accounts SET token_expires_at=NOW()-INTERVAL '1 minute' WHERE id=$1::uuid", aid)
    await db.new_connector(status="error")
    args["tenant_id"] = db.tenant
    result = await list_admin_connectors(**args)
    assert result.total == 107 and len(result.items) == 100
    assert result.summary.scope == "all_connectors"
    assert result.summary.total == baseline.summary.total + 107
    assert result.summary.active == baseline.summary.active + 1
    assert result.summary.expiring_or_expired == baseline.summary.expiring_or_expired + 1
    assert result.summary.error == baseline.summary.error + 1
    args.update(status="pending", page=2)
    page = await list_admin_connectors(**args)
    assert page.total == 105 and len(page.items) == 5
    assert page.summary == result.summary


async def test_ambiguous_current_account_is_not_reported_healthy_or_refreshed(connector_db):
    from app.api.v1.endpoints.admin.connectors import list_admin_connectors
    db = connector_db
    cid, aid = await db.new_connector()
    await db.owner.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,status,created_at,
        access_token_encrypted,refresh_token_encrypted,token_expires_at)
        SELECT $2,connector_id,tenant_id,status,created_at,'synthetic:other','synthetic:other',token_expires_at
        FROM connector_accounts WHERE id=$1::uuid""", aid, uuid4())
    result = await list_admin_connectors(admin_user=NS(),db_client=db.client,tenant_id=db.tenant,
        status=None,type=None,provider=None,page=1,page_size=100)
    assert result.items[0].token_status == "unknown" and result.items[0].account_email is None
    assert "ambiguous" in result.items[0].error_message
    remote = provider()
    with pytest.raises(lifecycle.ConnectorLifecycleError, match="ambiguous"):
        await db.service.refresh(db.tenant, cid, connector=remote)
    remote.refresh_tokens.assert_not_awaited()
