"""Native opt-out through actual migrated PostgreSQL; no provider/telephone I/O."""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.dialer import opt_out
from app.domain.services.dnc_service import DNCService
from app.realtime.openai import RealtimeEvent
from tests.unit.test_realtime_end_call_ownership import _events, _fixture

pytestmark = pytest.mark.integration
PHONE = "+15555550107"


@pytest_asyncio.fixture
async def dnc_db(monkeypatch):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Only a disposable localhost *_test database is supported")
    admin = await asyncpg.connect(dsn)
    pool = None
    role = "op07_dnc_" + uuid4().hex
    tenants, campaigns, leads, jobs = ([uuid4(), uuid4()] for _ in range(4))
    try:
        assert await admin.fetchval("SELECT to_regclass('public.dnc_entries') IS NOT NULL")
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON dnc_entries,leads,dialer_jobs TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON campaigns TO "{role}"')
        for tenant, campaign, lead, job in zip(tenants, campaigns, leads, jobs):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic OP07')", tenant)
            await admin.execute("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic OP07')", campaign, tenant)
            await admin.execute("INSERT INTO leads(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,$4,'pending')", lead, tenant, campaign, PHONE)
            await admin.execute("""INSERT INTO dialer_jobs(id,tenant_id,campaign_id,lead_id,phone_number,status)
                                   VALUES($1,$2,$3,$4,$5,'queued')""", job, tenant, campaign, lead, PHONE)

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert tuple(flags.values()) == (False, False)
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM leads)")
        voice = SimpleNamespace(_dialer_tenant_id=str(tenants[0]), _dialer_lead_id=str(leads[0]),
                                _dialer_phone=PHONE, _dialer_call_id=None)
        session = SimpleNamespace(call_id="synthetic-close")
        voice.call_session = session
        monkeypatch.setattr("app.domain.services.telephony.lifecycle._state",
                            lambda: SimpleNamespace(get_voice_session=lambda _: voice))
        monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
            is_initialized=True, db_pool=pool, db_client=None))
        yield SimpleNamespace(admin=admin, pool=pool, role=role, tenants=tenants, leads=leads,
                              jobs=jobs, voice=voice, session=session)
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM dnc_entries WHERE tenant_id=ANY($1::uuid[])", tenants)
        await admin.execute("DELETE FROM dialer_jobs WHERE id=ANY($1::uuid[])", jobs)
        await admin.execute("DELETE FROM leads WHERE id=ANY($1::uuid[])", leads)
        await admin.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaigns)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", tenants)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


@pytest.mark.parametrize("status", ["queued", "processing", "calling"])
async def test_native_continued_question_blocks_future_contact_and_keeps_live_owner(dnc_db, status):
    db = dnc_db
    await db.admin.execute("UPDATE dialer_jobs SET status=$2 WHERE id=$1", db.jobs[0], status)
    bridge, provider, _, ended, session = _fixture()
    session.call_id = "synthetic-close"
    db.voice.call_session = session
    try:
        await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", is_final=True,
            text="Do not call me again, but first I need help with my account."))
        assert bridge._opt_out_task is not None
        await bridge._opt_out_task
        assert db.voice._opt_out_purged is True
        assert await DNCService(db.pool).is_on_dnc(tenant_id=str(db.tenants[0]), e164=PHONE)
        assert not await DNCService(db.pool).is_on_dnc(tenant_id=str(db.tenants[1]), e164=PHONE)
        rows = await db.admin.fetch("SELECT id,status FROM dialer_jobs WHERE id=ANY($1::uuid[])", db.jobs)
        states = {row["id"]: row["status"] for row in rows}
        assert states[db.jobs[0]] == ("cancelled" if status == "queued" else status)
        assert states[db.jobs[1]] == "queued"
        assert await db.admin.fetchval("SELECT status FROM leads WHERE id=$1", db.leads[0]) == "dnc"
        assert await db.admin.fetchval("SELECT status FROM leads WHERE id=$1", db.leads[1]) == "pending"
        assert bridge._termination_task is None
        ended.assert_not_awaited()
    finally:
        await bridge.stop()


async def test_actual_cleanup_failure_retries_after_dnc_commit(dnc_db):
    db = dnc_db
    await db.admin.execute(f'REVOKE UPDATE ON leads,dialer_jobs FROM "{db.role}"')
    assert await opt_out.purge_opt_out_before_farewell(db.session) is True
    assert db.voice._opt_out_dnc_written and not getattr(db.voice, "_opt_out_purged", False)
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[0]) == "queued"
    await db.admin.execute(f'GRANT UPDATE ON leads,dialer_jobs TO "{db.role}"')
    assert await opt_out.purge_opt_out_before_farewell(db.session) is True
    assert db.voice._opt_out_purged
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[0]) == "cancelled"
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 1


async def test_foreign_lead_zero_row_never_acknowledges_full_cleanup(dnc_db):
    db = dnc_db
    db.voice._dialer_lead_id = str(db.leads[1])
    assert await opt_out.purge_opt_out_before_farewell(db.session) is True
    assert not getattr(db.voice, "_opt_out_purged", False)
    assert await db.admin.fetchval("SELECT status FROM leads WHERE id=$1", db.leads[1]) == "pending"
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[1]) == "queued"


async def test_concurrent_shared_attempts_are_idempotent_and_no_lead_is_supported(dnc_db):
    db = dnc_db
    db.voice._dialer_lead_id = None
    assert await asyncio.gather(*(opt_out.purge_opt_out_before_farewell(db.session) for _ in range(2))) == [True, True]
    assert db.voice._opt_out_purged
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 1
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[0]) == "queued"


async def test_database_timeout_does_not_manufacture_acknowledgement(dnc_db):
    db = dnc_db
    async with db.admin.transaction():
        # Block only this synthetic tenant's FK check, never a shared table.
        await db.admin.fetchrow("SELECT id FROM tenants WHERE id=$1 FOR UPDATE", db.tenants[0])
        assert not await opt_out.purge_opt_out_before_farewell(db.session, timeout_s=.05)
        assert not getattr(db.voice, "_opt_out_dnc_written", False)
        assert not getattr(db.voice, "_opt_out_purged", False)
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 0
    assert await opt_out.purge_opt_out_before_farewell(db.session)
    assert db.voice._opt_out_purged


async def test_permanent_opt_out_preserves_other_source_and_refreshes_old_expiry(dnc_db):
    db = dnc_db
    from datetime import datetime, timedelta, timezone
    expires = datetime.now(timezone.utc) + timedelta(days=3)
    service = DNCService(db.pool)
    manual = await service.add(tenant_id=str(db.tenants[0]), e164=PHONE,
                               source="operator_custom_reason", expires_at=expires)
    caller = await service.add_caller_opt_out(tenant_id=str(db.tenants[0]), e164=PHONE)
    # Historical expiring caller entry must be made permanent on replay.
    await db.admin.execute("UPDATE dnc_entries SET expires_at=$2 WHERE id=$1", caller.id, expires)
    again = await service.add_caller_opt_out(tenant_id=str(db.tenants[0]), e164=PHONE)
    assert again.id == caller.id and again.expires_at is None
    assert await db.admin.fetchval("SELECT expires_at FROM dnc_entries WHERE id=$1", manual.id) == expires
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 2
    rows = await db.admin.fetch("SELECT phone_number,normalized_number FROM dnc_entries WHERE tenant_id=$1", db.tenants[0])
    assert all(row["phone_number"] == row["normalized_number"] == PHONE for row in rows)


async def test_bulk_compatibility_writes_normalized_phone_and_preserves_source(dnc_db):
    db = dnc_db
    service = DNCService(db.pool)
    result = await service.bulk_import(tenant_id=str(db.tenants[0]),
        numbers=["+1 (555) 555-0107", PHONE], source="existing_custom_list")
    assert result["invalid_count"] == 0
    rows = await db.admin.fetch("SELECT phone_number,normalized_number,source FROM dnc_entries WHERE tenant_id=$1", db.tenants[0])
    assert len(rows) == 1
    assert tuple(rows[0].values()) == (PHONE, PHONE, "existing_custom_list")


async def test_global_suppression_remains_idempotent_and_visible_across_tenants(dnc_db):
    db = dnc_db
    service = DNCService(db.pool)
    source = "synthetic_global_" + uuid4().hex
    ids = []
    try:
        first = await service.add(tenant_id=None, e164="+15555550997", source=source)
        ids.append(first.id)
        second = await service.add(tenant_id=None, e164="+15555550997", source=source)
        ids.append(second.id)
        assert first.id == second.id
        for tenant in db.tenants:
            assert await service.is_on_dnc(tenant_id=str(tenant), e164="+15555550997")
    finally:
        # This test alone owns these exact synthetic global entries.
        if ids:
            await db.admin.execute("DELETE FROM dnc_entries WHERE id=ANY($1::uuid[])", ids)
