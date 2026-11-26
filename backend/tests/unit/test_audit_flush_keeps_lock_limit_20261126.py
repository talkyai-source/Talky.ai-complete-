"""The audit flush must give back the caller's lock limit, never reset it to the default."""
import inspect
from datetime import datetime, timezone

from app.domain.services import audit_logger
from app.domain.services.audit_logger import AuditEvent, AuditLogger


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.executed: list[str] = []

    def transaction(self):
        return _Tx()

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchrow(self, sql, *args):
        return {"entry_hash": "0" * 64, "event_time": datetime(2020, 1, 1, tzinfo=timezone.utc)}

    async def executemany(self, sql, rows):
        return None


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self, **kwargs):
        conn = self._conn

        class _A:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _A()


async def test_flush_restores_the_previous_lock_limit_instead_of_the_default():
    conn = _Conn()
    audit = AuditLogger(_Pool(conn), signing_key="dummy", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="one")
        conn.executed.clear()
        await audit.flush()
    finally:
        await audit.aclose()
    lock_sql = [s for s in conn.executed if "pg_advisory_xact_lock" in s]
    assert len(lock_sql) == 1, conn.executed
    sql = lock_sql[0]
    assert "TO DEFAULT" not in sql.upper()
    assert "current_setting('lock_timeout')" in sql
    assert "PERFORM set_config('lock_timeout', prev, true)" in sql
    assert not any("TO DEFAULT" in s.upper() for s in conn.executed)


async def test_flush_lock_statement_is_the_same_text_as_lock_transaction():
    conn = _Conn()
    await AuditLogger.lock_transaction(_InTx(conn))
    expected = conn.executed[0]

    flush_conn = _Conn()
    audit = AuditLogger(_Pool(flush_conn), signing_key="dummy", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="one")
        flush_conn.executed.clear()
        await audit.flush()
    finally:
        await audit.aclose()
    assert [s for s in flush_conn.executed if "pg_advisory_xact_lock" in s] == [expected]


class _InTx:
    def __init__(self, conn):
        self._conn = conn

    def is_in_transaction(self):
        return True

    async def execute(self, sql, *args):
        await self._conn.execute(sql, *args)


def test_flush_literals_match_the_module_constants_and_text_is_constant():
    src = inspect.getsource(AuditLogger._flush_locked)
    assert str(audit_logger._CHAIN_LOCK_KEY) in src
    assert "'%d'" % audit_logger._CHAIN_LOCK_TIMEOUT_MS in src
    assert "SET LOCAL lock_timeout" not in src


# --- a lock wait that ran out is not a bad row: the batch is kept, nothing is dropped ---

class _LockedConn(_Conn):
    """`batch_error` is raised by the batch insert, `row_error` by each single-row insert."""

    def __init__(self, batch_error, row_error=None):
        super().__init__()
        self.batch_error, self.row_error = batch_error, row_error

    async def execute(self, sql, *args):
        if args and self.row_error is not None:
            raise self.row_error
        await super().execute(sql, *args)

    async def executemany(self, sql, rows):
        raise self.batch_error


async def _flush_with(conn, caplog):
    import asyncpg
    import logging
    import pytest

    audit = AuditLogger(_Pool(_Conn()), signing_key="dummy", flush_interval_seconds=3600)
    try:
        await audit.log(AuditEvent.RECORD_VIEWED, action="one")
        await audit.log(AuditEvent.RECORD_VIEWED, action="two")
        audit.db_pool = _Pool(conn)
        caplog.set_level(logging.WARNING)
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            await audit.flush()
        kept = [e["action"] for e in audit._batch]
    finally:
        audit._batch = []
        await audit.aclose()
    return kept


async def test_a_lock_wait_on_the_batch_insert_keeps_every_entry(caplog):
    import asyncpg

    conn = _LockedConn(asyncpg.exceptions.LockNotAvailableError("lock timeout"))
    assert await _flush_with(conn, caplog) == ["one", "two"]
    assert not [r for r in caplog.records if "dropping bad row" in r.getMessage()]
    assert not [r for r in caplog.records if "dropped" in r.getMessage()]


async def test_a_lock_wait_on_a_single_row_insert_keeps_every_entry(caplog):
    import asyncpg

    conn = _LockedConn(ValueError("one bad row in the batch"),
                       asyncpg.exceptions.LockNotAvailableError("lock timeout"))
    assert await _flush_with(conn, caplog) == ["one", "two"]
    assert not [r for r in caplog.records if "dropping bad row" in r.getMessage()]
