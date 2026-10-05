"""Atomic health acknowledgement and private generation proof (synthetic DB ports)."""

import asyncio
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
        "access_token_encrypted": "synthetic-ciphertext",
        "refresh_token_encrypted": "synthetic-refresh",
        "external_account_id": None,
        "token_expires_at": None,
        "last_refreshed_at": "2026-10-05T00:00:00Z",
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

    @asynccontextmanager
    async def transaction(self):
        before = deepcopy((self.rows, self.parent))
        try:
            yield
        except BaseException:
            self.rows, self.parent = before
            self.events.append("rollback")
            raise
        else:
            self.committed = True
            self.events.append("commit")

    async def execute(self, sql, *args):
        assert "SET LOCAL app.current_tenant_id" in sql

    async def fetchrow(self, sql, *args):
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
        return sorted(
            (row for row in self.rows if row["status"] == "active"),
            key=lambda row: row["last_refreshed_at"] or "~",
            reverse=True,
        )[:2]

    async def fetchval(self, sql, *args):
        if "UPDATE connector_accounts" in sql:
            self.events.append("account_update")
            assert args == (UUID(ACCOUNT), UUID(CONNECTOR), UUID(TENANT))
            for row in self.rows:
                if row["id"] == ACCOUNT:
                    row["status"] = "expired"
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
        "parent_lock",
        "all_accounts_lock",
        "fresh_active_read",
        "account_update",
        "parent_update",
        "commit",
    ]


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
@pytest.mark.parametrize("fault", ["write", "zero_ack"])
async def test_failed_parent_ack_rolls_back_account_expiry(fault):
    conn = Connection(fault=fault)
    assert await mark(conn) is False
    assert conn.rows[0]["status"] == conn.parent == "active"
    assert not conn.committed and conn.events[-1] == "rollback"


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
    await conn.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert conn.events[-1] == "rollback"
    assert conn.rows[0]["status"] == conn.parent == "active"


@pytest.mark.asyncio
async def test_deadline_cannot_acknowledge_or_fall_back_to_broad_update(monkeypatch):
    monkeypatch.setattr(inbox, "_EMAIL_HEALTH_TIMEOUT_SECONDS", 0.01)
    conn = Connection(fault="wait")
    assert not await mark(conn)
    assert conn.events[-1] == "rollback"
    assert conn.rows[0]["status"] == conn.parent == "active"
