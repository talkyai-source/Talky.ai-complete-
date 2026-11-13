"""Passkey sign-in limits count per visitor, not one bucket for everyone.

passkeys.py had its own slowapi Limiter keyed by get_remote_address, which
behind nginx is always the proxy's loopback address. The 10/minute limit on
/auth/passkeys/login/begin and /login/complete was therefore ONE bucket shared
by every visitor: ten requests a minute from anyone refused passkey sign-in to
everyone. The routes now use the shared auth limiter, keyed by the real client
IP (X-Real-IP, trusted only from our own proxy tier).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.dependencies import get_db_client
from app.api.v1.endpoints import passkeys
from app.api.v1.endpoints.auth import _shared as auth_shared


@pytest.fixture
def behind_nginx(monkeypatch):
    app = FastAPI()
    app.include_router(passkeys.router)
    app.state.limiter = passkeys.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # The limiter runs before the handler body, so the database never matters.
    app.dependency_overrides[get_db_client] = lambda: object()
    monkeypatch.setattr(passkeys.limiter, "enabled", True)
    passkeys.limiter.reset()
    # The TCP peer is nginx on loopback, as in production.
    with TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False) as client:
        yield client
    passkeys.limiter.reset()


def _begin(client, visitor_ip: str):
    return client.post("/auth/passkeys/login/begin", json={}, headers={"X-Real-IP": visitor_ip})


def test_one_visitor_at_the_limit_does_not_lock_out_another(behind_nginx):
    for _ in range(10):
        assert _begin(behind_nginx, "203.0.113.10").status_code != 429

    assert _begin(behind_nginx, "203.0.113.10").status_code == 429
    assert _begin(behind_nginx, "203.0.113.20").status_code != 429


def test_passkey_routes_use_the_shared_auth_limiter():
    assert passkeys.limiter is auth_shared.limiter
