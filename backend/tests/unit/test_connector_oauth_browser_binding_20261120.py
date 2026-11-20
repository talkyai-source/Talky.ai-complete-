"""The OAuth sign-in must finish in the browser that started it.

Defect: GET /connectors/callback is unauthenticated and accepted any live
``state``, so a provider link started by one user could be completed in another
person's browser and store that person's mailbox/CRM in the first account.
Fix: the start sets a one-time nonce cookie (HttpOnly, SameSite=Lax, scoped to
the callback path) and keeps only its SHA-256 in the Redis state; the callback
refuses before the code exchange unless the cookie matches.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import fakeredis
import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.api.v1.endpoints import connectors as ce
from app.infrastructure.connectors import oauth as oauth_mod
from app.infrastructure.connectors.base import OAuthTokens

pytestmark = pytest.mark.asyncio

TENANT = "tenant-a"
NONCE = "fixture-browser-nonce"
NONCE_HASH = hashlib.sha256(NONCE.encode()).hexdigest()


def bound_request(state: str, nonce: str | None = NONCE) -> Request:
    """A callback request carrying (or lacking) this flow's nonce cookie."""
    headers = []
    if nonce is not None:
        headers.append((b"cookie", f"{ce._nonce_cookie_name(state)}={nonce}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _location_query(response):
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


class _Lookup:
    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def single(self): return self
    def execute(self): return NS(data=self._data, error=None)


class _World:
    def __init__(self, monkeypatch):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        manager = oauth_mod.OAuthStateManager()
        manager._redis = self.redis
        monkeypatch.setattr(ce, "get_oauth_state_manager", lambda: manager)
        monkeypatch.setenv("FRONTEND_URL", "https://app.example")
        monkeypatch.setenv("API_BASE_URL", "https://api.example")
        self.connector = NS(
            connector_type="email", oauth_scopes=[],
            get_oauth_url=lambda **kw: "https://provider.example/auth?state=" + kw["state"],
            exchange_code=AsyncMock(return_value=OAuthTokens(
                access_token="a", refresh_token="r",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scope=None)),
            set_access_token=AsyncMock(),
            get_profile=AsyncMock(return_value={"emailAddress": "owner@example.invalid"}),
        )
        self.lifecycle = NS(
            create_pending=AsyncMock(return_value={"id": "connector-1"}),
            begin_oauth=AsyncMock(), reject_pending=AsyncMock(),
            activate=AsyncMock(return_value={"id": "account-1"}),
        )
        monkeypatch.setattr(ce, "ConnectorLifecycle", lambda _db, **_kw: self.lifecycle)
        monkeypatch.setattr(ce.ConnectorFactory, "create", lambda **_kw: self.connector)
        monkeypatch.setattr(ce.ConnectorFactory, "is_registered", lambda _p: True)
        monkeypatch.setattr(ce, "get_encryption_service",
                            lambda: NS(encrypt=lambda v: "enc:" + v, decrypt=lambda v: v))
        monkeypatch.setattr("app.core.security.tenant_isolation.set_current_tenant_id", lambda _: None)
        self.db = NS(table=lambda _name: _Lookup(
            {"id": "connector-1", "type": "email", "provider": "gmail", "status": "pending"}))

    async def start(self):
        response = Response()
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                           "scheme": "https", "server": ("api.example", 443)})
        request.state.authenticated_user_id = "user-1"
        request.state.authenticated_session_id = str(uuid4())
        out = await ce.authorize_connector_by_type(
            "email", request, redirect_uri=None, current_user=NS(id="user-1", tenant_id=TENANT),
            db_client=self.db, response=response)
        state = out.authorization_url.rsplit("state=", 1)[1]
        return state, response

    async def callback(self, state, request):
        return await ce.oauth_callback(request, state=state, code="code", error=None, db_client=self.db)


@pytest.fixture
def world(monkeypatch):
    return _World(monkeypatch)


def _nonce_from(response, state):
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(ce._nonce_cookie_name(state) + "="):
            return raw.split("=", 1)[1].split(";", 1)[0]
    raise AssertionError("start did not set the nonce cookie")


async def test_start_sets_a_scoped_httponly_lax_cookie_and_stores_only_the_hash(world):
    state, response = await world.start()
    cookie = next(c for c in response.headers.getlist("set-cookie")
                  if c.startswith(ce._nonce_cookie_name(state) + "="))
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low
    assert "path=/api/v1/connectors/callback" in low
    assert f"max-age={oauth_mod.OAuthStateManager.STATE_TTL_SECONDS}" in low
    nonce = _nonce_from(response, state)
    stored = await world.redis.get(f"{oauth_mod.OAuthStateManager.STATE_KEY_PREFIX}{state}")
    assert nonce not in stored
    assert json.loads(stored)["browser_nonce_hash"] == hashlib.sha256(nonce.encode()).hexdigest()


async def test_matching_cookie_exchanges_the_code_stores_for_the_starting_tenant_and_clears_cookie(world):
    state, start_response = await world.start()
    nonce = _nonce_from(start_response, state)
    response = await world.callback(state, bound_request(state, nonce))
    assert _location_query(response)["status"] == "success"
    world.connector.exchange_code.assert_awaited_once()
    world.lifecycle.activate.assert_awaited_once()
    assert world.lifecycle.activate.await_args.args[0] == TENANT
    cleared = " ".join(response.headers.getlist("set-cookie")).lower()
    assert ce._nonce_cookie_name(state) in cleared and "max-age=0" in cleared


async def test_no_cookie_is_refused_before_exchange_or_write(world):
    state, _ = await world.start()
    response = await world.callback(state, bound_request(state, None))
    assert _location_query(response)["error"] == "state_mismatch"
    world.connector.exchange_code.assert_not_awaited()
    world.lifecycle.begin_oauth.assert_not_awaited()
    world.lifecycle.activate.assert_not_awaited()
    assert ce._nonce_cookie_name(state) in " ".join(response.headers.getlist("set-cookie"))


async def test_a_different_nonce_is_refused(world):
    state, _ = await world.start()
    response = await world.callback(state, bound_request(state, "another-browsers-nonce"))
    assert _location_query(response)["error"] == "state_mismatch"
    world.connector.exchange_code.assert_not_awaited()
    world.lifecycle.activate.assert_not_awaited()


async def test_a_state_without_a_nonce_hash_is_refused(world):
    # A state created before this release: no browser_nonce_hash in its data.
    old_state = "old-format-state"
    await world.redis.setex(
        f"{oauth_mod.OAuthStateManager.STATE_KEY_PREFIX}{old_state}", 300,
        json.dumps({"tenant_id": TENANT, "user_id": "user-1", "provider": "gmail",
                    "redirect_uri": "https://api.example/api/v1/connectors/callback",
                    "code_verifier": "v", "connector_id": "connector-1"}))
    response = await world.callback(old_state, bound_request(old_state))
    assert _location_query(response)["error"] == "state_mismatch"
    world.connector.exchange_code.assert_not_awaited()
    world.lifecycle.activate.assert_not_awaited()


async def test_state_is_still_single_use(world):
    state, start_response = await world.start()
    nonce = _nonce_from(start_response, state)
    first = await world.callback(state, bound_request(state, nonce))
    assert _location_query(first)["status"] == "success"
    second = await world.callback(state, bound_request(state, nonce))
    assert _location_query(second)["error"] == "invalid_state"
    world.connector.exchange_code.assert_awaited_once()
