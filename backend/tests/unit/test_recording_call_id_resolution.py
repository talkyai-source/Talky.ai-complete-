"""Regression tests for the 2026-07-17 recording call_id resolution fix.

DEFECT 1: `_insert_recording_record` did a bare `UUID(call_id)` inside the
INSERT into recordings_s3. On non-dialer (manual PBX) calls, `call_id` is a
PBX channel string like "talky-out-da0f496d..." — not a UUID — so `UUID()`
raised "badly formed hexadecimal UUID string", the broad except swallowed
it, and the recordings_s3 row was silently never written (the WAV still
landed on disk/S3, but the UI could never list or stream it).

The fix resolves the incoming id to the authoritative `calls.id` UUID
before the INSERT: if it already parses as a UUID, use it as-is; otherwise
look it up via `calls.external_call_uuid` (tenant-scoped); if neither
resolves, degrade cleanly — log a WARNING and return None, never raise.

These tests prove:
  1. A non-UUID call_id with no matching `calls` row -> no exception,
     returns None, logs a WARNING (not an ERROR).
  2. A non-UUID call_id that resolves via `external_call_uuid` -> the
     INSERT uses the resolved `calls.id`, not the raw channel string.
  3. A proper UUID call_id -> unchanged behaviour (no lookup needed).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.domain.services.recording_service import RecordingService


RESOLVED_CALL_UUID = UUID("11111111-1111-1111-1111-111111111111")
TENANT_UUID = "22222222-2222-2222-2222-222222222222"
CAMPAIGN_UUID = "33333333-3333-3333-3333-333333333333"
PBX_CHANNEL_ID = "talky-out-da0f496d-1234-4a2b-9c3d-abcdef012345"


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    """Configurable fake asyncpg connection.

    ``resolved_row`` controls what the `external_call_uuid` lookup
    (SELECT ... FROM calls) returns. ``insert_row`` controls what the
    recordings_s3 INSERT ... RETURNING id returns.
    """

    def __init__(self, resolved_row=None, insert_row=None):
        self.resolved_row = resolved_row
        self.insert_row = insert_row
        self.fetchrow_calls = []
        self.execute_calls = []

    async def fetchrow(self, query, *args, **kwargs):
        self.fetchrow_calls.append((query, args))
        if "FROM calls" in query:
            return self.resolved_row
        if "INSERT INTO recordings_s3" in query:
            # Record the call_id positional arg (first bind param) actually
            # used in the INSERT, so tests can assert on it.
            return self.insert_row
        return None

    async def execute(self, *args, **kwargs):
        self.execute_calls.append(args)
        return None

    def transaction(self):
        # No-op txn ctx: production wraps the RLS set_config + resolution
        # SELECT + INSERT in conn.transaction() so the tenant context is
        # transaction-local.
        return _FakeTxnCtx()


class _FakeTxnCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeDbPool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


def _svc(conn: FakeConn) -> RecordingService:
    return RecordingService(db_pool=FakeDbPool(conn))


# ---------------------------------------------------------------------------
# 1 — non-UUID call_id, no matching calls row -> clean no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_recording_record_unresolvable_non_uuid_call_id_logs_warning_no_raise(
    caplog,
):
    conn = FakeConn(resolved_row=None)
    svc = _svc(conn)

    with caplog.at_level(logging.WARNING, logger="app.domain.services.recording_service"):
        result = await svc._insert_recording_record(
            call_id=PBX_CHANNEL_ID,
            tenant_id=TENANT_UUID,
            campaign_id=CAMPAIGN_UUID,
            s3_key="some/key.wav",
            file_size_bytes=1234,
            duration_seconds=10,
            upload_started=None,
            upload_finished=None,
        )

    assert result is None
    # No ERROR-level "Failed to insert recording_s3 record" — the
    # unresolved-id case must degrade via a WARNING, not the broad except.
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert any(
        "recording_call_id_unresolved" in r.message for r in caplog.records
    )
    # The INSERT itself must never have been attempted.
    assert not any(
        "INSERT INTO recordings_s3" in q for q, _ in conn.fetchrow_calls
    )


# ---------------------------------------------------------------------------
# 2 — non-UUID call_id resolvable via external_call_uuid -> resolved UUID used
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_recording_record_resolves_non_uuid_via_external_call_uuid():
    conn = FakeConn(
        resolved_row={"id": RESOLVED_CALL_UUID},
        insert_row={"id": UUID("44444444-4444-4444-4444-444444444444")},
    )
    svc = _svc(conn)

    result = await svc._insert_recording_record(
        call_id=PBX_CHANNEL_ID,
        tenant_id=TENANT_UUID,
        campaign_id=CAMPAIGN_UUID,
        s3_key="some/key.wav",
        file_size_bytes=1234,
        duration_seconds=10,
        upload_started=None,
        upload_finished=None,
    )

    assert result == UUID("44444444-4444-4444-4444-444444444444")

    insert_calls = [
        (q, args) for q, args in conn.fetchrow_calls if "INSERT INTO recordings_s3" in q
    ]
    assert len(insert_calls) == 1
    _, insert_args = insert_calls[0]
    # First bind param is call_id — must be the RESOLVED calls.id UUID, not
    # the raw PBX channel string.
    assert insert_args[0] == RESOLVED_CALL_UUID

    # The lookup itself must have been tenant-scoped with the raw channel id.
    lookup_calls = [
        (q, args) for q, args in conn.fetchrow_calls if "FROM calls" in q
    ]
    assert len(lookup_calls) == 1
    _, lookup_args = lookup_calls[0]
    assert lookup_args[0] == PBX_CHANNEL_ID
    assert lookup_args[1] == UUID(TENANT_UUID)


# ---------------------------------------------------------------------------
# 3 — proper UUID call_id -> unchanged behaviour, no lookup performed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_recording_record_proper_uuid_skips_lookup():
    conn = FakeConn(
        resolved_row=None,  # would fail resolution if the lookup ran at all
        insert_row={"id": UUID("55555555-5555-5555-5555-555555555555")},
    )
    svc = _svc(conn)

    result = await svc._insert_recording_record(
        call_id=str(RESOLVED_CALL_UUID),
        tenant_id=TENANT_UUID,
        campaign_id=CAMPAIGN_UUID,
        s3_key="some/key.wav",
        file_size_bytes=1234,
        duration_seconds=10,
        upload_started=None,
        upload_finished=None,
    )

    assert result == UUID("55555555-5555-5555-5555-555555555555")

    # The external_call_uuid lookup must never run for an id that already
    # parses as a UUID.
    lookup_calls = [
        (q, args) for q, args in conn.fetchrow_calls if "FROM calls" in q
    ]
    assert len(lookup_calls) == 0

    insert_calls = [
        (q, args) for q, args in conn.fetchrow_calls if "INSERT INTO recordings_s3" in q
    ]
    assert len(insert_calls) == 1
    _, insert_args = insert_calls[0]
    assert insert_args[0] == RESOLVED_CALL_UUID


# ---------------------------------------------------------------------------
# 4 — DEFECT 2: calls.lead_id is nullable in schema, and the stub
# ``calls`` row insert (telephony/recording.py) never supplies it.
# ---------------------------------------------------------------------------

def test_schema_calls_lead_id_is_nullable():
    import pathlib

    schema_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "database"
        / "complete_schema.sql"
    )
    schema_sql = schema_path.read_text(encoding="utf-8")

    # Locate the `calls` table definition specifically (not `dialer_jobs`,
    # which also has a lead_id column and legitimately stays NOT NULL).
    calls_table_start = schema_sql.index("CREATE TABLE IF NOT EXISTS calls (")
    calls_table_end = schema_sql.index(");", calls_table_start)
    calls_table_sql = schema_sql[calls_table_start:calls_table_end]

    assert "lead_id UUID REFERENCES leads(id)" in calls_table_sql
    assert "lead_id UUID NOT NULL REFERENCES leads(id)" not in calls_table_sql


def test_migration_drops_calls_lead_id_not_null():
    import pathlib

    migration_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "20260717_calls_lead_id_nullable.sql"
    )
    assert migration_path.exists()
    sql = migration_path.read_text(encoding="utf-8")
    assert "ALTER TABLE calls ALTER COLUMN lead_id DROP NOT NULL" in sql


def test_stub_calls_row_insert_does_not_supply_lead_id():
    import pathlib

    recording_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "app"
        / "domain"
        / "services"
        / "telephony"
        / "recording.py"
    )
    src = recording_path.read_text(encoding="utf-8")

    insert_start = src.index("INSERT INTO calls (")
    insert_end = src.index(")", insert_start)
    insert_columns = src[insert_start:insert_end]

    assert "lead_id" not in insert_columns, (
        "the stub calls row must leave lead_id NULL (no lead exists for a "
        "manual/PBX-originated call) rather than supplying a value"
    )
