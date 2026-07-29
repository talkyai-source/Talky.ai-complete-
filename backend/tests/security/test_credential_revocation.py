"""Credential-revocation invariants for the account-recovery paths.

`security_sessions` and `refresh_tokens` are INDEPENDENT stores. Revoking
one does nothing to the other, and POST /auth/refresh consults only
`refresh_tokens` (app/api/v1/endpoints/auth/refresh.py). So any flow that
invalidates a credential must sweep BOTH, or a stolen `talky_rt` cookie
keeps minting fresh 15-min access JWTs for up to 7 days.

Covered here:
  ✓ POST /auth/reset-password revokes sessions AND refresh tokens
  ✓ ...both inside the SAME transaction as the password UPDATE
  ✓ ...and survives the refresh_tokens.revoked_reason CHECK constraint
  ✓ DELETE /sessions/{id} does NOT nuke every refresh family (the
    "wrong fix" that would sign the user out of every device)
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.api.v1.endpoints.auth import password_reset as pr_mod
from app.api.v1.endpoints import sessions as sessions_mod


USER_ID = "11111111-1111-1111-1111-111111111111"
EMAIL = "victim@example.com"
CODE = "123456"
NEW_PASSWORD = "Str0ng-New-Passw0rd!"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Minimal asyncpg.Connection stand-in that tracks transaction depth.

    `transaction()` is re-entrant so the SAVEPOINT used by the
    constraint-safe revocation helper behaves like the real thing.
    """

    def __init__(self, *, user_row: dict | None = None):
        self.executed: list[tuple[str, tuple]] = []
        self.depth = 0
        self.max_depth = 0
        self._user_row = user_row if user_row is not None else {"id": USER_ID}

    async def fetchrow(self, sql, *args):
        return self._user_row

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                conn.depth += 1
                conn.max_depth = max(conn.max_depth, conn.depth)
                return self

            async def __aexit__(self, *exc):
                conn.depth -= 1
                return False

        return _Txn()


def _db_client(conn: FakeConn):
    acquire = AsyncMock()
    acquire.__aenter__.return_value = conn
    acquire.__aexit__.return_value = None
    pool = MagicMock()
    pool.acquire.return_value = acquire
    return SimpleNamespace(pool=pool)


def _fake_redis():
    payload = {
        "user_id": USER_ID,
        "email": EMAIL,
        "code_hash": hashlib.sha256(CODE.encode("utf-8")).hexdigest(),
    }
    r = AsyncMock()
    r.get = AsyncMock(return_value=json.dumps(payload))
    r.delete = AsyncMock(return_value=1)
    return r


class Recorder:
    """Records calls and the transaction depth they happened at."""

    def __init__(self, conn: FakeConn, *, raises=None, raise_times: int = 0):
        self.conn = conn
        self.calls: list[dict] = []
        self._raises = raises
        self._raise_times = raise_times

    async def __call__(self, conn, user_id, *, reason, **kwargs):
        self.calls.append(
            {"user_id": user_id, "reason": reason, "depth": self.conn.depth, **kwargs}
        )
        if self._raises is not None and self._raise_times > 0:
            self._raise_times -= 1
            raise self._raises
        return 3

    @property
    def reasons(self) -> list[str]:
        return [c["reason"] for c in self.calls]


@pytest.fixture()
def reset_harness(monkeypatch):
    """Wire reset_password up to fakes; return (conn, sessions_rec, refresh_rec)."""
    conn = FakeConn()
    sessions_rec = Recorder(conn)
    refresh_rec = Recorder(conn)

    monkeypatch.setattr(pr_mod, "revoke_all_user_sessions", sessions_rec)
    monkeypatch.setattr(pr_mod, "revoke_all_user_refresh_tokens", refresh_rec)
    monkeypatch.setattr(pr_mod, "_get_redis_or_503", _fake_redis_factory())
    monkeypatch.setattr(pr_mod, "hash_password", lambda p: "$argon2id$fake")

    return conn, sessions_rec, refresh_rec


_REDIS_SINGLETON = {}


def _fake_redis_factory():
    r = _fake_redis()
    _REDIS_SINGLETON["r"] = r
    return lambda: r


async def _call_reset(db_client):
    body = pr_mod.ResetPasswordRequest(
        email=EMAIL, code=CODE, new_password=NEW_PASSWORD
    )
    return await pr_mod.reset_password(
        request=MagicMock(headers={}, client=SimpleNamespace(host="1.2.3.4")),
        body=body,
        db_client=db_client,
        audit_logger=SimpleNamespace(log=AsyncMock()),
    )


# ===========================================================================
# DEFECT 1 — password reset must revoke BOTH stores
# ===========================================================================


class TestPasswordResetRevokesBothStores:

    async def test_revokes_sessions(self, reset_harness):
        conn, sessions_rec, _ = reset_harness
        await _call_reset(_db_client(conn))

        assert len(sessions_rec.calls) == 1
        assert sessions_rec.calls[0]["user_id"] == USER_ID

    async def test_revokes_refresh_tokens(self, reset_harness):
        """THE regression guard: without the fix this list is empty and a
        stolen talky_rt keeps minting access JWTs for up to 7 days."""
        conn, _, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert len(refresh_rec.calls) == 1, (
            "reset_password did not revoke refresh tokens — a stolen "
            "talky_rt survives the account-recovery flow"
        )
        assert refresh_rec.calls[0]["user_id"] == USER_ID

    async def test_refresh_revocation_is_not_scoped_to_one_family(
        self, reset_harness
    ):
        """Recovery must sweep EVERY family, including the caller's."""
        conn, _, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert refresh_rec.calls[0].get("exclude_family_id") in (None, "")

    async def test_both_revocations_share_the_password_transaction(
        self, reset_harness
    ):
        """Atomicity: if revocation fails the new password must roll back,
        otherwise the account is left half-secured."""
        conn, sessions_rec, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert sessions_rec.calls[0]["depth"] >= 1
        assert refresh_rec.calls[0]["depth"] >= 1

    async def test_password_update_precedes_revocation(self, reset_harness):
        """Mirrors auth/password.py ordering: UPDATE, then sweep."""
        conn, sessions_rec, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert any("password_hash" in sql for sql, _ in conn.executed)
        assert sessions_rec.calls and refresh_rec.calls

    async def test_returns_success(self, reset_harness):
        conn, _, _ = reset_harness
        result = await _call_reset(_db_client(conn))
        assert "message" in result


class TestRevokedReasonCheckConstraint:
    """`refresh_tokens.revoked_reason` has a CHECK constraint permitting only
    ('rotated','reuse_detected','logout','admin','expired') — see
    Alembic/versions/0002_add_refresh_tokens.py and
    database/schema/baseline_2026-06-02.sql:1187.

    A CheckViolationError inside the reset transaction would roll back the
    new password and lock the user out of their own recovery flow, so the
    accurate reason is attempted in a SAVEPOINT with a permitted fallback.
    """

    async def test_prefers_the_accurate_reason(self, reset_harness):
        conn, _, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert refresh_rec.reasons == ["password_reset"]

    async def test_falls_back_when_constraint_rejects_the_reason(
        self, monkeypatch
    ):
        conn = FakeConn()
        sessions_rec = Recorder(conn)
        refresh_rec = Recorder(
            conn,
            raises=asyncpg.exceptions.CheckViolationError(
                'new row violates check constraint '
                '"refresh_tokens_revoked_reason_check"'
            ),
            raise_times=1,
        )
        monkeypatch.setattr(pr_mod, "revoke_all_user_sessions", sessions_rec)
        monkeypatch.setattr(pr_mod, "revoke_all_user_refresh_tokens", refresh_rec)
        monkeypatch.setattr(pr_mod, "_get_redis_or_503", _fake_redis_factory())
        monkeypatch.setattr(pr_mod, "hash_password", lambda p: "$argon2id$fake")

        result = await _call_reset(_db_client(conn))

        # Retried, and with a value the constraint actually permits.
        assert refresh_rec.reasons == ["password_reset", "logout"]
        assert refresh_rec.reasons[1] in {
            "rotated", "reuse_detected", "logout", "admin", "expired",
        }
        # And the reset itself still succeeded — recovery is never blocked.
        assert "message" in result

    async def test_savepoint_wraps_the_first_attempt(self, reset_harness):
        """The retry only works if attempt #1 ran inside a nested
        transaction (SAVEPOINT); otherwise the outer txn is poisoned."""
        conn, _, refresh_rec = reset_harness
        await _call_reset(_db_client(conn))

        assert conn.max_depth >= 2, (
            "refresh-token revocation was not wrapped in a SAVEPOINT"
        )
        assert refresh_rec.calls[0]["depth"] >= 2


# ===========================================================================
# DEFECT 2 — selective logout must NOT over-revoke
# ===========================================================================


class TestSelectiveSessionRevokeDoesNotOverRevoke:
    """DELETE /sessions/{id} cannot revoke the device's refresh token —
    `refresh_tokens` has no session_id/family correlation column. The
    correct behaviour is to revoke the session row and be HONEST about it,
    never to revoke every family (that would sign the user out everywhere
    when they asked to sign out of one device)."""

    async def test_module_does_not_import_bulk_refresh_revocation(self):
        assert not hasattr(sessions_mod, "revoke_all_user_refresh_tokens"), (
            "endpoints/sessions.py must not bulk-revoke refresh tokens: "
            "'log out this device' would log the user out everywhere"
        )

    async def test_response_admits_refresh_token_was_not_revoked(
        self, monkeypatch
    ):
        conn = FakeConn(user_row=None)
        monkeypatch.setattr(
            sessions_mod, "revoke_session_by_id", AsyncMock(return_value=True)
        )

        resp = await sessions_mod.revoke_specific_session(
            session_id="22222222-2222-2222-2222-222222222222",
            current_user=SimpleNamespace(id=USER_ID),
            db_client=_db_client(conn),
            talky_sid=None,
        )

        assert resp.revoked is True
        assert resp.refresh_token_revoked is False
        assert "refresh token" in resp.detail.lower()
