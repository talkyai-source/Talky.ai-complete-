"""Actual migrated PostgreSQL; synthetic calls, no telephony/provider/Redis."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.security.tenant_isolation import clear_tenant_context, set_bypass_rls
from app.domain.models.dialer_job import CallOutcome
from app.domain.services.call_service import CallService
from app.domain.services.dialer.opt_out import purge_lead_on_opt_out
from app.domain.services.dnc_service import DNCService
from tests.integration.test_op07_native_dnc import dnc_db as _dnc_db, PHONE

pytestmark = pytest.mark.integration
dnc_db = _dnc_db


@pytest_asyncio.fixture
async def settlement(dnc_db):
    db = dnc_db
    call_id = uuid4()
    await db.admin.execute(f'GRANT SELECT,INSERT,UPDATE ON calls TO "{db.role}"')
    # Canonical calls UPDATE trigger invalidates linked prepared CRM work.
    await db.admin.execute(f'GRANT SELECT,INSERT,UPDATE ON crm_deliveries TO "{db.role}"')
    await db.admin.execute(f'GRANT SELECT ON connectors TO "{db.role}"')
    original_pool = db.pool

    async def init(conn):
        await conn.execute(f'SET ROLE "{db.role}"')
        # Match the canonical application pool's JSONB codec for its outbox.
        await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                                  schema="pg_catalog", format="text")

    db.pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=3, init=init)
    campaign = await db.admin.fetchval("SELECT campaign_id FROM leads WHERE id=$1", db.leads[0])
    await db.admin.execute("""INSERT INTO calls(id,tenant_id,campaign_id,lead_id,dialer_job_id,phone_number,status)
                             VALUES($1,$2,$3,$4,$5,$6,'in_progress')""",
                          call_id, db.tenants[0], campaign, db.leads[0], db.jobs[0], PHONE)
    await db.admin.execute("UPDATE dialer_jobs SET status='processing' WHERE id=$1", db.jobs[0])
    service = CallService(db_client=MagicMock(), queue_service=AsyncMock(), db_pool=db.pool)
    service._effective_attempt_number = AsyncMock(return_value=1)
    set_bypass_rls(True)
    try:
        yield SimpleNamespace(db=db, call_id=call_id, service=service)
    finally:
        clear_tenant_context()
        await db.pool.close()
        db.pool = original_pool
        await db.admin.execute("DELETE FROM calls WHERE id=$1", call_id)


async def settle(fixture, outcome=CallOutcome.FAILED):
    # Context belongs to the executing task, not pytest's fixture task.
    set_bypass_rls(True)
    try:
        return await fixture.service._handle_call_status_pooled(
            str(fixture.call_id), outcome, outcome.value, 42)
    finally:
        clear_tenant_context()


@pytest.mark.parametrize("outcome", [CallOutcome.ANSWERED, CallOutcome.GOAL_ACHIEVED, CallOutcome.FAILED])
async def test_live_opt_out_survives_final_settlement_without_changing_call_history(settlement, outcome):
    f, db = settlement, settlement.db
    result = await purge_lead_on_opt_out(db_pool=db.pool, db_client=None,
        tenant_id=str(db.tenants[0]), lead_id=str(db.leads[0]), phone_number=PHONE)
    assert result["purge_complete"]
    execution = await settle(f, outcome)
    assert execution.result.durable and execution.retry_args is None
    call = await db.admin.fetchrow("SELECT outcome,duration_seconds,terminal_retry_payload FROM calls WHERE id=$1", f.call_id)
    assert tuple(call.values()) == (outcome.value, 42, None)
    lead = await db.admin.fetchrow("SELECT status,last_call_result,call_attempts,do_not_call FROM leads WHERE id=$1", db.leads[0])
    assert tuple(lead.values()) == ("dnc", "caller_opt_out", 1, False)
    job = await db.admin.fetchrow("SELECT status,last_outcome,failure_reason,attempt_number FROM dialer_jobs WHERE id=$1", db.jobs[0])
    assert tuple(job.values()) == ("non_retryable", outcome.value, "caller_opt_out", 1)
    assert await DNCService(db.pool).is_on_dnc(tenant_id=str(db.tenants[0]), e164=PHONE)
    await settle(f, outcome)
    assert await db.admin.fetchval("SELECT call_attempts FROM leads WHERE id=$1", db.leads[0]) == 1


@pytest.mark.parametrize("kind", ["foreign", "expired", "other_source", "removed", "new_lead_phone"])
async def test_only_active_original_destination_caller_opt_out_suppresses_retry(settlement, kind):
    f, db = settlement, settlement.db
    tenant = str(db.tenants[1] if kind == "foreign" else db.tenants[0])
    number = "+15555550108" if kind == "new_lead_phone" else PHONE
    source = "manual_admin" if kind == "other_source" else "caller_opt_out"
    row = await DNCService(db.pool).add(tenant_id=tenant, e164=number, source=source)
    if kind == "expired":
        await db.admin.execute("UPDATE dnc_entries SET expires_at=NOW()-interval '1 day' WHERE id=$1", row.id)
    if kind == "removed":
        await db.admin.execute("DELETE FROM dnc_entries WHERE id=$1", row.id)
    if kind == "new_lead_phone":
        await db.admin.execute("UPDATE leads SET phone_number=$2 WHERE id=$1", db.leads[0], number)
    execution = await settle(f)
    assert execution.retry_args is not None
    assert await db.admin.fetchval("SELECT status FROM leads WHERE id=$1", db.leads[0]) == "called"


async def test_changed_lead_phone_does_not_erase_original_call_suppression(settlement):
    f, db = settlement, settlement.db
    await DNCService(db.pool).add_caller_opt_out(tenant_id=str(db.tenants[0]), e164=PHONE)
    await db.admin.execute("UPDATE leads SET phone_number='+15555550108' WHERE id=$1", db.leads[0])
    assert (await settle(f)).retry_args is None


async def test_global_opt_out_uses_existing_global_matching_policy(settlement):
    f, db = settlement, settlement.db
    row = await DNCService(db.pool).add_caller_opt_out(tenant_id=None, e164=PHONE)
    try:
        assert (await settle(f)).retry_args is None
    finally:
        await db.admin.execute("DELETE FROM dnc_entries WHERE id=$1", row.id)


async def test_pending_retry_outbox_is_cancelled_after_later_durable_opt_out(settlement):
    f, db = settlement, settlement.db
    first = await settle(f)
    assert first.retry_args is not None
    await purge_lead_on_opt_out(db_pool=db.pool, db_client=None,
        tenant_id=str(db.tenants[0]), lead_id=str(db.leads[0]), phone_number=PHONE)
    recovered = await settle(f)
    assert recovered.result.durable and recovered.retry_args is None
    assert await db.admin.fetchval("SELECT terminal_retry_payload FROM calls WHERE id=$1", f.call_id) is None
    assert await db.admin.fetchval("SELECT outcome FROM calls WHERE id=$1", f.call_id) == "failed"
    assert await db.admin.fetchval("SELECT call_attempts FROM leads WHERE id=$1", db.leads[0]) == 1
    assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[0]) == "cancelled"


async def test_lookup_storage_failure_rolls_back_entire_settlement(settlement):
    f, db = settlement, settlement.db
    await db.admin.execute(f'REVOKE SELECT ON dnc_entries FROM "{db.role}"')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await settle(f)
    assert await db.admin.fetchval("SELECT status FROM calls WHERE id=$1", f.call_id) == "in_progress"
    assert await db.admin.fetchval("SELECT terminal_retry_payload FROM calls WHERE id=$1", f.call_id) is None
    assert await db.admin.fetchval("SELECT call_attempts FROM leads WHERE id=$1", db.leads[0]) == 0


async def test_matching_suppression_delete_serializes_after_settlement_read(settlement, monkeypatch):
    f, db = settlement, settlement.db
    row = await DNCService(db.pool).add_caller_opt_out(tenant_id=str(db.tenants[0]), e164=PHONE)
    entered, release = asyncio.Event(), asyncio.Event()
    original = f.service._update_lead_status_pooled

    async def paused(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(f.service, "_update_lead_status_pooled", paused)
    task = asyncio.create_task(settle(f))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # Await an acknowledged server lock timeout/rollback. Cancelling the
        # client await alone would leave the DELETE outcome ambiguous.
        with pytest.raises(asyncpg.LockNotAvailableError):
            async with db.admin.transaction():
                await db.admin.execute("SET LOCAL lock_timeout='100ms'")
                await db.admin.execute("DELETE FROM dnc_entries WHERE id=$1", row.id)
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert await db.admin.execute("DELETE FROM dnc_entries WHERE id=$1", row.id) == "DELETE 1"


async def test_opt_out_committed_while_settlement_waits_for_lead_is_not_overwritten(settlement):
    f, db = settlement, settlement.db
    lead_read = asyncio.Event()

    class LeadBarrier:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def fetchval(self, sql, *args):
            if "FROM leads" in sql:
                lead_read.set()
            return await self.conn.fetchval(sql, *args)

    class WrappedPool:
        @asynccontextmanager
        async def acquire(self, **kwargs):
            async with db.pool.acquire(**kwargs) as conn:
                yield LeadBarrier(conn)

    f.service._db_pool = WrappedPool()
    task = wait_read = None
    try:
        async with db.admin.transaction():
            await db.admin.fetchval("SELECT id FROM leads WHERE id=$1 FOR UPDATE", db.leads[0])
            task = asyncio.create_task(settle(f))
            wait_read = asyncio.create_task(lead_read.wait())
            done, _ = await asyncio.wait({task, wait_read}, timeout=3,
                                         return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task  # Surface a fixture/query error instead of a false race timeout.
            assert wait_read in done, "Settlement did not reach the lead lock"
            await db.admin.execute("""INSERT INTO dnc_entries(tenant_id,phone_number,normalized_number,source)
                                     VALUES($1,$2,$2,'caller_opt_out')""", db.tenants[0], PHONE)
            await db.admin.execute("UPDATE leads SET status='dnc',last_call_result='caller_opt_out' WHERE id=$1", db.leads[0])
        execution = await asyncio.wait_for(task, 2)
        assert execution.retry_args is None
        assert await db.admin.fetchval("SELECT status FROM leads WHERE id=$1", db.leads[0]) == "dnc"
    finally:
        if task and not task.done():
            task.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if wait_read:
            wait_read.cancel()
            await asyncio.gather(wait_read, return_exceptions=True)


@pytest.mark.parametrize("source,accepted", [("caller_opt_out", 1), ("bulk_import", 0)])
async def test_bulk_expired_duplicate_acknowledgement_matches_actual_suppression(dnc_db, source, accepted):
    db = dnc_db
    service = DNCService(db.pool)
    row = await service.add(tenant_id=str(db.tenants[0]), e164=PHONE, source=source)
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    await db.admin.execute("UPDATE dnc_entries SET expires_at=$2 WHERE id=$1", row.id, expired)
    result = await service.bulk_import(tenant_id=str(db.tenants[0]), numbers=[PHONE], source=source)
    assert result["accepted_count"] == accepted
    assert result["skipped_count"] == 1 - accepted
    assert await service.is_on_dnc(tenant_id=str(db.tenants[0]), e164=PHONE) is bool(accepted)
    assert await db.admin.fetchval("SELECT expires_at FROM dnc_entries WHERE id=$1", row.id) == (None if accepted else expired)


async def test_concurrent_bulk_duplicates_and_foreign_scope(dnc_db):
    db = dnc_db
    service = DNCService(db.pool)
    foreign = await service.add(tenant_id=str(db.tenants[1]), e164=PHONE, source="caller_opt_out")
    await db.admin.execute("UPDATE dnc_entries SET expires_at=NOW()-interval '1 day' WHERE id=$1", foreign.id)
    results = await asyncio.gather(*(service.bulk_import(tenant_id=str(db.tenants[0]), numbers=[PHONE], source="caller_opt_out") for _ in range(2)))
    assert [result["accepted_count"] for result in results] == [1, 1]
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 1
    assert not await service.is_on_dnc(tenant_id=str(db.tenants[1]), e164=PHONE)


async def test_bulk_row_database_failure_does_not_poison_next_row(dnc_db):
    db = dnc_db
    bad = "+15555550109"

    class FailingRow:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def fetchrow(self, sql, *args):
            if "INSERT INTO dnc_entries" in sql and args[1] == bad:
                await self.conn.execute("SELECT 1/0")
            return await self.conn.fetchrow(sql, *args)

    class WrappedPool:
        @asynccontextmanager
        async def acquire(self):
            async with db.pool.acquire() as conn:
                yield FailingRow(conn)

    result = await DNCService(WrappedPool()).bulk_import(tenant_id=str(db.tenants[0]),
        numbers=[bad, PHONE, "invalid"], source="bulk_import")
    assert (result["accepted_count"], result["skipped_count"], result["invalid_count"]) == (1, 1, 1)
    assert await DNCService(db.pool).is_on_dnc(tenant_id=str(db.tenants[0]), e164=PHONE)
    assert not await DNCService(db.pool).is_on_dnc(tenant_id=str(db.tenants[0]), e164=bad)


async def test_bulk_permission_failure_is_not_masked_as_a_skipped_input(dnc_db):
    db = dnc_db
    await db.admin.execute(f'REVOKE INSERT ON dnc_entries FROM "{db.role}"')
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await DNCService(db.pool).bulk_import(tenant_id=str(db.tenants[0]),
            numbers=[PHONE], source="bulk_import")
    assert await db.admin.fetchval("SELECT count(*) FROM dnc_entries WHERE tenant_id=$1", db.tenants[0]) == 0


@pytest.mark.parametrize("live_status", ["processing", "calling"])
async def test_old_unacknowledged_outbox_keeps_newer_live_attempt_owned(settlement, live_status):
    f, db = settlement, settlement.db
    assert (await settle(f)).retry_args is not None
    newer_call = uuid4()
    await db.admin.execute("UPDATE dialer_jobs SET status=$2,attempt_number=2 WHERE id=$1", db.jobs[0], live_status)
    campaign = await db.admin.fetchval("SELECT campaign_id FROM calls WHERE id=$1", f.call_id)
    await db.admin.execute("""INSERT INTO calls(id,tenant_id,lead_id,dialer_job_id,phone_number,campaign_id,status)
                             VALUES($1,$2,$3,$4,$5,$6,'in_progress')""",
                          newer_call, db.tenants[0], db.leads[0], db.jobs[0], PHONE, campaign)
    try:
        await DNCService(db.pool).add_caller_opt_out(tenant_id=str(db.tenants[0]), e164=PHONE)
        assert (await settle(f)).retry_args is None
        assert await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1", db.jobs[0]) == live_status
        assert await db.admin.fetchval("SELECT status FROM calls WHERE id=$1", newer_call) == "in_progress"
    finally:
        await db.admin.execute("DELETE FROM calls WHERE id=$1", newer_call)
