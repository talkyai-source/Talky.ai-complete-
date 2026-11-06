"""F12-06 bounded retention, explicit tenant SQL, holds and dry-run defaults."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

import pytest

from app.workers import cleanup_worker as worker


TENANT = "a0000000-0000-4000-8000-000000000001"
OTHER = "b0000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
PERSONAL = {"transcripts", "conversations", "call_guard_decisions"}
TARGETS = ("call_events", "call_legs", "stream_events", "transcripts", "conversations", "call_guard_decisions")
WINDOWS = {table: 365 if table in {"transcripts", "conversations"} else 90 for table in TARGETS}


def uid(number):
    return f"d0000000-0000-4000-8000-{number:012d}"


class Connection:
    """No RLS simulation: SQL must carry the owner tenant and strict cutoff."""

    def __init__(self):
        self.rows = {name: [] for name in TARGETS}
        self.calls = {}
        for tenant_index, tenant in enumerate((TENANT, OTHER), 1):
            call = uid(tenant_index)
            self.calls[call] = {"tenant_id": tenant, "status": "completed"}
            for table_index, table in enumerate(TARGETS, 1):
                for age_index, age in enumerate((WINDOWS[table] + 1, WINDOWS[table], 1), 1):
                    row = {"id": uid(100 * tenant_index + 10 * table_index + age_index),
                           "created_at": NOW - timedelta(days=age), "tenant_id": tenant,
                           "call_id": call, "drive_file_id": None}
                    if table in {"call_events", "call_legs"}:
                        row.pop("tenant_id")
                    if table == "stream_events":
                        row["related_call_id"] = row.pop("call_id")
                    self.rows[table].append(row)
        self.present = set(TARGETS) | {"tenants", "calls", "suspension_events"}
        self.holds = set()
        self.busy = set()
        self.depth = 0
        self.locked_tenant = None
        self.checked_tenant = None
        self.hold_after_count = None
        self.fail_table = None
        self.deletes = []
        self.counts = []

    @asynccontextmanager
    async def transaction(self, **kwargs):
        before = deepcopy(self.rows)
        self.depth += 1
        try:
            yield
        except BaseException:
            self.rows = before
            raise
        finally:
            self.depth -= 1
            if self.depth == 0:
                self.locked_tenant = None
                self.checked_tenant = None

    def _select(self, sql, args):
        flat = " ".join(sql.lower().split())
        table = next((match.group(1) for match in re.finditer(
            r"\b(?:from|into)\s+(?:public\.)?(\w+)\b", flat,
        ) if match.group(1) in TARGETS), None)
        assert table is not None, f"Unexpected retention table SQL: {sql}"
        assert table in self.present, "Missing-table preflight must precede retention query"
        assert re.search(r"\b(?:[a-z_]\w*\.)?tenant_id\s*=\s*\$1", flat), (
            "Retention query lacks an explicit tenant predicate under BYPASSRLS"
        )
        assert len(args) >= 2 and str(args[0]) in {TENANT, OTHER}
        assert isinstance(args[1], datetime) and args[1].tzinfo is not None
        assert re.search(r"\b(?:[a-z_]\w*\.)?created_at\s*<\s*\$2", flat), (
            "Preview and apply must use the same bound strict cutoff"
        )
        tenant, cutoff = str(args[0]), args[1]
        assert cutoff == NOW - timedelta(days=WINDOWS[table])
        if table in {"call_events", "call_legs", "transcripts"}:
            assert re.search(r"\bjoin\s+(?:public\.)?calls\b", flat), (
                "Tenantless/nullable legacy rows must be bound through the parent call"
            )
        if table == "transcripts":
            assert "drive_file_id is null" in flat, "Do not orphan legacy Drive-backed transcripts"
        if self.fail_table == table:
            raise RuntimeError("synthetic database permission failure")
        selected = []
        for row in self.rows[table]:
            call = self.calls.get(row.get("call_id", row.get("related_call_id")))
            owner = row.get("tenant_id") or (call or {}).get("tenant_id")
            if owner != tenant or row["created_at"] >= cutoff:
                continue
            if table == "transcripts" and row.get("drive_file_id"):
                continue
            selected.append(row)
        return table, tenant, sorted(selected, key=lambda row: (row["created_at"], row["id"]))

    async def fetch(self, sql, *args):
        flat = " ".join(sql.lower().split())
        assert re.search(r"\bfrom\s+(?:public\.)?tenants\b", flat), f"Unexpected fetch: {sql}"
        assert "order by id" in flat and "limit" in flat
        assert len(args) == 2 and 0 < args[1] <= 100, "Tenant discovery must be bounded/keyset paged"
        cursor, limit = args
        return [{"id": tenant} for tenant in sorted((TENANT, OTHER))
                if cursor is None or tenant > str(cursor)][:limit]

    async def fetchval(self, sql, *args):
        flat = " ".join(sql.lower().split())
        if "to_regclass" in flat:
            table = str(args[0]).removeprefix("public.")
            return table if table in self.present else None
        if "pg_try_advisory_xact_lock" in flat:
            assert self.depth > 0 and "talky:media-hold:" in flat
            tenant = str(args[0])
            if tenant in self.busy:
                return False
            self.locked_tenant = tenant
            return True
        if "pg_try_advisory_lock" in flat:
            return True  # Existing overall scheduler lock; per-batch hold lock is separate.
        if "suspension_events" in flat:
            tenant = str(args[0])
            assert self.locked_tenant == tenant and self.depth > 0
            assert "$1" in flat and "target_id" in flat
            self.checked_tenant = tenant
            return tenant in self.holds
        if "count(" in flat:
            table, tenant, selected = self._select(sql, args)
            self.counts.append((table, tenant, args[1]))
            if self.hold_after_count == tenant:
                self.holds.add(tenant)
            return len(selected)
        raise AssertionError(f"Unexpected scalar SQL: {sql}")

    async def execute(self, sql, *args):
        flat = " ".join(sql.lower().split())
        if flat.startswith("set local") or flat.startswith("select set_config("):
            return "SELECT 1"
        if "pg_advisory_unlock" in flat:
            return "SELECT 1"
        assert "delete from" in flat, f"Unexpected mutation: {sql}"
        table, tenant, selected = self._select(sql, args)
        assert self.depth > 0 and self.locked_tenant == tenant and self.checked_tenant == tenant
        assert tenant not in self.holds, "A newly committed hold must prevent apply"
        assert len(args) == 3 and 0 < args[2] <= 5000
        assert "limit $3" in flat and "order by" in flat
        deleted = selected[:args[2]]
        deleted_ids = {row["id"] for row in deleted}
        self.rows[table] = [row for row in self.rows[table] if row["id"] not in deleted_ids]
        self.deletes.append((table, tenant, deleted_ids))
        return f"DELETE {len(deleted)}"


class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.leases = 0

    @asynccontextmanager
    async def acquire(self):
        self.leases += 1
        try:
            yield self.conn
        finally:
            self.leases -= 1


@pytest.fixture
def fixture(monkeypatch):
    for name in (
        "CLEANUP_DRY_RUN", "CLEANUP_PERSONAL_RETENTION_ENABLED", "CLEANUP_TRANSCRIPTS_DAYS",
        "CLEANUP_CONVERSATIONS_DAYS", "CLEANUP_GUARD_DECISIONS_DAYS", "CLEANUP_CALL_EVENTS_DAYS",
        "CLEANUP_CALL_LEGS_DAYS", "CLEANUP_STREAM_EVENTS_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker, "_utc_now", lambda: NOW, raising=False)
    conn = Connection()
    return conn, Pool(conn)


async def run(pool, *, dry_run=True, batch_size=2, max_batches=3):
    return await worker.cleanup_old_records(
        pool, dry_run=dry_run, batch_size=batch_size, max_batches=max_batches, batch_sleep_ms=0,
    )


def test_new_personal_retention_targets_have_proposed_windows_and_default_dry_run(fixture):
    targets = {target.table: target.days for target in worker._load_targets()}
    assert targets == WINDOWS, "F12-06 still lacks transcript/conversation/guard retention jobs"
    assert worker._dry_run_enabled() is True


async def test_dry_run_counts_both_tenants_with_explicit_scope_and_never_writes(fixture):
    conn, pool = fixture
    before, calls = deepcopy(conn.rows), deepcopy(conn.calls)
    summary = await run(pool)
    assert {table: summary[table] for table in TARGETS} == {table: 2 for table in TARGETS}
    assert conn.rows == before and conn.calls == calls and conn.deletes == [] and pool.leases == 0
    assert {tenant for _, tenant, _ in conn.counts} == {TENANT, OTHER}


async def test_explicit_apply_deletes_only_older_rows_and_preserves_cutoff_and_new_rows(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    calls = deepcopy(conn.calls)
    summary = await run(pool, dry_run=False)
    assert {table: summary[table] for table in TARGETS} == {table: 2 for table in TARGETS}
    for table in TARGETS:
        assert len(conn.rows[table]) == 4
        assert all(row["created_at"] >= NOW - timedelta(days=WINDOWS[table]) for row in conn.rows[table])
    assert conn.calls == calls and pool.leases == 0


async def test_existing_telemetry_apply_does_not_enable_new_personal_deletes(fixture):
    conn, pool = fixture
    before = deepcopy(conn.rows)
    summary = await run(pool, dry_run=False)
    assert PERSONAL <= set(summary["dry_run_tables"])
    assert all(conn.rows[table] == before[table] for table in PERSONAL)
    assert all(table not in PERSONAL for table, _, _ in conn.deletes)


async def test_missing_stream_events_is_reported_and_does_not_abort_other_retention(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    conn.present.remove("stream_events")
    summary = await run(pool, dry_run=False)
    assert "stream_events" in summary["missing_tables"]
    assert summary["conversations"] == 2 and summary["transcripts"] == 2
    assert not any(table == "stream_events" for table, _, _ in conn.counts + conn.deletes)


@pytest.mark.parametrize("blocked", ["held", "busy"])
async def test_active_hold_and_busy_hold_lock_leave_that_tenants_data_untouched(fixture, monkeypatch, blocked):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    (conn.holds if blocked == "held" else conn.busy).add(OTHER)
    before = deepcopy(conn.rows)
    summary = await run(pool, dry_run=False)
    assert OTHER in summary["held_tenants" if blocked == "held" else "busy_tenants"]
    assert all(tenant == TENANT for _, tenant, _ in conn.deletes)
    for table in TARGETS:
        assert [row for row in conn.rows[table] if row.get("tenant_id") == OTHER] == [
            row for row in before[table] if row.get("tenant_id") == OTHER
        ]


async def test_hold_created_after_preview_is_rechecked_inside_delete_transaction(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    conn.hold_after_count = TENANT
    summary = await run(pool, dry_run=False)
    assert TENANT in summary["held_tenants"]
    assert all(tenant != TENANT for _, tenant, _ in conn.deletes)


async def test_unavailable_hold_schema_refuses_apply_instead_of_deleting_unchecked(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    conn.present.remove("suspension_events")
    before = deepcopy(conn.rows)
    summary = await run(pool, dry_run=False)
    assert "suspension_events" in summary["missing_tables"]
    assert conn.rows == before and conn.deletes == []


async def test_legacy_drive_transcript_is_not_orphaned_by_table_retention(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    conn.rows["transcripts"][0]["drive_file_id"] = "synthetic-legacy-drive-id"
    original = deepcopy(conn.rows["transcripts"][0])
    summary = await run(pool, dry_run=False)
    assert original in conn.rows["transcripts"]
    assert summary["transcripts"] == 1


async def test_database_error_is_not_swallowed_as_a_missing_table(fixture):
    conn, pool = fixture
    conn.fail_table = "conversations"
    before = deepcopy(conn.rows)
    with pytest.raises(RuntimeError, match="synthetic database permission"):
        await run(pool)
    assert conn.rows == before and pool.leases == 0


async def test_batch_budget_is_per_table_across_tenants_not_reset_for_each_tenant(fixture, monkeypatch):
    conn, pool = fixture
    monkeypatch.setenv("CLEANUP_PERSONAL_RETENTION_ENABLED", "true")
    summary = await run(pool, dry_run=False, batch_size=1, max_batches=1)
    assert all(summary[table] <= 1 for table in TARGETS)
    assert all(sum(len(ids) for target, _, ids in conn.deletes if target == table) <= 1 for table in TARGETS)


@pytest.mark.parametrize("batch_size,max_batches", [(0, 1), (5001, 1), (1, 0), (1, 201)])
async def test_unbounded_or_invalid_work_limits_are_rejected_before_acquisition(fixture, batch_size, max_batches):
    conn, pool = fixture
    with pytest.raises(ValueError):
        await run(pool, batch_size=batch_size, max_batches=max_batches)
    assert pool.leases == 0 and conn.counts == [] and conn.deletes == []


@pytest.mark.parametrize("missing", ["recordings_s3", "tenant_recording_policy", "suspension_events", "tenants"])
async def test_recording_retention_skips_missing_schema_before_data_query(fixture, missing):
    from app.workers import recording_retention_purge

    conn, pool = fixture
    conn.present.update({"recordings_s3", "tenant_recording_policy"})
    conn.present.remove(missing)

    async def no_data_query(sql, *args):
        raise AssertionError("Recording purge queried missing schema before preflight")

    conn.fetch = no_data_query
    result = await recording_retention_purge.select_expired_recordings(pool, 10)
    assert result == [] and pool.leases == 0
