"""Real-database proof: taking the audit chain lock leaves the transaction's
lock limit exactly as the application set it (it used to fall back to the
session default, 0 = wait forever, while the chain lock was held).

Skipped unless TEST_DATABASE_URL points at a migrated scratch database
(``pg_local_up.sh`` prepares one). The account row is created and removed here.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import asyncpg
import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services import audit_logger
from app.domain.services.audit_logger import AuditLogger

DSN = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set (needs a migrated scratch Postgres)")

LIMIT_MS = 1500


@pytest.fixture
async def world(monkeypatch):
    monkeypatch.setenv("PG_LOCK_TIMEOUT_MS", str(LIMIT_MS))
    admin = await asyncpg.connect(DSN)
    tenant = None
    pool = None
    try:
        tenant = str(await admin.fetchval(
            "INSERT INTO tenants (business_name) VALUES ($1) RETURNING id",
            "chain-lock-pg-" + uuid.uuid4().hex[:8]))
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        yield pool, tenant
    finally:
        if pool is not None:
            await pool.close()
        if tenant:
            await admin.execute("DELETE FROM tenants WHERE id = $1::uuid", tenant)
        await admin.close()


@pytest.mark.asyncio
async def test_lock_limit_is_the_same_before_and_after(world):
    pool, tenant = world
    async with acquire_with_tenant(pool, tenant) as conn:
        before = await conn.fetchval("SHOW lock_timeout")
        await AuditLogger.lock_transaction(conn)
        after = await conn.fetchval("SHOW lock_timeout")
    print("lock_timeout before/after:", before, after)
    assert before != "0"
    assert after == before


@pytest.mark.asyncio
async def test_a_row_wait_after_the_chain_lock_still_ends_at_the_limit(world):
    pool, tenant = world
    holder = await asyncpg.connect(DSN)
    tx = holder.transaction()
    await tx.start()
    try:
        await holder.fetchval("SELECT id FROM tenants WHERE id = $1::uuid FOR UPDATE", tenant)
        started = None
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            async with acquire_with_tenant(pool, tenant) as conn:
                await AuditLogger.lock_transaction(conn)
                started = time.monotonic()
                await asyncio.wait_for(
                    conn.execute("UPDATE tenants SET business_name = business_name WHERE id = $1::uuid", tenant),
                    timeout=8)
        waited = time.monotonic() - started
        print("blocked update waited %.2fs (limit %d ms)" % (waited, LIMIT_MS))
        assert waited < 4
    finally:
        await tx.rollback()
        await holder.close()


@pytest.mark.asyncio
async def test_chain_lock_is_held_until_commit_then_released(world):
    pool, tenant = world
    async with acquire_with_tenant(pool, tenant) as first:
        await AuditLogger.lock_transaction(first)
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            async with acquire_with_tenant(pool, tenant) as second:
                await AuditLogger.lock_transaction(second)
    async with acquire_with_tenant(pool, tenant) as third:
        await AuditLogger.lock_transaction(third)


@pytest.mark.asyncio
async def test_busy_chain_lock_raises_after_its_own_short_wait(world):
    pool, tenant = world
    async with acquire_with_tenant(pool, tenant) as first:
        await AuditLogger.lock_transaction(first)
        started = time.monotonic()
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            async with acquire_with_tenant(pool, tenant) as second:
                await AuditLogger.lock_transaction(second)
        waited = time.monotonic() - started
    print("busy chain lock waited %.2fs" % waited)
    # The SQL carries the real 5000 ms as a literal, so it cannot be shrunk: the real wait is tested.
    limit = audit_logger._CHAIN_LOCK_TIMEOUT_MS / 1000
    assert limit - 0.3 <= waited < limit + 1.5, "the chain lock's own wait, not the 1500 ms limit"


@pytest.mark.asyncio
async def test_outside_a_transaction_still_raises_runtime_error(world):
    pool, _ = world
    async with pool.acquire() as conn:
        with pytest.raises(RuntimeError):
            await AuditLogger.lock_transaction(conn)


@pytest.mark.asyncio
async def test_flush_path_keeps_the_callers_lock_limit(world, monkeypatch):
    """Drive the real flush inside a transaction that set its own 777ms limit."""
    from contextlib import asynccontextmanager

    from app.domain.services.audit_logger import AuditEvent

    pool, _ = world
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SET LOCAL lock_timeout = '777ms'")

            @asynccontextmanager
            async def _same_conn(_pool, _tenant, **_kw):
                yield conn

            monkeypatch.setattr(audit_logger, "acquire_with_tenant", _same_conn)
            audit = AuditLogger(pool, signing_key="dummy", flush_interval_seconds=3600)
            try:
                await audit.log(AuditEvent.RECORD_VIEWED, action="lock-limit-probe")
                await audit.flush()
            finally:
                await audit.aclose()
            assert await conn.fetchval("SHOW lock_timeout") == "777ms"
        finally:
            await tx.rollback()
