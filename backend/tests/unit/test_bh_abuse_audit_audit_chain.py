"""AuditLogger buffering and hash chain, against a real Postgres.

Bug-hunt finding calls-recordings-analytics-4:

* entries buffered in memory were flushed only by the *next* log() call, with
  no timer and no shutdown flush, so a quiet process lost its trailing rows;
* every buffered entry took ``previous_hash`` from the database (ignoring the
  unflushed batch), so entries in one batch forked the chain;
* ``verify_chain_integrity`` recomputed the hash from fewer fields than
  ``_compute_hash`` uses, so healthy chains reported ``hash_mismatch``.

Runs only with an explicit TEST_DATABASE_URL; creates and drops its own
database. ``audit_logs`` DDL is cut from ``database/schema/baseline_2026-06-02.sql``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.audit_logger import AuditEvent, AuditLogger

pytestmark = pytest.mark.asyncio

_BASELINE = (
    Path(__file__).resolve().parents[2] / "database" / "schema" / "baseline_2026-06-02.sql"
)


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


async def _jsonb_codec(conn):
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


_DB_PREFIX = "bh_chain"


async def _apply_schema(conn):
    ddl = re.search(
        r"CREATE TABLE public\.audit_logs \(.*?\n\);",
        _BASELINE.read_text(encoding="utf-8"),
        re.S,
    )
    assert ddl
    await conn.execute(
        "CREATE FUNCTION public.uuid_generate_v4() RETURNS uuid "
        "LANGUAGE sql AS $$ SELECT gen_random_uuid() $$"
    )
    await conn.execute(ddl.group(0))


@pytest.fixture(scope="module")
def database_dsn():
    """One throwaway database per module (CREATE DATABASE is slow on CI boxes)."""
    admin_dsn = _admin_dsn_or_skip()
    dbname = f"{_DB_PREFIX}_{uuid.uuid4().hex[:10]}"
    base, _, _ = admin_dsn.rpartition("/")

    async def create():
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'CREATE DATABASE "{dbname}"')
        await admin.close()
        conn = await asyncpg.connect(f"{base}/{dbname}")
        try:
            await _apply_schema(conn)
        finally:
            await conn.close()

    async def drop():
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()

    asyncio.run(create())
    try:
        yield f"{base}/{dbname}"
    finally:
        asyncio.run(drop())


@pytest_asyncio.fixture
async def pool(database_dsn):
    p = await asyncpg.create_pool(database_dsn, min_size=1, max_size=5, init=_jsonb_codec)
    try:
        async with p.acquire() as conn:
            await conn.execute("TRUNCATE audit_logs CASCADE")
        yield p
    finally:
        await p.close()


async def _close(audit):
    # Teardown only; test_aclose_flushes_the_pending_buffer pins aclose itself.
    await getattr(audit, "aclose", audit.flush)()


async def _rows(pool):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT event_id, action, previous_hash, entry_hash FROM audit_logs "
            "ORDER BY event_time"
        )


async def test_entries_buffered_together_form_one_unbroken_chain(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    try:
        for i in range(5):
            await audit.log(AuditEvent.RECORD_VIEWED, action=f"a{i}", metadata={"i": i})
        await audit.flush()
        rows = await _rows(pool)

        assert [r["action"] for r in rows] == [f"a{i}" for i in range(5)]
        assert rows[0]["previous_hash"] == "0" * 64
        for prev, cur in zip(rows, rows[1:]):
            assert cur["previous_hash"] == prev["entry_hash"]
        result = await audit.verify_chain_integrity()
        assert result == {"valid": True, "entries_checked": 5, "errors": []}
    finally:
        await _close(audit)


async def test_chain_continues_across_flushes(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="first")
        await audit.flush()
        await audit.log(AuditEvent.RECORD_VIEWED, action="second")
        await audit.flush()

        assert (await audit.verify_chain_integrity())["valid"] is True
        rows = await _rows(pool)
        assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
    finally:
        await _close(audit)


async def test_concurrent_log_calls_do_not_fork_the_chain(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600, batch_size=7)
    try:
        await asyncio.gather(
            *[audit.log(AuditEvent.RECORD_VIEWED, action=f"c{i}") for i in range(30)]
        )
        await audit.flush()
        rows = await _rows(pool)

        assert len(rows) == 30
        assert len({r["previous_hash"] for r in rows}) == 30
        result = await audit.verify_chain_integrity()
        assert result["valid"] is True, result["errors"][:2]
    finally:
        await _close(audit)


async def test_verify_accepts_every_kind_of_entry_it_can_write(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    tenant = uuid.uuid4()
    actor = uuid.uuid4()
    try:
        # Empty metadata is stored as NULL; it must still verify.
        await audit.log(AuditEvent.RECORD_VIEWED, action="bare", metadata={})
        await audit.log(
            AuditEvent.USER_UPDATED, action="full", actor_id=actor, tenant_id=tenant,
            before_state={"role": "a"}, after_state={"role": "b"},
            metadata={"reason": "x"}, resource_type="user", resource_id=uuid.uuid4(),
        )
        await audit.flush()

        result = await audit.verify_chain_integrity()
        assert result["valid"] is True, result["errors"]
        assert result["entries_checked"] == 2
    finally:
        await _close(audit)


async def test_verify_still_detects_tampering_and_a_removed_row(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    try:
        for i in range(3):
            await audit.log(AuditEvent.RECORD_VIEWED, action=f"t{i}")
        await audit.flush()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE audit_logs SET action = 'edited' WHERE action = 't1'")

        result = await audit.verify_chain_integrity()
        assert result["valid"] is False
        assert [e["error"] for e in result["errors"]] == ["hash_mismatch"]

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM audit_logs WHERE action = 't0'")
        result = await audit.verify_chain_integrity()
        assert "chain_break" in {e["error"] for e in result["errors"]}
    finally:
        await _close(audit)


async def test_background_timer_flushes_a_quiet_buffer(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=0.1)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="lonely")
        # No further log() call: only a timer can write this row.
        for _ in range(50):
            if await _rows(pool):
                break
            await asyncio.sleep(0.1)

        assert [r["action"] for r in await _rows(pool)] == ["lonely"]
    finally:
        await _close(audit)


async def test_aclose_flushes_the_pending_buffer(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    await audit.log(AuditEvent.RECORD_VIEWED, action="pending")
    assert await _rows(pool) == []

    await audit.aclose()

    assert [r["action"] for r in await _rows(pool)] == ["pending"]


async def test_one_rejected_row_does_not_cost_the_rest_of_the_batch(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="good-1")
        # country_code is char(2): the database rejects this row.
        await audit.log(AuditEvent.RECORD_VIEWED, action="bad", country_code="USA")
        await audit.log(AuditEvent.RECORD_VIEWED, action="good-2")

        await audit.flush()

        assert [r["action"] for r in await _rows(pool)] == ["good-1", "good-2"]
    finally:
        await _close(audit)


async def test_entries_survive_an_unreachable_database_and_land_on_retry(pool):
    audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="kept-1")
        await audit.log(AuditEvent.RECORD_VIEWED, action="kept-2")
        real_pool = audit.db_pool
        audit.db_pool = None  # acquire_with_tenant raises AttributeError
        with pytest.raises(Exception):
            await audit.flush()
        audit.db_pool = real_pool

        await audit.flush()

        assert [r["action"] for r in await _rows(pool)] == ["kept-1", "kept-2"]
        assert (await audit.verify_chain_integrity())["valid"] is True
    finally:
        await _close(audit)
