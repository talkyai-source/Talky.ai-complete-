"""Unit tests for refresh token rotation + reuse detection.

Uses an in-memory stub conn that mimics the asyncpg.Connection surface
the service actually touches (fetchrow / execute / transaction()).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.security import refresh_tokens as rt


class _StubTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class StubConn:
    """Minimal asyncpg-shaped conn that stores rows in a dict keyed by id."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, dict[str, Any]] = {}

    def transaction(self):
        return _StubTransaction()

    async def fetchrow(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO refresh_tokens"):
            return self._insert(args, returning=("id", "family_id"))
        if sql_norm.startswith("SELECT id, family_id, user_id"):
            (token_hash,) = args
            for row in self.rows.values():
                if row["token_hash"] == token_hash:
                    return row
            return None
        raise AssertionError(f"unhandled fetchrow: {sql_norm[:80]}")

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("UPDATE refresh_tokens SET used_at"):
            (row_id, now) = args
            self.rows[row_id]["used_at"] = now
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE refresh_tokens SET revoked_at = $2, revoked_reason = 'reuse_detected'"):
            (family_id, now) = args
            n = 0
            for row in self.rows.values():
                if row["family_id"] == family_id and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    row["revoked_reason"] = "reuse_detected"
                    n += 1
            return f"UPDATE {n}"
        if sql_norm.startswith("UPDATE refresh_tokens SET revoked_at = $2, revoked_reason = 'expired'"):
            (row_id, now) = args
            row = self.rows.get(row_id)
            if row and row["revoked_at"] is None:
                row["revoked_at"] = now
                row["revoked_reason"] = "expired"
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE refresh_tokens SET revoked_at = $2, revoked_reason = $3 WHERE family_id ="):
            presented_hash, now, reason = args
            target_family = None
            for row in self.rows.values():
                if row["token_hash"] == presented_hash:
                    target_family = row["family_id"]
                    break
            if target_family is None:
                return "UPDATE 0"
            for row in self.rows.values():
                if row["family_id"] == target_family and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    row["revoked_reason"] = reason
            return "UPDATE *"
        if sql_norm.startswith("INSERT INTO refresh_tokens"):
            self._insert(args, returning=None)
            return "INSERT 0 1"
        raise AssertionError(f"unhandled execute: {sql_norm[:120]}")

    def _insert(self, args, *, returning):
        if len(args) == 7:
            user_id, tenant_id, token_hash, issued_at, expires_at, ip, user_agent = args
            family_id = uuid.uuid4()
            parent_id = None
        else:
            (family_id, user_id, tenant_id, token_hash, parent_id,
             issued_at, expires_at, ip, user_agent) = args
        new_id = uuid.uuid4()
        self.rows[new_id] = {
            "id": new_id,
            "family_id": family_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "token_hash": token_hash,
            "parent_id": parent_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "used_at": None,
            "revoked_at": None,
            "revoked_reason": None,
            "ip": ip,
            "user_agent": user_agent,
        }
        if returning:
            return {k: self.rows[new_id][k] for k in returning}
        return None


@pytest.fixture
def conn():
    return StubConn()


def _patch_initial_insert(monkeypatch, conn: StubConn):
    """No-op now that StubConn handles both 7-arg and 9-arg INSERT shapes."""
    return None


@pytest.mark.asyncio
async def test_issue_initial_creates_family(conn, monkeypatch):
    _patch_initial_insert(monkeypatch, conn)
    raw, token_id, family_id = await rt.issue_initial_refresh_token(
        conn, user_id=str(uuid.uuid4()), tenant_id=None, ip="1.2.3.4", user_agent="ua"
    )
    assert raw  # non-empty
    row = conn.rows[token_id]
    assert row["family_id"] == family_id
    assert row["parent_id"] is None
    assert row["used_at"] is None
    assert row["revoked_at"] is None


@pytest.mark.asyncio
async def test_rotation_marks_consumed_and_extends_family(conn, monkeypatch):
    _patch_initial_insert(monkeypatch, conn)
    user_id = str(uuid.uuid4())
    raw, token_id, family_id = await rt.issue_initial_refresh_token(
        conn, user_id=user_id
    )

    result = await rt.rotate_refresh_token(conn, presented_token=raw)
    assert result is not None
    new_raw, claims = result
    assert new_raw != raw
    assert claims["user_id"] == user_id
    assert claims["family_id"] == str(family_id)

    # Old row marked consumed (used_at) but NOT revoked — that distinction
    # is what lets the reuse-detection branch see a replay later.
    assert conn.rows[token_id]["used_at"] is not None
    assert conn.rows[token_id]["revoked_at"] is None

    # New row exists in same family with parent_id pointing back.
    new_rows = [r for r in conn.rows.values() if r["id"] != token_id]
    assert len(new_rows) == 1
    successor = new_rows[0]
    assert successor["family_id"] == family_id
    assert successor["parent_id"] == token_id


@pytest.mark.asyncio
async def test_reuse_detection_revokes_entire_family(conn, monkeypatch):
    _patch_initial_insert(monkeypatch, conn)
    raw, _, family_id = await rt.issue_initial_refresh_token(
        conn, user_id=str(uuid.uuid4())
    )

    # First rotation succeeds.
    first = await rt.rotate_refresh_token(conn, presented_token=raw)
    assert first is not None
    _new_raw, _ = first

    # Replaying the original token a second time must trip reuse detection.
    second = await rt.rotate_refresh_token(conn, presented_token=raw)
    assert second is None

    # Every row in the family is now revoked.
    family_rows = [r for r in conn.rows.values() if r["family_id"] == family_id]
    assert family_rows
    assert all(r["revoked_at"] is not None for r in family_rows)
    assert any(r["revoked_reason"] == "reuse_detected" for r in family_rows)


@pytest.mark.asyncio
async def test_unknown_token_returns_none(conn):
    result = await rt.rotate_refresh_token(conn, presented_token="not-a-real-token")
    assert result is None


@pytest.mark.asyncio
async def test_revoke_family_by_token_clears_all_rows(conn, monkeypatch):
    _patch_initial_insert(monkeypatch, conn)
    raw, _, family_id = await rt.issue_initial_refresh_token(
        conn, user_id=str(uuid.uuid4())
    )
    await rt.revoke_family_by_token(conn, presented_token=raw, reason="logout")
    family_rows = [r for r in conn.rows.values() if r["family_id"] == family_id]
    assert all(r["revoked_at"] is not None for r in family_rows)
    assert all(r["revoked_reason"] == "logout" for r in family_rows)
