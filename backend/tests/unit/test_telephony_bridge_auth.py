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
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.security.internal_auth import (
    is_internal_service_request,
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


def _call_control_gate(req: Request, user=None):
    from app.api.v1.endpoints.telephony_bridge import _require_telephony_control

    return asyncio.run(_require_telephony_control(req, user))


def test_telephony_start_stop_gate_rejects_unauthenticated(monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        _call_control_gate(_request(), None)
    assert exc.value.status_code == 401


def test_telephony_start_stop_gate_rejects_tenant_admin(monkeypatch):
    from app.api.v1.dependencies import CurrentUser

    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    user = CurrentUser(id="user-1", email="admin@example.com", role="tenant_admin")
    with pytest.raises(HTTPException) as exc:
        _call_control_gate(_request(), user)
    assert exc.value.status_code == 403


def test_telephony_start_stop_gate_allows_internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _request(headers={"x-internal-service-token": _TOKEN})
    assert is_internal_service_request(req) is True
    assert _call_control_gate(req, None) is None


def test_telephony_start_stop_gate_allows_platform_admin(monkeypatch):
    from app.api.v1.dependencies import CurrentUser

    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    user = CurrentUser(id="platform-1", email="root@example.com", role="platform_admin")
    assert _call_control_gate(_request(), user) is None


@pytest.mark.asyncio
async def test_telephony_stop_remains_running_when_any_call_is_unconfirmed(monkeypatch):
    from app import main
    from app.api.v1.endpoints import telephony_bridge

    class Adapter:
        connected = True
        disconnect_called = False

        async def disconnect(self, **_kwargs):
            self.disconnect_called = True

    adapter = Adapter()

    async def drain(*_args, **_kwargs):
        return {
            "total": 1,
            "attempted": 1,
            "confirmed": 0,
            "deferred": 1,
            "deferred_call_ids": ["still-live"],
        }

    monkeypatch.setattr(main, "_terminate_active_telephony_sessions_for_shutdown", drain)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(telephony_bridge, "_watchdog_task", None)

    response = await telephony_bridge.stop_telephony(None)
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["status"] == "termination_deferred"
    assert body["deferred_call_ids"] == ["still-live"]
    assert adapter.disconnect_called is False
    assert telephony_bridge._adapter is adapter


@pytest.mark.asyncio
async def test_telephony_stop_disconnects_only_after_complete_drain(monkeypatch):
    from app import main
    from app.api.v1.endpoints import telephony_bridge

    class Adapter:
        connected = True
        disconnect_kwargs = None

        async def disconnect(self, **kwargs):
            self.disconnect_kwargs = kwargs
            return {"status": "disconnected", "deferred": 0, "deferred_call_ids": []}

    adapter = Adapter()

    async def drain(*_args, **_kwargs):
        return {
            "total": 2,
            "attempted": 2,
            "confirmed": 2,
            "deferred": 0,
            "deferred_call_ids": [],
        }

    monkeypatch.setattr(main, "_terminate_active_telephony_sessions_for_shutdown", drain)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(telephony_bridge, "_watchdog_task", None)

    response = await telephony_bridge.stop_telephony(None)
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["status"] == "stopped"
    assert body["confirmed"] == 2
    assert adapter.disconnect_kwargs == {
        "drain_timeout_s": 5.5,
        "force_handoff": False,
    }
    assert telephony_bridge._adapter is None


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


# ── POST /audio/{session_id} — the C++ gateway audio callback ────────────
#
# This route ingests caller audio for a live call and had NO auth gate at all,
# while its siblings /start and /stop go through `_require_telephony_control`
# (internal service token OR platform admin). Anyone who could reach
# /api/v1/sip/telephony/audio/<session_id> could inject audio into a live
# call's STT stream and recording.
#
# The C++ gateway sends X-Internal-Service-Token. There is deliberately no
# unauthenticated compatibility mode: old gateway and new backend artifacts
# must never be mixed by the deploy path.


def _audio_request(*, headers: dict[str, str] | None = None) -> Request:
    headers = headers or {}
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("api.talkleeai.com", 443),
        "path": "/api/v1/sip/telephony/audio/asterisk-abc-10000",
        "raw_path": b"/api/v1/sip/telephony/audio/asterisk-abc-10000",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("203.0.113.9", 51234),
        "state": {},
    }
    return Request(scope)


def _receive_gateway_audio():
    from app.api.v1.endpoints.telephony_bridge import receive_gateway_audio

    return receive_gateway_audio


def test_gateway_audio_rejects_unauthenticated(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _audio_request()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_receive_gateway_audio()("asterisk-abc-10000", req))

    assert exc.value.status_code in (401, 403)


def test_gateway_audio_allows_internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    req = _audio_request(headers={"x-internal-service-token": _TOKEN})

    # Gate passes; the handler then no-ops on an unreadable body.
    response = asyncio.run(_receive_gateway_audio()("asterisk-abc-10000", req))
    assert response.status_code == 200


def test_gateway_audio_legacy_disable_flag_cannot_bypass_auth(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("TELEPHONY_GATEWAY_AUDIO_REQUIRE_INTERNAL_TOKEN", "false")
    req = _audio_request()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_receive_gateway_audio()("asterisk-abc-10000", req))

    assert exc.value.status_code in (401, 403)


# -- _require_call_control / _require_call_read: unseeded deployment ------
#
# Production has 0 rows in `role_permissions` and 0 in `tenant_users`, so the
# DB-only resolver returns an empty set for every non-platform-admin user and
# both gates 403 every tenant caller: nobody can hang up a live call and nobody
# can read a transfer attempt. "This deployment has no RBAC data" and "this
# user was denied" are different states and must behave differently -- the same
# three-state contract require_permission uses
# (tests/security/test_rbac.py::TestUnseededDeploymentFallback).


def _control_request(*, tenant_id="tenant-A", user_id="user-1", role="tenant_admin"):
    req = _request()
    req.state.tenant_id = tenant_id
    req.state.user_id = user_id
    req.state.user_role = role
    return req


class _BridgeResolver:
    """Stands in for get_effective_permissions + rbac_data_is_seeded."""

    def __init__(self, granted, seeded: bool):
        self.granted = set(granted)
        self.seeded = seeded
        self.probe_calls = 0

    async def resolve(self, *_args, **_kwargs):
        return set(self.granted)

    async def probe(self, *_args, **_kwargs):
        self.probe_calls += 1
        return self.seeded


def _install_bridge_resolver(monkeypatch, resolver):
    from app.api.v1.endpoints import telephony_bridge as tb

    monkeypatch.setattr(tb, "get_effective_permissions", resolver.resolve)
    monkeypatch.setattr(tb, "rbac_data_is_seeded", resolver.probe)


def _call_control(req, db_pool=object()):
    from app.api.v1.endpoints.telephony_bridge import _require_call_control

    return asyncio.run(_require_call_control(req, db_pool=db_pool))


def _call_read(req, db_pool=object()):
    from app.api.v1.endpoints.telephony_bridge import _require_call_read

    return asyncio.run(_require_call_read(req, db_pool=db_pool))


@pytest.fixture()
def _clean_probe_cache():
    # app.core.security.rbac imports app.api.v1.dependencies at module scope and
    # dependencies imports back from rbac, so rbac must never be the FIRST of the
    # pair to be imported in a process. Importing the endpoint module first pulls
    # dependencies in and makes the cycle resolvable.
    from app.api.v1.endpoints import telephony_bridge  # noqa: F401
    from app.core.security.rbac import reset_rbac_seeding_probe_cache

    reset_rbac_seeding_probe_cache()
    yield
    reset_rbac_seeding_probe_cache()


class TestCallControlUnseededDeploymentFallback:
    def test_unseeded_deployment_falls_back_to_role_defaults(
        self, monkeypatch, _clean_probe_cache
    ):
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(monkeypatch, _BridgeResolver([], seeded=False))

        ctx = _call_control(_control_request())
        assert ctx.tenant_id == "tenant-A"
        assert ctx.is_internal is False

    def test_unseeded_fallback_is_role_scoped_not_blanket_allow(
        self, monkeypatch, _clean_probe_cache
    ):
        """Hanging up a live call stays with tenant_admin+: the plain user role
        has no calls:delete default, so the unseeded path is never wider than
        the seeded one."""
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(monkeypatch, _BridgeResolver([], seeded=False))

        with pytest.raises(HTTPException) as exc:
            _call_control(_control_request(role="user"))
        assert exc.value.status_code == 403
        assert exc.value.detail["required"] == "calls:delete"

    def test_seeded_deployment_denies_user_with_no_grant(
        self, monkeypatch, _clean_probe_cache
    ):
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(monkeypatch, _BridgeResolver([], seeded=True))

        with pytest.raises(HTTPException) as exc:
            _call_control(_control_request())
        assert exc.value.status_code == 403

    def test_seeded_deployment_revoking_one_permission_denies(
        self, monkeypatch, _clean_probe_cache
    ):
        """Non-empty grants prove the deployment is seeded -- no probe needed."""
        from app.core.security.rbac import Permission

        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        resolver = _BridgeResolver([Permission.CALLS_READ], seeded=True)
        _install_bridge_resolver(monkeypatch, resolver)

        with pytest.raises(HTTPException) as exc:
            _call_control(_control_request())
        assert exc.value.status_code == 403
        assert resolver.probe_calls == 0, "probe must not run when grants resolve"

    def test_seeded_and_granted_allows(self, monkeypatch, _clean_probe_cache):
        from app.core.security.rbac import Permission

        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(
            monkeypatch, _BridgeResolver([Permission.CALLS_DELETE], seeded=True)
        )

        ctx = _call_control(_control_request())
        assert ctx.tenant_id == "tenant-A"

    def test_probe_query_error_fails_closed_with_503(
        self, monkeypatch, _clean_probe_cache
    ):
        from app.api.v1.endpoints import telephony_bridge as tb

        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)

        async def empty(*_a, **_k):
            return set()

        async def broken(*_a, **_k):
            raise RuntimeError("relation role_permissions does not exist")

        monkeypatch.setattr(tb, "get_effective_permissions", empty)
        monkeypatch.setattr(tb, "rbac_data_is_seeded", broken)

        with pytest.raises(HTTPException) as exc:
            _call_control(_control_request())
        assert exc.value.status_code == 503
        assert exc.value.detail == {"error": "authorization_unavailable"}

    def test_internal_service_token_never_reaches_the_probe(
        self, monkeypatch, _clean_probe_cache
    ):
        """The dialer path is trusted and must not depend on RBAC seeding."""
        monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", _TOKEN)
        resolver = _BridgeResolver([], seeded=False)
        _install_bridge_resolver(monkeypatch, resolver)

        req = _request(headers={"x-internal-service-token": _TOKEN})
        ctx = _call_control(req)
        assert ctx.is_internal is True
        assert resolver.probe_calls == 0


class TestCallReadUnseededDeploymentFallback:
    def test_unseeded_deployment_falls_back_to_role_defaults(
        self, monkeypatch, _clean_probe_cache
    ):
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(monkeypatch, _BridgeResolver([], seeded=False))

        # Even the narrowest role reads calls, so a plain user keeps working.
        ctx = _call_read(_control_request(role="user"))
        assert ctx.tenant_id == "tenant-A"

    def test_seeded_deployment_denies_user_with_no_grant(
        self, monkeypatch, _clean_probe_cache
    ):
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        _install_bridge_resolver(monkeypatch, _BridgeResolver([], seeded=True))

        with pytest.raises(HTTPException) as exc:
            _call_read(_control_request())
        assert exc.value.status_code == 403

    def test_seeded_and_granted_allows(self, monkeypatch, _clean_probe_cache):
        from app.core.security.rbac import Permission

        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        resolver = _BridgeResolver([Permission.CALLS_READ], seeded=True)
        _install_bridge_resolver(monkeypatch, resolver)

        ctx = _call_read(_control_request())
        assert ctx.tenant_id == "tenant-A"
        assert resolver.probe_calls == 0, "probe must not run when grants resolve"

    def test_probe_query_error_fails_closed_with_503(
        self, monkeypatch, _clean_probe_cache
    ):
        from app.api.v1.endpoints import telephony_bridge as tb

        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)

        async def empty(*_a, **_k):
            return set()

        async def broken(*_a, **_k):
            raise RuntimeError("relation tenant_users does not exist")

        monkeypatch.setattr(tb, "get_effective_permissions", empty)
        monkeypatch.setattr(tb, "rbac_data_is_seeded", broken)

        with pytest.raises(HTTPException) as exc:
            _call_read(_control_request())
        assert exc.value.status_code == 503
        assert exc.value.detail == {"error": "authorization_unavailable"}
