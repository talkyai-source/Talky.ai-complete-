"""Real-database proof (QA F12-10): a caller's opt-out and the spam web hook's
mark each leave ONE audit row, written AFTER the mark in its own transaction,
so a busy audit chain lock can never delay or lose the mark.

Skipped unless TEST_DATABASE_URL points at a migrated scratch database
(``pg_local_up.sh`` prepares one). Accounts, role, pools, do-not-call rows and
audit rows are created inside the ``try`` and removed in its ``finally``. The
test role of the CI-shaped database is a superuser with BYPASSRLS, so row
security is exercised through a pool whose connections ``SET ROLE`` to a
throw-away role that has neither (random name, so a killed run cannot collide).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from types import SimpleNamespace

import asyncpg
import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services import audit_logger
from app.domain.services import dnc_system_mark_audit as sma
from app.domain.services.call_service import CallService
from app.domain.services.dialer import opt_out

DSN = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set (needs a migrated scratch Postgres)")

NUMBER = "+442079460123"
SPAM_NUMBER = "+442079460124"


@pytest.fixture
async def world():
    admin = await asyncpg.connect(DSN)
    tenants: list[str] = []
    suffix = uuid.uuid4().hex[:8]
    role = "dnc_audit_pg_" + suffix
    norights = "dnc_audit_nr_" + suffix
    plain = restricted = no_audit = None

    async def as_app(conn):
        await conn.execute(f"SET ROLE {role}")

    async def as_norights(conn):
        await conn.execute(f"SET ROLE {norights}")

    try:
        for _ in range(2):
            tenants.append(str(await admin.fetchval(
                "INSERT INTO tenants (business_name) VALUES ($1) RETURNING id",
                "dnc-audit-pg-" + uuid.uuid4().hex[:8])))
        await admin.execute(
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS; "
            f"GRANT USAGE ON SCHEMA public TO {role}; "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON dnc_entries, audit_logs TO {role}; "
            f"GRANT SELECT ON tenants TO {role}; "
            f"CREATE ROLE {norights} NOLOGIN NOSUPERUSER NOBYPASSRLS; "
            f"GRANT USAGE ON SCHEMA public TO {norights}; "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON dnc_entries TO {norights}; "
            f"GRANT SELECT ON tenants TO {norights};")
        plain = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        restricted = await asyncpg.create_pool(DSN, min_size=1, max_size=4, setup=as_app)
        no_audit = await asyncpg.create_pool(DSN, min_size=1, max_size=2, setup=as_norights)
        yield SimpleNamespace(a=tenants[0], b=tenants[1], plain=plain,
                              restricted=restricted, no_audit=no_audit, admin=admin)
    finally:
        for pool in (plain, restricted, no_audit):
            if pool is not None:
                await pool.close()
        if tenants:
            await admin.execute("DELETE FROM audit_logs WHERE tenant_id = ANY($1::uuid[])", tenants)
            await admin.execute("DELETE FROM dnc_entries WHERE tenant_id = ANY($1::uuid[])", tenants)
            await admin.execute("DELETE FROM tenants WHERE id = ANY($1::uuid[])", tenants)
        for name in (role, norights):
            await admin.execute(f"DROP OWNED BY {name}")
            await admin.execute(f"DROP ROLE IF EXISTS {name}")
        await admin.close()


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(sma, "RETRY_WAITS_S", (0.3, 0.5, 1.0, 2.0))
    monkeypatch.setattr(sma, "CHAIN_WAIT_MS", 300)


async def _rows(pool, tenant):
    async with acquire_with_tenant(pool, tenant) as conn:
        dnc = await conn.fetch(
            "SELECT id, tenant_id, normalized_number, source FROM dnc_entries "
            "WHERE tenant_id = $1::uuid ORDER BY created_at", tenant)
        audit = await conn.fetch(
            "SELECT resource_id, tenant_id, actor_type, actor_role, actor_id, action, resource_type "
            "FROM audit_logs WHERE tenant_id = $1::uuid ORDER BY event_time", tenant)
    return dnc, audit


def _mine():
    loop = asyncio.get_running_loop()
    return [t for t in sma._pending if t.get_loop() is loop]


async def _drain():
    # Only tasks of THIS loop: another test's task may sit on a closed loop.
    loop = asyncio.get_running_loop()
    while True:
        mine = _mine()
        if not mine:
            return
        await asyncio.gather(*mine)


async def _caller(pool, tenant, number=NUMBER):
    return await opt_out.purge_lead_on_opt_out(
        db_pool=pool, db_client=None, tenant_id=tenant, lead_id=None,
        phone_number=number, call_id="call-1")


async def _hook(pool, tenant, number=SPAM_NUMBER):
    await CallService(db_client=SimpleNamespace(pool=pool))._add_spam_mark_to_dnc_list(
        tenant, number, "spam")


def _check(dnc, audit, role, count=1):
    assert len(dnc) == 1 and len(audit) == count
    for row in audit:
        assert row["action"] == "dnc.entry.add" and row["resource_type"] == "dnc_entry"
        assert row["resource_id"] == dnc[0]["id"] and row["tenant_id"] == dnc[0]["tenant_id"]
        assert row["actor_type"] == "system" and row["actor_role"] == role
        assert row["actor_id"] is None


@pytest.mark.asyncio
async def test_callers_opt_out_marks_at_once_then_one_audit_row(world):
    result = await _caller(world.plain, world.a)
    assert result["dnc_added"] is True
    dnc, _ = await _rows(world.plain, world.a)
    assert len(dnc) == 1 and dnc[0]["source"] == "caller_opt_out"
    await _drain()
    dnc, audit = await _rows(world.plain, world.a)
    _check(dnc, audit, "caller_opt_out")


@pytest.mark.asyncio
async def test_spam_hook_marks_then_one_audit_row(world):
    await _hook(world.plain, world.a)
    dnc, _ = await _rows(world.plain, world.a)
    assert len(dnc) == 1 and dnc[0]["source"] == "manual_admin"
    await _drain()
    dnc, audit = await _rows(world.plain, world.a)
    _check(dnc, audit, "webhook")


async def _hold_chain_lock(admin_dsn):
    conn = await asyncpg.connect(admin_dsn)
    tx = conn.transaction()
    await tx.start()
    await conn.execute("SELECT pg_advisory_xact_lock($1)", audit_logger._CHAIN_LOCK_KEY)
    return conn, tx


@pytest.mark.asyncio
async def test_a_busy_audit_lock_never_delays_or_loses_the_mark(world):
    conn, tx = await _hold_chain_lock(DSN)
    try:
        started = time.monotonic()
        result = await asyncio.wait_for(_caller(world.plain, world.a), 5)
        caller_s = time.monotonic() - started
        started = time.monotonic()
        await asyncio.wait_for(_hook(world.plain, world.b), 5)
        hook_s = time.monotonic() - started
        print(f"\nmark returned while lock held: caller={caller_s:.3f}s hook={hook_s:.3f}s")
        assert result["dnc_added"] is True and caller_s < 3.0 and hook_s < 3.0
        for tenant in (world.a, world.b):
            dnc, audit = await _rows(world.plain, tenant)
            assert len(dnc) == 1 and audit == []     # mark present, audit not yet
        await asyncio.sleep(0.6)                      # the audit tasks are waiting on the lock
    finally:
        await tx.commit()                             # release the lock
        await conn.close()
    await asyncio.wait_for(_drain(), 10)
    dnc_a, audit_a = await _rows(world.plain, world.a)
    dnc_b, audit_b = await _rows(world.plain, world.b)
    _check(dnc_a, audit_a, "caller_opt_out")
    _check(dnc_b, audit_b, "webhook")


@pytest.mark.asyncio
async def test_lock_held_through_every_try_leaves_the_mark_and_one_error(world, monkeypatch, caplog):
    monkeypatch.setattr(sma, "RETRY_WAITS_S", (0.05, 0.05, 0.05, 0.05))
    monkeypatch.setattr(sma, "CHAIN_WAIT_MS", 100)
    conn, tx = await _hold_chain_lock(DSN)
    try:
        with caplog.at_level(logging.ERROR):
            result = await _caller(world.plain, world.a)
            await asyncio.wait_for(_drain(), 10)
    finally:
        await tx.commit()
        await conn.close()
    assert result["dnc_added"] is True
    dnc, audit = await _rows(world.plain, world.a)
    assert len(dnc) == 1 and audit == []
    missing = [r for r in caplog.records if "dnc_mark_audit_missing" in r.getMessage()]
    assert len(missing) == 1
    text = missing[0].getMessage()
    assert "attempts=5" in text and "ChainBusy" in text and "442079460123" not in text


@pytest.mark.asyncio
async def test_second_mark_keeps_one_row_and_adds_a_second_audit_row(world):
    await _caller(world.plain, world.a)
    await _drain()
    await _caller(world.plain, world.a)
    await _drain()
    dnc, audit = await _rows(world.plain, world.a)
    _check(dnc, audit, "caller_opt_out", count=2)


@pytest.mark.asyncio
async def test_isolation_under_a_role_without_bypass(world):
    async with world.restricted.acquire() as conn:
        flags = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    assert not flags["rolsuper"] and not flags["rolbypassrls"]
    await _caller(world.restricted, world.a)
    await _drain()
    dnc, audit = await _rows(world.restricted, world.a)
    _check(dnc, audit, "caller_opt_out")
    async with acquire_with_tenant(world.restricted, world.b) as conn:   # no tenant predicate
        assert await conn.fetch("SELECT event_id FROM audit_logs") == []
        assert await conn.fetch("SELECT id FROM dnc_entries") == []
    await _caller(world.restricted, world.b)
    await _drain()
    dnc_b, audit_b = await _rows(world.restricted, world.b)
    dnc_a, audit_a = await _rows(world.restricted, world.a)
    assert len(dnc_a) == len(audit_a) == len(dnc_b) == len(audit_b) == 1
    assert audit_b[0]["resource_id"] == dnc_b[0]["id"] != dnc_a[0]["id"]
    async with acquire_with_tenant(world.restricted, world.b) as conn:
        assert await conn.fetch(
            "SELECT event_id FROM audit_logs WHERE tenant_id = $1::uuid", world.a) == []


@pytest.mark.asyncio
async def test_a_waiting_audit_task_holds_a_pool_connection_only_for_the_short_wait(world):
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
    conn, tx = await _hold_chain_lock(DSN)
    try:
        result = await _caller(pool, world.a)
        assert result["dnc_added"] is True
        await asyncio.sleep(0.1)                      # the audit task is trying or between tries
        started = time.monotonic()
        async with asyncio.timeout(5.0):              # generous bound; the real bound is CHAIN_WAIT_MS
            async with pool.acquire() as other:
                await other.fetchval("SELECT 1")
        acquire_s = time.monotonic() - started
        print(f"pool-of-1 acquire while the lock is held: {acquire_s:.3f}s (CHAIN_WAIT_MS={sma.CHAIN_WAIT_MS})")
        dnc, audit = await _rows(pool, world.a)
        assert len(dnc) == 1 and audit == []
    finally:
        await tx.commit()
        await conn.close()
    try:
        await asyncio.wait_for(_drain(), 15)
        dnc, audit = await _rows(pool, world.a)
        _check(dnc, audit, "caller_opt_out")
    finally:
        await pool.close()


def _numbers(count=20):
    return ["+44207946" + f"{1000 + i:04d}" for i in range(count)]


async def _burst(world):
    results = await asyncio.gather(*[_caller(world.plain, world.a, n) for n in _numbers()])
    assert all(r["dnc_added"] for r in results)


async def _burst_rows(world):
    dnc, audit = await _rows(world.plain, world.a)
    return dnc, audit


@pytest.mark.asyncio
async def test_a_burst_of_twenty_marks_leaves_twenty_audit_rows(world, caplog):
    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        await _burst(world)
        await asyncio.wait_for(_drain(), 30)
    print(f"burst of 20, no outside lock: {time.monotonic() - started:.2f}s")
    dnc, audit = await _burst_rows(world)
    assert len(dnc) == 20 and len(audit) == 20
    assert {r["resource_id"] for r in audit} == {r["id"] for r in dnc}
    assert not [r for r in caplog.records if "dnc_mark_audit_missing" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_burst_of_twenty_marks_during_a_held_lock_still_leaves_twenty_audit_rows(world, caplog):
    conn, tx = await _hold_chain_lock(DSN)
    started = time.monotonic()
    try:
        with caplog.at_level(logging.ERROR):
            await _burst(world)
            dnc, audit = await _burst_rows(world)
            assert len(dnc) == 20 and audit == []     # every mark is there, no audit yet
            await asyncio.sleep(1.0)
    finally:
        await tx.commit()
        await conn.close()
    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(_drain(), 60)
    print(f"burst of 20 with the lock held about 1s: {time.monotonic() - started:.2f}s")
    dnc, audit = await _burst_rows(world)
    assert len(dnc) == 20 and len(audit) == 20
    assert {r["resource_id"] for r in audit} == {r["id"] for r in dnc}
    assert not [r for r in caplog.records if "dnc_mark_audit_missing" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_role_without_rights_on_the_audit_table_ends_at_once_with_one_error(world, caplog):
    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        result = await _caller(world.no_audit, world.a)
        await asyncio.wait_for(_drain(), 5)
    elapsed = time.monotonic() - started
    assert result["dnc_added"] is True and elapsed < 5.0 and not _mine()
    dnc, _ = await _rows(world.plain, world.a)
    assert len(dnc) == 1
    missing = [r for r in caplog.records if "dnc_mark_audit_missing" in r.getMessage()]
    assert len(missing) == 1
    assert "attempts=1" in missing[0].getMessage()
    assert "InsufficientPrivilegeError" in missing[0].getMessage()
    _, audit = await _rows(world.plain, world.a)
    assert audit == []
