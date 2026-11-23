"""An ended sign-in refused at /auth/refresh is not a token theft.

Production 23 Nov 2026: after the server-side sign-in ended (idle 30 min), the
route rotated the token, found the session gone, revoked the family and left the
browser's cookie alone. Every later attempt with that same cookie (used + family
revoked) was logged as ``refresh.reuse_detected`` at ERROR: a false theft report.
The real first refusal wrote no line at all.

Only labels and log lines change: when a token is accepted, rotated or a family
revoked is pinned here as unchanged.
"""
from __future__ import annotations

import importlib.util
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.endpoints.auth import refresh as ep
from app.core.security import refresh_tokens as rt

_spec = importlib.util.spec_from_file_location(
    "_rot_tests_for_ended_signin", Path(__file__).with_name("test_refresh_rotation.py")
)
_rot_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rot_tests)

RT_LOGGER = "app.core.security.refresh_tokens"
EP_LOGGER = "app.api.v1.endpoints.auth.refresh"


class Conn(_rot_tests.StubConn):
    """StubConn plus the statements refresh.py issues; records every statement."""

    def __init__(self) -> None:
        super().__init__()
        self.statements: list[str] = []

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split())
        self.statements.append(s)
        if s.startswith("SELECT user_id,tenant_id FROM refresh_tokens"):
            for row in self.rows.values():
                if row["token_hash"] == args[0]:
                    return {"user_id": row["user_id"], "tenant_id": row["tenant_id"]}
            return None
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        s = " ".join(sql.split())
        self.statements.append(s)
        if "revoked_reason = 'expired_with_subsequent_use'" in s:
            family_id, now = args
            for row in self.rows.values():
                if row["family_id"] == family_id and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    row["revoked_reason"] = "expired_with_subsequent_use"
            return "UPDATE *"
        return await super().execute(sql, *args)

    def writes(self) -> list[str]:
        return [s for s in self.statements if not s.startswith("SELECT")]


def _records(caplog, name, needle):
    return [r for r in caplog.records if r.name == name and needle in r.getMessage()]


def _request():
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/refresh",
                    "headers": [(b"user-agent", b"unit-test")],
                    "client": ("203.0.113.5", 5555), "query_string": b""})


class _Client:
    pool = None


@pytest.fixture
def conn():
    return Conn()


@pytest.fixture
def route(conn, monkeypatch):
    """The real route function over the fake store; the session check answers 'ended'."""

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id):
        yield conn

    async def no_lock(c, user_id):
        return None

    async def principal(c, user_id, tenant_id=None):
        return {"id": str(user_id), "email": "user@example.test", "role": "user",
                "tenant_id": str(tenant_id) if tenant_id else None}

    async def session_gone(c, session_id, user_id=None):
        return None

    monkeypatch.setattr(ep, "acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(ep, "lock_user_credential_issuance", no_lock)
    monkeypatch.setattr(ep, "load_current_principal", principal)
    monkeypatch.setattr(ep, "get_session_by_id", session_gone)
    handler = getattr(ep.refresh, "__wrapped__", ep.refresh)

    async def call(cookie):
        try:
            await handler(request=_request(), response=Response(), db_client=_Client(), talky_rt=cookie)
        except HTTPException as exc:
            return exc.status_code, exc.detail
        return 200, None

    return call


@pytest.mark.asyncio
async def test_ended_signin_presented_three_times_is_three_refusals_and_no_theft_report(conn, route, caplog):
    caplog.set_level(logging.DEBUG)
    user_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    t1, _, family_id = await rt.issue_initial_refresh_token(conn, user_id=user_id, session_id=session_id)

    caplog.clear()
    first = await route(t1)
    assert first[0] == 401 and "login session has ended" in first[1]
    ended = _records(caplog, EP_LOGGER, "refresh.session_ended")
    assert len(ended) == 1 and ended[0].levelno == logging.INFO
    assert user_id in ended[0].getMessage()
    assert t1 not in ended[0].getMessage()

    caplog.clear()
    second = await route(t1)
    third = await route(t1)
    assert second[0] == 401 and third[0] == 401

    assert not [r for r in caplog.records if "refresh.reuse_detected" in r.getMessage()]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    warned = _records(caplog, RT_LOGGER, "refresh.used_token_of_ended_signin")
    assert len(warned) == 2 and all(r.levelno == logging.WARNING for r in warned)
    # the family stays revoked with the reason the route gave it
    assert all(r["revoked_reason"] == "logout" for r in conn.rows.values() if r["family_id"] == family_id)


@pytest.mark.asyncio
async def test_real_theft_replay_of_a_used_token_still_revokes_family_and_logs_one_error(conn, caplog):
    caplog.set_level(logging.DEBUG)
    user_id = str(uuid.uuid4())
    raw, _, family_id = await rt.issue_initial_refresh_token(conn, user_id=user_id)
    successor, _ = await rt.rotate_refresh_token(conn, presented_token=raw)
    await rt.rotate_refresh_token(conn, presented_token=successor)  # the owner moved on
    original = next(r for r in conn.rows.values() if r["token_hash"] == rt._hash_token(raw))
    original["used_at"] -= timedelta(seconds=rt.REFRESH_REUSE_GRACE_SECONDS + 1)
    assert all(r["revoked_at"] is None for r in conn.rows.values())  # family NOT revoked yet

    caplog.clear()
    assert await rt.rotate_refresh_token(conn, presented_token=raw) is None

    family = [r for r in conn.rows.values() if r["family_id"] == family_id]
    assert family and all(r["revoked_reason"] == "reuse_detected" for r in family)
    errors = _records(caplog, RT_LOGGER, "refresh.reuse_detected")
    assert len(errors) == 1 and errors[0].levelno == logging.ERROR
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == errors


@pytest.mark.asyncio
async def test_race_allowance_is_unchanged(conn, caplog):
    caplog.set_level(logging.DEBUG)
    raw, token_id, family_id = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    winner, _ = await rt.rotate_refresh_token(conn, presented_token=raw)
    used_at = conn.rows[token_id]["used_at"]

    caplog.clear()
    loser = await rt.rotate_refresh_token(conn, presented_token=raw)

    assert loser is not None and loser[0] != winner
    assert conn.rows[token_id]["used_at"] == used_at
    assert all(r["revoked_at"] is None for r in conn.rows.values() if r["family_id"] == family_id)
    assert len(_records(caplog, RT_LOGGER, "refresh.concurrent_rotation")) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_used_token_of_a_fully_revoked_family_changes_no_row_and_is_not_reported_as_theft(conn, caplog):
    caplog.set_level(logging.DEBUG)
    raw, token_id, family_id = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    await rt.rotate_refresh_token(conn, presented_token=raw)
    await rt.revoke_family_by_token(conn, presented_token=raw, reason="logout")
    # Minutes later, as in production: well outside the allowance for two racing requests.
    conn.rows[token_id]["used_at"] -= timedelta(minutes=5)
    before = {k: dict(v) for k, v in conn.rows.items()}
    conn.statements.clear()
    caplog.clear()

    assert await rt.rotate_refresh_token(conn, presented_token=raw) is None

    # The protective statement still runs, exactly as before; it finds nothing to revoke.
    assert len(conn.writes()) == 1 and "revoked_reason = 'reuse_detected'" in conn.writes()[0]
    assert conn.rows == before
    assert len(_records(caplog, RT_LOGGER, "refresh.used_token_of_ended_signin")) == 1
    assert not _records(caplog, RT_LOGGER, "refresh.revoked_token_presented")   # its own name
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_a_used_revoked_token_with_a_live_sibling_still_revokes_it_and_reports_theft(conn, caplog):
    """If anything in the family is still valid, a used token coming back is treated as theft,
    even when that presented token is itself already revoked."""
    caplog.set_level(logging.DEBUG)
    raw, token_id, family_id = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    successor, _ = await rt.rotate_refresh_token(conn, presented_token=raw)
    original = conn.rows[token_id]
    original["used_at"] -= timedelta(seconds=rt.REFRESH_REUSE_GRACE_SECONDS + 1)
    original["revoked_at"] = datetime.now(timezone.utc)      # only this one token is revoked
    original["revoked_reason"] = "logout"
    live = [r for r in conn.rows.values() if r["family_id"] == family_id and r["revoked_at"] is None]
    assert len(live) == 1                                      # the successor is still valid

    caplog.clear()
    assert await rt.rotate_refresh_token(conn, presented_token=raw) is None

    assert all(r["revoked_at"] is not None for r in conn.rows.values() if r["family_id"] == family_id)
    assert live[0]["revoked_reason"] == "reuse_detected"
    errors = _records(caplog, RT_LOGGER, "refresh.reuse_detected")
    assert len(errors) == 1 and errors[0].levelno == logging.ERROR
    assert await rt.rotate_refresh_token(conn, presented_token=successor) is None   # the successor is dead too


@pytest.mark.asyncio
async def test_unknown_and_expired_tokens_behave_as_before(conn, caplog):
    caplog.set_level(logging.DEBUG)
    assert await rt.rotate_refresh_token(conn, presented_token="not-a-real-token") is None
    assert len(_records(caplog, RT_LOGGER, "refresh.unknown_token_presented")) == 1

    raw, token_id, family_id = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    conn.rows[token_id]["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    caplog.clear()
    assert await rt.rotate_refresh_token(conn, presented_token=raw) is None
    assert len(_records(caplog, RT_LOGGER, "refresh.expired_family_revoked")) == 1
    assert conn.rows[token_id]["revoked_reason"] == "expired_with_subsequent_use"
    assert conn.rows[token_id]["used_at"] is None
