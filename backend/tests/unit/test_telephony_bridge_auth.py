"""Auth-gate tests for the telephony bridge origination endpoint.

Covers the dual-path auth on ``POST /sip/telephony/call`` (and the shared
``require_internal_or_tenant`` helper):

  (a) valid internal service token + arbitrary body.tenant_id  → allowed
  (b) authed user, body.tenant_id == their tenant (or omitted)  → allowed
  (c) authed user, body.tenant_id != their tenant               → 403
  (d) no token + no authenticated user                          → 401

Built like ``test_csrf_middleware`` — plain Starlette ``Request`` objects,
no TestClient (avoids the starlette/httpx version mismatch in this env).
The adapter/CallGuard are never reached: for the *allowed* cases the auth
gate passes and the handler fails downstream on the un-connected adapter
(status != 401/403), which is exactly what proves the gate let it through.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.security.internal_auth import (
    require_internal_or_tenant,
    resolve_call_tenant,
)

_TOKEN = "s3cret-internal-token-value"


def _request(*, headers: dict[str, str] | None = None, tenant_id: str | None = None) -> Request:
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("api.talkleeai.com", 443),
        "path": "/api/v1/sip/telephony/call",
        "raw_path": b"/api/v1/sip/telephony/call",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 0),
        "state": {},
    }
    req = Request(scope)
    if tenant_id is not None:
        req.state.tenant_id = tenant_id
    return req


# ── The shared helper (authoritative a/b/c/d) ────────────────────────────


def test_a_internal_token_allows_arbitrary_body_tenant(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _request(headers={"x-internal-service-token": _TOKEN})
    # Dialer path: body may name ANY tenant and it is honoured as-is.
    assert resolve_call_tenant(req, "tenant-B") == "tenant-B"


def test_b_user_matching_tenant_allowed(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request(tenant_id="tenant-A")
    # body omitted → JWT tenant
    assert resolve_call_tenant(req, None) == "tenant-A"
    # body equal to JWT tenant → JWT tenant
    assert resolve_call_tenant(req, "tenant-A") == "tenant-A"


def test_c_user_cross_tenant_body_is_403(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request(tenant_id="tenant-A")
    with pytest.raises(HTTPException) as exc:
        resolve_call_tenant(req, "tenant-B")
    assert exc.value.status_code == 403


def test_d_no_token_no_user_is_401(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request()  # no token header, no request.state.tenant_id
    with pytest.raises(HTTPException) as exc:
        require_internal_or_tenant(req)
    assert exc.value.status_code == 401


def test_unset_token_env_never_accepts_internal_header(monkeypatch):
    """Fail-safe: with INTERNAL_SERVICE_TOKEN unset, a presented token is
    ignored and (absent a JWT) the request is 401 — the internal path is
    disabled, not wide open."""
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request(headers={"x-internal-service-token": _TOKEN})
    with pytest.raises(HTTPException) as exc:
        require_internal_or_tenant(req)
    assert exc.value.status_code == 401


def test_wrong_token_falls_back_to_user_path(monkeypatch):
    """A wrong token is not internal; with a JWT tenant present it degrades
    to the user path (not a bypass)."""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _request(headers={"x-internal-service-token": "wrong"}, tenant_id="tenant-A")
    ctx = require_internal_or_tenant(req)
    assert ctx.is_internal is False
    assert ctx.tenant_id == "tenant-A"


# ── Wiring: the make_call endpoint enforces the gate ─────────────────────


def _make_body(tenant_id: str | None):
    from app.api.v1.endpoints.telephony_bridge import MakeCallRequest

    return MakeCallRequest(destination="+15551234567", caller_id="1001", tenant_id=tenant_id)


def _call_make_call(req: Request, body) -> HTTPException:
    from app.api.v1.endpoints.telephony_bridge import make_call

    with pytest.raises(HTTPException) as exc:
        asyncio.run(make_call(req, body))
    return exc.value


def test_make_call_no_auth_is_401(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    err = _call_make_call(_request(), _make_body("tenant-B"))
    assert err.status_code == 401


def test_make_call_cross_tenant_user_is_403(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request(tenant_id="tenant-A")
    err = _call_make_call(req, _make_body("tenant-B"))
    assert err.status_code == 403


def test_make_call_internal_token_passes_gate(monkeypatch):
    """Valid internal token → gate passes; the handler then fails downstream
    on the un-connected adapter (status != 401/403), proving the dialer
    path is authorized rather than blocked."""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _request(headers={"x-internal-service-token": _TOKEN})
    err = _call_make_call(req, _make_body("tenant-B"))
    assert err.status_code not in (401, 403)


def test_make_call_user_own_tenant_passes_gate(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    req = _request(tenant_id="tenant-A")
    err = _call_make_call(req, _make_body("tenant-A"))
    assert err.status_code not in (401, 403)


# ── P0-6: hangup/transfer call-ownership (IDOR) ──────────────────────────
#
# The user (JWT) path must only control its OWN tenant's calls. Internal-token
# callers (the dialer/system) are trusted and skip the check. Fail-closed: a
# call owned by another tenant, or not on record, is a 403.

from app.core.security.internal_auth import CallerContext  # noqa: E402


class _FakeTxn:
    """Minimal stand-in for asyncpg's transaction context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.in_transaction = True
        return None

    async def __aexit__(self, *a):
        self._conn.in_transaction = False
        return False


class _FakeConn:
    def __init__(self, row):
        self._row = row
        # Records whether each statement ran inside an explicit transaction, so
        # tests can prove `SET LOCAL` is transaction-scoped rather than merely
        # present. SET LOCAL outside a transaction is silently discarded before
        # the next statement — see docs/v2/rls-set-audit.md (F-25).
        self.in_transaction = False
        self.statements: list[tuple[str, bool]] = []

    def transaction(self):
        return _FakeTxn(self)

    async def execute(self, sql, *a, **k):
        self.statements.append((str(sql), self.in_transaction))
        return None

    async def fetchrow(self, *a, **k):
        self.statements.append(("<fetchrow>", self.in_transaction))
        return self._row


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, row):
        # One connection instance for the life of the pool, so a test can inspect
        # what was executed on it and in what scope.
        self.conn = _FakeConn(row)

    def acquire(self):
        return _FakeAcquire(self.conn)


class _FakeContainer:
    def __init__(self, row):
        self.is_initialized = True
        self.db_pool = _FakePool(row)


def _patch_container(monkeypatch, row):
    """Patch get_container to return ONE stable container, and hand back its pool.

    Stable rather than freshly-constructed per call so a test can inspect what was
    executed on the connection afterwards — and closer to reality, since the real
    container is a singleton.
    """
    import app.core.container as cmod

    container = _FakeContainer(row)
    monkeypatch.setattr(cmod, "get_container", lambda: container)
    return container.db_pool


def _verify(ctx, call_id):
    from app.api.v1.endpoints.telephony_bridge import _verify_call_ownership

    return asyncio.run(_verify_call_ownership(ctx, call_id))


def test_ownership_internal_caller_skips_check(monkeypatch):
    # Trusted internal path: never touches the container (patch it to explode).
    import app.core.container as cmod

    def _boom():
        raise AssertionError("internal path must not query call ownership")

    monkeypatch.setattr(cmod, "get_container", _boom)
    _verify(CallerContext(is_internal=True, tenant_id=None), "call-x")  # no raise


def test_ownership_same_tenant_allowed(monkeypatch):
    _patch_container(monkeypatch, {"tenant_id": "tenant-A"})
    _verify(CallerContext(is_internal=False, tenant_id="tenant-A"), "call-x")  # no raise


def test_ownership_cross_tenant_is_403(monkeypatch):
    _patch_container(monkeypatch, {"tenant_id": "tenant-B"})
    with pytest.raises(HTTPException) as exc:
        _verify(CallerContext(is_internal=False, tenant_id="tenant-A"), "call-x")
    assert exc.value.status_code == 403


def test_ownership_unknown_call_is_403(monkeypatch):
    # Call not on record → cannot prove ownership → fail-closed 403.
    _patch_container(monkeypatch, None)
    with pytest.raises(HTTPException) as exc:
        _verify(CallerContext(is_internal=False, tenant_id="tenant-A"), "call-x")
    assert exc.value.status_code == 403


def test_ownership_lookup_runs_the_bypass_inside_a_transaction(monkeypatch):
    """
    TKT-009 / F-25. `SET LOCAL` outside an explicit transaction is discarded before
    the next statement, so the RLS bypass would never reach the ownership SELECT —
    leaving a security check dependent on session state leaked by some other caller.

    The fake records the transaction state of every statement; without this test
    that recording is dead infrastructure and reverting the fix would go unnoticed.
    """
    pool = _patch_container(monkeypatch, {"tenant_id": "tenant-A"})
    _verify(CallerContext(is_internal=False, tenant_id="tenant-A"), "call-x")

    stmts = pool.conn.statements
    bypass = next((s for s in stmts if "bypass_rls" in s[0]), None)
    lookup = next((s for s in stmts if s[0] == "<fetchrow>"), None)

    assert bypass is not None, f"no RLS bypass was issued; statements={stmts}"
    assert lookup is not None, f"no ownership lookup was issued; statements={stmts}"
    assert bypass[1] is True, "SET LOCAL app.bypass_rls must run inside a transaction"
    assert lookup[1] is True, "the ownership SELECT must run in the same transaction"
