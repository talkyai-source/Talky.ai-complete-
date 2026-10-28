"""AD03 audit atomicity and RLS chain evidence on isolated PostgreSQL only."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.test_connector_lifecycle import connector_db, provider, tokens, account_data
from app.services import connector_lifecycle as lifecycle
from app.domain.services.audit_logger import AuditEvent
from app.core.db_utils import acquire_with_tenant
from app.services.connector_resolver import _authorization_snapshot_for_row
from app.infrastructure.assistant.tools.inbox import _mark_email_authorization_expired

pytestmark = pytest.mark.integration


async def receipts(db, cid):
    return await db.owner.fetch(
        "SELECT * FROM audit_logs WHERE tenant_id=$1::uuid AND resource_id=$2::uuid ORDER BY event_time",
        db.tenant, cid)


def facts(row):
    value = row["metadata"]
    return json.loads(value) if isinstance(value, str) else value


async def test_refresh_receipts_bind_actor_account_generation_without_secret_material(connector_db):
    db = connector_db
    cid, aid = await db.new_connector()
    service = lifecycle.ConnectorLifecycle(db.client, actor=lifecycle.ConnectorActor.human(
        db.actor, "admin_reconnect", request=db.actor_request, platform_admin=True))
    remote = provider()
    async def dispatched(_):
        rows = await receipts(db, cid)
        assert [row["action"] for row in rows] == ["connector.refresh_intent"]
        assert rows[0]["actor_id"] == UUID(db.actor.id)
        return tokens()
    remote.refresh_tokens.side_effect = dispatched
    _, saved = await service.refresh(db.tenant, cid, connector=remote)
    rows = await receipts(db, cid)
    assert [row["action"] for row in rows] == ["connector.refresh_intent", "connector.refresh_installed"]
    assert all(facts(row)["account_id"] == aid and str(row["actor_id"]) == db.actor.id for row in rows)
    assert {facts(row)["operation_id"] for row in rows} == {saved["operation_id"]}
    assert str(rows[-1]["event_id"]) == saved["audit_event_id"]
    assert facts(rows[-1])["generation"] == 1
    stored = json.dumps([dict(row) for row in rows], default=str)
    assert not any(secret in stored for secret in ("old-access", "old-refresh", "rotated-access", "rotated-refresh", "token_encrypted"))


@pytest.mark.parametrize("phase", ["refresh_intent", "refresh_installed"])
async def test_refresh_audit_failure_never_replays_provider_effect(connector_db, phase):
    db = connector_db
    cid, _ = await db.new_connector()
    await db.trigger_failure("audit_logs", cid, f"NEW.action='connector.{phase}'")
    remote = provider()
    with pytest.raises(asyncpg.RaiseError):
        await db.service.refresh(db.tenant, cid, connector=remote)
    parent, accounts = await db.state(cid)
    assert parent["status"] == "active" and accounts[0]["credential_generation"] == 0
    assert accounts[0]["access_token_encrypted"] == "synthetic:old-access"
    if phase == "refresh_intent":
        remote.refresh_tokens.assert_not_awaited()
        assert accounts[0]["refresh_state"] == "idle" and not await receipts(db, cid)
    else:
        remote.refresh_tokens.assert_awaited_once()
        assert accounts[0]["refresh_state"] == "uncertain"
        with pytest.raises(lifecycle.ConnectorLifecycleError):
            await db.service.refresh(db.tenant, cid, connector=remote)
        remote.refresh_tokens.assert_awaited_once()
        assert [row["action"] for row in await receipts(db, cid)] == [
            "connector.refresh_intent", "connector.refresh_uncertain"]


@pytest.mark.parametrize("phase", ["disabled_locally", "provider_revocation_result"])
async def test_disable_audit_failure_preserves_the_actual_committed_outcome(connector_db, phase):
    db = connector_db
    cid, _ = await db.new_connector()
    await db.trigger_failure("audit_logs", cid, f"NEW.action='connector.{phase}'")
    remote = AsyncMock(return_value=True)
    if phase == "disabled_locally":
        with pytest.raises(asyncpg.RaiseError):
            await db.service.disable(db.tenant, cid, revoke_token=remote)
        parent, accounts = await db.state(cid)
        assert parent["status"] == accounts[0]["status"] == "active"
        assert not await receipts(db, cid)
        remote.assert_not_awaited()
    else:
        result = await db.service.disable(db.tenant, cid, revoke_token=remote)
        assert not result.provider_revoked and not result.provider_result_recorded
        assert result.receipt()["local_disabled"] is True and result.audit_event_id
        parent, accounts = await db.state(cid)
        assert parent["status"] == "disconnected" and parent["provider_revocation_status"] == "pending"
        assert accounts[0]["access_token_encrypted"] is None
        rows = await receipts(db, cid)
        assert [row["action"] for row in rows] == ["connector.disabled_locally"]
        assert str(rows[0]["event_id"]) == result.audit_event_id
        replay = await db.service.disable(db.tenant, cid, revoke_token=remote)
        assert replay.audit_event_id == result.audit_event_id and not replay.provider_revoked
        remote.assert_awaited_once()


async def test_actor_losing_authority_while_waiting_cannot_dispatch(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    remote = provider()
    service = lifecycle.ConnectorLifecycle(db.client, actor=lifecycle.ConnectorActor.human(
        db.actor, "admin_reconnect", request=db.actor_request, platform_admin=True))
    task = None
    async with acquire_with_tenant(db.pool, None) as conn:
        await db.audit.lock_transaction(conn)
        task = asyncio.create_task(service.refresh(db.tenant, cid, connector=remote))
        await asyncio.sleep(.03)
        assert not task.done()
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1::uuid", db.actor.id)
    with pytest.raises(lifecycle.ConnectorLifecycleError) as error:
        await asyncio.wait_for(task, 3)
    assert error.value.status_code == 403
    remote.refresh_tokens.assert_not_awaited()
    assert not await receipts(db, cid)


async def test_oauth_records_trusted_initiator_separately_from_executing_service(connector_db):
    db = connector_db
    cid, _ = await db.new_connector(status="pending")
    service = lifecycle.ConnectorLifecycle(db.client, actor=lifecycle.ConnectorActor.oauth(db.actor.id))
    await service.begin_oauth(db.tenant, cid)
    saved = await service.activate(db.tenant, cid, "gmail", "email", account_data(), {})
    rows = await receipts(db, cid)
    assert [row["action"] for row in rows] == ["connector.oauth_exchange_intent", "connector.oauth_activated"]
    assert all(row["actor_type"] == "system" and row["actor_id"] is None for row in rows)
    assert all(facts(row)["initiator_id"] == db.actor.id for row in rows)
    assert {facts(row)["operation_id"] for row in rows} == {saved["operation_id"]}


async def test_config_and_activation_fail_atomically_when_audit_is_unavailable(connector_db):
    db = connector_db
    active, _ = await db.new_connector(provider="salesforce", config={"log_calls": True})
    pending, _ = await db.new_connector(status="pending", provider="salesforce")
    await db.trigger_failure("audit_logs", active, "TRUE")
    with pytest.raises(asyncpg.RaiseError):
        await db.service.update_config(db.tenant, active, {"log_calls": False}, provider="salesforce")
    assert json.loads((await db.state(active))[0]["config"]) == {"log_calls": True}
    await db.trigger_failure("audit_logs", pending, "TRUE")
    with pytest.raises(asyncpg.RaiseError):
        await db.service.activate(db.tenant, pending, "salesforce", "crm", account_data(), {})
    assert (await db.state(active))[0]["status"] == "active"
    assert (await db.state(pending))[0]["status"] == "pending"


async def test_tenant_rls_keeps_one_global_hash_chain_and_restores_context(connector_db):
    db = connector_db
    forced = await db.owner.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE oid='audit_logs'::regclass")
    await db.owner.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    ids = []
    try:
        for scope, event_tenant in [(db.tenant, db.tenant), (db.other, db.other), (None, db.tenant)]:
            async with acquire_with_tenant(db.pool, scope) as conn:
                before = await conn.fetchrow("SELECT current_setting('app.bypass_rls',true) AS bypass,current_setting('app.current_tenant_id',true) AS tenant")
                flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                assert tuple(flags.values()) == (False, False)
                await db.audit.lock_transaction(conn)
                ids.append(await db.audit.log_in_transaction(conn, event_type=AuditEvent.CONFIG_CHANGED,
                    action="ad03.chain", actor_type="system", tenant_id=event_tenant))
                after = await conn.fetchrow("SELECT current_setting('app.bypass_rls',true) AS bypass,current_setting('app.current_tenant_id',true) AS tenant")
                assert (before["bypass"] or "", before["tenant"]) == (after["bypass"] or "", after["tenant"])
                if scope == db.other:
                    assert await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1::uuid", db.tenant) == 0
        rows = await db.owner.fetch("SELECT * FROM audit_logs WHERE event_id=ANY($1::uuid[]) ORDER BY event_time", ids)
        for previous, current in zip(rows, rows[1:]):
            assert current["previous_hash"] == previous["entry_hash"]
        for row in rows:
            assert db.audit._recompute_row_hash(row) == row["entry_hash"]
            assert db.audit._sign_entry(dict(row)) == row["signature"]
    finally:
        if not forced:
            await db.owner.execute("ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY")


async def test_actual_admin_connector_and_background_writers_share_signature_key(connector_db, monkeypatch):
    from app.api.v1 import dependencies
    from app.domain.services.audit_logger import AuditLogger
    db = connector_db
    monkeypatch.setattr(dependencies, "_audit_logger_service", None)
    admin = await dependencies.get_audit_logger(db.pool)
    background = AuditLogger(db.pool)
    ids = []
    for writer in (db.service.audit, admin, background):
        async with acquire_with_tenant(db.pool, db.tenant) as conn:
            await writer.lock_transaction(conn)
            ids.append(await writer.log_in_transaction(conn, event_type=AuditEvent.CONFIG_CHANGED,
                action="ad03.shared_signing", actor_type="system", tenant_id=db.tenant))
    rows = await db.owner.fetch("SELECT * FROM audit_logs WHERE event_id=ANY($1::uuid[]) ORDER BY event_time", ids)
    assert len(rows) == 3
    assert all(background._sign_entry(dict(row)) == row["signature"] for row in rows)
    for previous, current in zip(rows, rows[1:]):
        assert current["previous_hash"] == previous["entry_hash"]


async def test_failed_global_tail_read_restores_rls_and_rolls_back_business_write(connector_db):
    db = connector_db
    cid, _ = await db.new_connector()
    class TailFault:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, name): return getattr(self.conn, name)
        async def fetchrow(self, query, *args):
            if "SELECT entry_hash,event_time FROM audit_logs" in query:
                await self.conn.execute("SELECT 1/0")
            return await self.conn.fetchrow(query, *args)
    with pytest.raises(asyncpg.DivisionByZeroError):
        async with acquire_with_tenant(db.pool, db.tenant) as conn:
            await db.audit.lock_transaction(conn)
            await conn.execute("UPDATE connectors SET status='error' WHERE id=$1::uuid", cid)
            try:
                await db.audit.log_in_transaction(TailFault(conn), event_type=AuditEvent.CONFIG_CHANGED,
                    action="ad03.tail_failure", tenant_id=db.tenant)
            except asyncpg.DivisionByZeroError:
                assert (await conn.fetchval("SELECT current_setting('app.bypass_rls',true)") or "") in ("", "off")
                assert await conn.fetchval("SELECT current_setting('app.current_tenant_id',true)") == db.tenant
                raise
    assert (await db.state(cid))[0]["status"] == "active"


@pytest.mark.parametrize("audit_fails", [False, True])
async def test_inbox_expiry_and_receipt_commit_together(connector_db, audit_fails):
    db = connector_db
    cid, aid = await db.new_connector()
    _, accounts = await db.state(cid)
    snapshot = _authorization_snapshot_for_row(db.tenant, cid, "gmail", accounts[0])
    if audit_fails:
        await db.trigger_failure("audit_logs", cid, "TRUE")
    acknowledged = await _mark_email_authorization_expired(db.client, db.tenant, cid, authorization=snapshot)
    assert acknowledged is (not audit_fails)
    parent, accounts = await db.state(cid)
    assert parent["status"] == accounts[0]["status"] == ("active" if audit_fails else "expired")
    rows = await receipts(db, cid)
    if audit_fails:
        assert not rows and accounts[0]["credential_generation"] == 0
    else:
        assert len(rows) == 1 and rows[0]["actor_type"] == "system"
        assert facts(rows[0])["source"] == "inbox_authorization_expiry"
        assert facts(rows[0])["account_id"] == aid and facts(rows[0])["generation"] == 1

