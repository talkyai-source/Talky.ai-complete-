"""Atomic health acknowledgement and private generation proof (synthetic DB ports)."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.infrastructure.assistant.tools import inbox
from app.services.connector_resolver import _authorization_snapshot_for_row

TENANT = "00000000-0000-0000-0000-000000000001"
CONNECTOR = "00000000-0000-0000-0000-000000000002"
ACCOUNT = "00000000-0000-0000-0000-000000000003"
OTHER = "00000000-0000-0000-0000-000000000004"


def account(**changes):
    return {
        "id": ACCOUNT,
        "status": "active",
        "refresh_state": "idle",
        "access_token_encrypted": "synthetic-ciphertext",
        "refresh_token_encrypted": "synthetic-refresh",
        "external_account_id": None,
        "token_expires_at": None,
        "last_refreshed_at": "2026-10-05T00:00:00Z",
        "created_at": "2026-10-05T00:00:00Z",
        "credential_generation": 1,
        **changes,
    }


def proof(row=None):
    return _authorization_snapshot_for_row(TENANT, CONNECTOR, "gmail", row or account())


class Connection:
    def __init__(self, rows=None, *, fault=None):
        self.rows = deepcopy(rows or [account()])
        self.parent = "active"
        self.fault = fault
        self.events = []
        self.entered = asyncio.Event()
        self.committed = False
        self.depth = 0
        self.audit_lock = False
        self.bypass = ""
        self.audits = []

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy((self.rows, self.parent, self.audits, self.bypass))
        self.depth += 1
        try:
            yield
        except BaseException:
            self.rows, self.parent, self.audits, self.bypass = before
            self.events.append("rollback")
            raise
        else:
            if self.depth == 1:
                self.committed = True
                self.events.append("commit")
        finally:
            self.depth -= 1
            if not self.depth:
                self.audit_lock = False

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            assert self.is_in_transaction()
            self.audit_lock = True
            self.events.append("audit_lock")
            return
        if "set_config('app.bypass_rls'" in sql:
            self.bypass = args[0] if args else "on"
            return
        assert "SET LOCAL app.current_tenant_id" in sql

    async def fetchrow(self, sql, *args):
        if "FROM audit_logs" in sql:
            assert self.audit_lock and self.bypass == "on"
            return None
        assert "FROM connectors" in sql and "FOR UPDATE" in sql
        assert args == (UUID(CONNECTOR), UUID(TENANT), "gmail")
        self.events.append("parent_lock")
        self.entered.set()
        if self.fault == "wait":
            await asyncio.Event().wait()
        return {"id": CONNECTOR} if self.parent == "active" else None

    async def fetch(self, sql, *args):
        assert args == (UUID(CONNECTOR), UUID(TENANT))
        if "FOR UPDATE" in sql:
            assert "ORDER BY id" in sql and "LIMIT" not in sql and "status=" not in sql
            self.events.append("all_accounts_lock")
            return [{"id": row["id"]} for row in self.rows]
        self.events.append("fresh_active_read")
        return deepcopy(sorted(
            (row for row in self.rows if row["status"] == "active"),
            key=lambda row: row["created_at"] or "~",
            reverse=True,
        )[:2])

    async def fetchval(self, sql, *args):
        if "FROM pg_locks" in sql:
            return self.audit_lock
        if "current_setting('app.bypass_rls'" in sql:
            return self.bypass
        if "INSERT INTO audit_logs" in sql:
            assert self.audit_lock and self.is_in_transaction() and self.bypass == ""
            if self.fault == "audit":
                raise OSError("synthetic audit write fault")
            self.audits.append({
                "event_id": args[0], "actor_type": args[6], "tenant_id": args[8],
                "resource_id": args[10], "action": args[11], "metadata": json.loads(args[15]),
            })
            self.events.append("audit_insert")
            return args[0]
        if "UPDATE connector_accounts" in sql:
            self.events.append("account_update")
            assert args == (UUID(ACCOUNT), UUID(CONNECTOR), UUID(TENANT))
            for row in self.rows:
                if row["id"] == ACCOUNT:
                    row["status"] = "expired"
                    row["credential_generation"] += 1
            return UUID(ACCOUNT)
        self.events.append("parent_update")
        if self.fault == "write":
            raise OSError("synthetic write fault")
        if self.fault == "zero_ack":
            return None
        self.parent = "expired"
        return UUID(CONNECTOR)


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, **kwargs):
        assert kwargs["timeout"] == inbox._EMAIL_HEALTH_TIMEOUT_SECONDS
        yield self.conn


async def mark(conn, authorization=None):
    return await inbox._mark_email_authorization_expired(
        SimpleNamespace(pool=Pool(conn)),
        TENANT,
        CONNECTOR,
        authorization=authorization if authorization is not None else proof(),
    )


def test_generation_normalizes_json_timestamps_and_asyncpg_datetimes_privately():
    stored = account(token_expires_at="2026-10-06T05:00:00+05:00")
    from_pg = {
        **stored,
        "id": UUID(ACCOUNT),
        "last_refreshed_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
        "token_expires_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
    }
    value = proof(stored)
    assert value == proof(from_pg)
    assert "synthetic" not in repr(value) and value.generation_sha256 not in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.generation_sha256 = "changed"


@pytest.mark.parametrize("change", ["missing", "malformed_time", "malformed_token"])
def test_malformed_generation_has_no_proof(change):
    row = account()
    if change == "missing":
        del row["last_refreshed_at"]
    elif change == "malformed_time":
        row["last_refreshed_at"] = "not-a-timestamp"
    else:
        row["access_token_encrypted"] = object()
    assert proof(row) is None


@pytest.mark.asyncio
async def test_current_generation_expiry_is_acknowledged_only_after_commit():
    conn = Connection()
    assert await mark(conn) is True
    assert conn.committed and conn.parent == conn.rows[0]["status"] == "expired"
    assert conn.events == [
        "audit_lock",
        "parent_lock",
        "all_accounts_lock",
        "fresh_active_read",
        "account_update",
        "parent_update",
        "audit_insert",
        "commit",
    ]
    assert conn.rows[0]["credential_generation"] == 2
    assert len(conn.audits) == 1
    audit = conn.audits[0]
    assert audit["action"] == "connector.authorization_expired"
    assert audit["actor_type"] == "system" and audit["tenant_id"] == UUID(TENANT)
    assert audit["resource_id"] == UUID(CONNECTOR)
    assert audit["metadata"]["account_id"] == ACCOUNT
    assert audit["metadata"]["generation"] == 2
    assert audit["metadata"]["local_status"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [account(access_token_encrypted="synthetic-new-token")],
        [account(), account(id=OTHER, last_refreshed_at="2099-01-01T00:00:00Z")],
        [account(), account(id=OTHER)],
        [account(status="revoked")],
    ],
)
async def test_stale_ambiguous_or_revoked_generation_cannot_expire(rows):
    conn = Connection(rows)
    assert await mark(conn) is False
    assert "account_update" not in conn.events and conn.parent == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["write", "zero_ack", "audit"])
async def test_failed_parent_ack_rolls_back_account_expiry(fault):
    conn = Connection(fault=fault)
    assert await mark(conn) is False
    assert conn.rows[0]["status"] == conn.parent == "active"
    assert not conn.committed and conn.events[-1] == "rollback"
    assert conn.rows[0]["credential_generation"] == 1 and not conn.audits


@pytest.mark.asyncio
async def test_missing_or_foreign_proof_has_no_database_effect():
    conn = Connection()
    db = SimpleNamespace(pool=Pool(conn))
    assert not await inbox._mark_email_authorization_expired(db, TENANT, CONNECTOR)
    assert not await inbox._mark_email_authorization_expired(
        db, OTHER, CONNECTOR, authorization=proof()
    )
    assert not await inbox._mark_email_authorization_expired(
        SimpleNamespace(), TENANT, CONNECTOR, authorization=proof()
    )
    assert not conn.events


@pytest.mark.asyncio
async def test_cancel_during_lock_is_transparent_and_rolls_back():
    conn = Connection(fault="wait")
    task = asyncio.create_task(mark(conn))
    entered = asyncio.create_task(conn.entered.wait())
    try:
        done, _ = await asyncio.wait({task, entered}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            pytest.fail(f"Expiry completed before the lock barrier: {task.result()!r}")
        assert entered in done, "Expiry never reached the lock barrier"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        entered.cancel()
        await asyncio.gather(task, entered, return_exceptions=True)
    assert conn.events[-1] == "rollback"
    assert conn.rows[0]["status"] == conn.parent == "active"
    assert conn.rows[0]["credential_generation"] == 1 and not conn.audits


@pytest.mark.asyncio
async def test_deadline_cannot_acknowledge_or_fall_back_to_broad_update(monkeypatch):
    monkeypatch.setattr(inbox, "_EMAIL_HEALTH_TIMEOUT_SECONDS", 0.01)
    conn = Connection(fault="wait")
    assert not await asyncio.wait_for(mark(conn), timeout=1)
    assert conn.entered.is_set(), "Deadline must interrupt the actual lock wait"
    assert conn.events == ["audit_lock", "parent_lock", "rollback"]
    assert conn.events[-1] == "rollback"
    assert conn.rows[0]["status"] == conn.parent == "active"
    assert conn.rows[0]["credential_generation"] == 1 and not conn.audits
