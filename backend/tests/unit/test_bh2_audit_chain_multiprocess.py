"""The AuditLogger hash chain stays one chain when several processes write it.

Bug-hunt finding calls-recordings-analytics-4 (the part left after the
in-process fixes). Production runs several processes (talky-api,
talky-api-web, the dialer, the voice and reminder workers) and every one has
its own AuditLogger. Each chained its entries onto the tail it read from the
database at log() time, so two processes that flushed around the same time
both chained onto the same tail: the chain forked and verify_chain_integrity
reported chain_break on healthy data. The in-process asyncio.Lock cannot help
across processes.

Here N loggers on N separate pools stand in for N processes: they share
nothing but the database. That needs a real Postgres, so those tests opt in
with BH_PG_DSN pointing at a scratch database, e.g.
postgresql://postgres@127.0.0.1:55432/postgres. They create and drop a private
schema inside it (``audit_logs`` DDL cut from
database/schema/baseline_2026-06-02.sql) and never touch ``public``.

The last test needs no database: a recording fake connection pins that the
hashes come from the tail read under the chain lock at flush time.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.audit_logger import AuditEvent, AuditLogger

DSN = os.environ.get("BH_PG_DSN")
needs_pg = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a scratch Postgres)")

_BASELINE = (
    Path(__file__).resolve().parents[2] / "database" / "schema" / "baseline_2026-06-02.sql"
)


def _audit_logs_ddl(schema: str) -> list[str]:
    """audit_logs, its primary key and indexes from the production baseline, in ``schema``.

    Foreign keys are left out: the tables they point at are not part of this test.
    """
    sql = _BASELINE.read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE public\.audit_logs \(.*?\n\);", sql, re.S)
    assert table, "audit_logs DDL not found in the baseline"
    statements = [table.group(0)]
    statements += re.findall(
        r"ALTER TABLE ONLY public\.audit_logs\n    ADD CONSTRAINT audit_logs_pkey [^;]*;", sql
    )
    statements += re.findall(r"CREATE INDEX idx_audit_logs_\w+ ON public\.audit_logs [^;]*;", sql)
    return [
        s.replace("public.uuid_generate_v4()", "gen_random_uuid()").replace(
            "public.audit_logs", f"{schema}.audit_logs"
        )
        for s in statements
    ]


async def _jsonb_codec(conn):
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


@pytest.fixture(scope="module")
def schema():
    """One private schema per module, dropped afterwards."""
    name = f"bh2_audit_{uuid.uuid4().hex[:10]}"

    async def create():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(f'CREATE SCHEMA "{name}"')
            for statement in _audit_logs_ddl(name):
                await conn.execute(statement)
        finally:
            await conn.close()

    async def drop():
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(create())
    try:
        yield name
    finally:
        asyncio.run(drop())


class _Cluster:
    """AuditLoggers on separate pools: each one stands in for one OS process."""

    def __init__(self, schema: str, admin: asyncpg.Pool):
        self.schema = schema
        self.admin = admin
        self.pools: list[asyncpg.Pool] = []
        self.loggers: list[AuditLogger] = []

    async def logger(self, **kwargs) -> AuditLogger:
        pool = await asyncpg.create_pool(
            DSN, min_size=1, max_size=3, init=_jsonb_codec,
            server_settings={"search_path": self.schema},
        )
        self.pools.append(pool)
        audit = AuditLogger(pool, signing_key="k", flush_interval_seconds=3600, **kwargs)
        self.loggers.append(audit)
        return audit

    async def rows(self):
        async with self.admin.acquire() as conn:
            return await conn.fetch(
                "SELECT action, event_time, previous_hash, entry_hash FROM audit_logs "
                "ORDER BY event_time, created_at"
            )


@pytest_asyncio.fixture
async def cluster(schema):
    admin = await asyncpg.create_pool(
        DSN, min_size=1, max_size=2, init=_jsonb_codec,
        server_settings={"search_path": schema},
    )
    members = _Cluster(schema, admin)
    try:
        async with admin.acquire() as conn:
            await conn.execute("TRUNCATE audit_logs")
        yield members
    finally:
        for audit in members.loggers:
            try:
                await audit.aclose()
            except Exception:  # a test may leave the chain lock held on purpose
                pass
        for pool in members.pools:
            await pool.close()
        await admin.close()


def _assert_one_unbroken_chain(rows, result, expected_rows):
    assert len(rows) == expected_rows
    assert result == {"valid": True, "entries_checked": expected_rows, "errors": []}, result["errors"][:2]
    # A fork is two rows that name the same predecessor.
    assert len({r["previous_hash"] for r in rows}) == expected_rows
    assert rows[0]["previous_hash"] == "0" * 64
    for prev, cur in zip(rows, rows[1:]):
        assert cur["previous_hash"] == prev["entry_hash"]
    # The order verify_chain_integrity walks (event_time) is the order of the chain.
    times = [r["event_time"] for r in rows]
    assert times == sorted(set(times))


@needs_pg
async def test_two_processes_logging_and_flushing_in_turn_keep_one_chain(cluster):
    a, b = await cluster.logger(), await cluster.logger()

    # Both buffer before either flushes: at log() time each sees an empty chain.
    await a.log(AuditEvent.RECORD_VIEWED, action="a1", metadata={"n": 1})
    await b.log(AuditEvent.RECORD_VIEWED, action="b1", metadata={"n": 2})
    await a.log(AuditEvent.USER_UPDATED, action="a2", after_state={"role": "x"})
    await b.log(AuditEvent.USER_UPDATED, action="b2", before_state={"role": "x"})
    await a.flush()
    await b.flush()
    # And again with the flushes the other way round.
    await a.log(AuditEvent.RECORD_VIEWED, action="a3")
    await b.log(AuditEvent.RECORD_VIEWED, action="b3")
    await b.flush()
    await a.flush()

    _assert_one_unbroken_chain(
        await cluster.rows(), await a.verify_chain_integrity(), expected_rows=6
    )


@needs_pg
async def test_three_processes_flushing_concurrently_do_not_fork_the_chain(cluster):
    processes = [await cluster.logger(batch_size=4) for _ in range(3)]

    # batch_size=4 makes log() flush inline, so flushes of the three overlap.
    await asyncio.gather(
        *[
            process.log(AuditEvent.RECORD_VIEWED, action=f"p{p}-{n}", metadata={"p": p, "n": n})
            for n in range(24)
            for p, process in enumerate(processes)
        ]
    )
    await asyncio.gather(*[process.flush() for process in processes])

    _assert_one_unbroken_chain(
        await cluster.rows(), await processes[0].verify_chain_integrity(), expected_rows=72
    )


@needs_pg
async def test_an_entry_logged_earlier_but_flushed_later_is_chained_after_the_tail(cluster):
    a, b = await cluster.logger(), await cluster.logger()

    await a.log(AuditEvent.RECORD_VIEWED, action="logged-first")
    await asyncio.sleep(0.03)  # the Windows clock ticks every ~16 ms
    await b.log(AuditEvent.RECORD_VIEWED, action="logged-second")
    await b.flush()
    await a.flush()  # stamped before the row that is already the tail

    rows = await cluster.rows()
    # verify walks event_time order, so the chain order has to be the time order.
    assert [r["action"] for r in rows] == ["logged-second", "logged-first"]
    assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
    assert rows[1]["event_time"] > rows[0]["event_time"]
    assert (await a.verify_chain_integrity())["valid"] is True


@needs_pg
async def test_a_rejected_row_leaves_no_hole_in_the_chain(cluster):
    a = await cluster.logger()

    await a.log(AuditEvent.RECORD_VIEWED, action="good-1")
    # country_code is char(2): the database rejects this row and it is dropped.
    await a.log(AuditEvent.RECORD_VIEWED, action="bad", country_code="USA")
    await a.log(AuditEvent.RECORD_VIEWED, action="good-2")
    await a.flush()

    rows = await cluster.rows()
    assert [r["action"] for r in rows] == ["good-1", "good-2"]
    # good-2 must follow good-1, not the dropped row.
    assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
    assert (await a.verify_chain_integrity()) == {"valid": True, "entries_checked": 2, "errors": []}


@needs_pg
async def test_flush_waits_for_the_chain_lock_then_gives_up_and_keeps_its_entries(cluster, monkeypatch):
    module = importlib.import_module("app.domain.services.audit_logger")
    key = getattr(module, "_CHAIN_LOCK_KEY", None)
    assert key is not None, "chain writers must serialise on a fixed advisory lock key"
    monkeypatch.setattr(module, "_CHAIN_LOCK_TIMEOUT_MS", 200)
    a = await cluster.logger()
    await a.log(AuditEvent.RECORD_VIEWED, action="held-1")
    await a.log(AuditEvent.RECORD_VIEWED, action="held-2")

    # Another process is mid-flush: it holds the chain lock in an open transaction.
    other = await asyncpg.connect(DSN)
    transaction = other.transaction()
    await transaction.start()
    try:
        await other.execute("SELECT pg_advisory_xact_lock($1)", key)
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            await a.flush()
        assert await cluster.rows() == []  # nothing is written without the lock
    finally:
        await transaction.rollback()
        await other.close()

    await a.flush()  # the lock is free again: the kept entries land, in order

    rows = await cluster.rows()
    assert [r["action"] for r in rows] == ["held-1", "held-2"]
    assert (await a.verify_chain_integrity())["valid"] is True


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConnection:
    """Records every statement; ``tail`` is what the chain tail is *right now*."""

    def __init__(self, tail):
        self.tail = tail
        self.calls: list[tuple[str, str]] = []
        self.insert_sql = ""
        self.inserted: list[tuple] = []

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql))
        return self.tail

    async def executemany(self, sql, rows):
        self.calls.append(("executemany", sql))
        self.insert_sql, self.inserted = sql, list(rows)


class _FakePool:
    def __init__(self, connection):
        self._connection = connection

    def acquire(self, **kwargs):
        connection = self._connection

        class _Acquired:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *exc):
                return False

        return _Acquired()


async def test_hashes_come_from_the_tail_read_under_the_chain_lock_at_flush_time():
    tail_when_logged = {
        "entry_hash": "a" * 64,
        "event_time": datetime(2020, 1, 1, tzinfo=timezone.utc),
    }
    connection = _FakeConnection(tail_when_logged)
    audit = AuditLogger(_FakePool(connection), signing_key="k", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="one")
        await audit.log(AuditEvent.RECORD_VIEWED, action="two")
        # Another process extends the chain while these two sit in the buffer.
        tail_at_flush = {
            "entry_hash": "b" * 64,
            "event_time": datetime.now(timezone.utc) + timedelta(seconds=30),
        }
        connection.tail = tail_at_flush
        connection.calls.clear()

        await audit.flush()

        kinds = [kind for kind, _ in connection.calls]
        lock_at = next(
            (i for i, (_, sql) in enumerate(connection.calls) if "pg_advisory_xact_lock" in sql),
            None,
        )
        assert lock_at is not None, f"flush never took the chain lock: {connection.calls}"
        assert lock_at < kinds.index("fetchrow") < kinds.index("executemany")

        columns = [
            c.strip()
            for c in re.search(r"INSERT INTO audit_logs \((.*?)\) VALUES", connection.insert_sql, re.S)
            .group(1)
            .split(",")
        ]
        rows = [dict(zip(columns, values)) for values in connection.inserted]
        assert [r["action"] for r in rows] == ["one", "two"]
        assert rows[0]["previous_hash"] == tail_at_flush["entry_hash"]
        assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
        # Logged before the tail existed, but stored after it.
        assert rows[0]["event_time"] > tail_at_flush["event_time"]
        assert rows[1]["event_time"] > rows[0]["event_time"]
    finally:
        await audit.aclose()


async def test_content_that_cannot_be_hashed_is_refused_by_log_not_by_every_later_flush():
    tail = {"entry_hash": "0" * 64, "event_time": datetime(2020, 1, 1, tzinfo=timezone.utc)}
    connection = _FakeConnection(tail)
    audit = AuditLogger(_FakePool(connection), signing_key="k", flush_interval_seconds=3600)
    try:
        # Hashing now happens at flush time. If this entry were buffered it
        # would make every flush raise, and no audit row would ever be written.
        with pytest.raises(TypeError):
            await audit.log(
                AuditEvent.RECORD_VIEWED, action="poison", metadata={(1, 2): "tuple key"}
            )
        await audit.log(AuditEvent.RECORD_VIEWED, action="fine")

        await audit.flush()

        assert len(connection.inserted) == 1
    finally:
        await audit.aclose()
