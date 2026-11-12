"""HA plan, phase 3: the login and signup limiter keeps its counts in Redis.

slowapi kept the auth limiter's counts in each process's memory. Behind the
load balancer, two APP servers would each allow the full limit, so a visitor
got twice the login attempts. In production the counts now live in the Redis
the app already requires, using the container's own Redis settings; a Redis
outage falls back to per-process counts instead of failing sign-in.
"""
from __future__ import annotations

import fakeredis
import pytest
import redis
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from limits.storage import storage_from_string
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.endpoints.auth import _shared as auth_shared
from app.core.rate_limit_storage import limiter_storage

_REDIS_ENV = (
    "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD", "REDIS_MODE",
    "REDIS_SENTINEL_ADDRESSES", "REDIS_SENTINEL_SERVICE_NAME", "REDIS_SENTINEL_PASSWORD",
    "REDIS_CLUSTER_NODES",
)


@pytest.fixture(autouse=True)
def _clean_redis_env(monkeypatch):
    for name in _REDIS_ENV:
        monkeypatch.delenv(name, raising=False)


def test_outside_production_counts_stay_in_memory(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert limiter_storage() == (None, {})


def test_production_uses_the_containers_redis_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_HOST", "redis.internal")
    monkeypatch.setenv("REDIS_PASSWORD", "fixture/pw+=")

    uri, options = limiter_storage()

    assert uri == "redis://:fixture%2Fpw%2B%3D@redis.internal:6379/0"
    assert options == {"socket_timeout": 0.5, "socket_connect_timeout": 0.5}


def test_production_sentinel_follows_the_container_settings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_MODE", "sentinel")
    monkeypatch.setenv("REDIS_SENTINEL_ADDRESSES", "10.0.0.11:26379, 10.0.0.12:26379")
    monkeypatch.setenv("REDIS_SENTINEL_SERVICE_NAME", "talky")
    monkeypatch.setenv("REDIS_PASSWORD", "data-fixture")
    monkeypatch.setenv("REDIS_SENTINEL_PASSWORD", "sentinel-fixture")

    uri, options = limiter_storage()

    assert uri == "redis+sentinel://10.0.0.11:26379,10.0.0.12:26379/talky"
    assert options["password"] == "data-fixture"
    assert options["sentinel_kwargs"]["password"] == "sentinel-fixture"
    # The limits library accepts this URI and these options (no network yet).
    storage = storage_from_string(uri, **options)
    assert storage.service_name == "talky"


def test_production_cluster_lists_the_cluster_nodes(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_MODE", "cluster")
    monkeypatch.setenv("REDIS_CLUSTER_NODES", "10.0.0.21:6379,10.0.0.22:6379")

    uri, _options = limiter_storage()

    assert uri == "redis+cluster://10.0.0.21:6379,10.0.0.22:6379"


def _login_app(limiter) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/login")
    @limiter.limit("3/minute")
    async def login(request: Request):
        return {"ok": True}

    return app


def _post(client: TestClient, visitor_ip: str = "203.0.113.10") -> int:
    return client.post("/login", headers={"X-Real-IP": visitor_ip}).status_code


@pytest.fixture
def shared_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fakeredis.FakeRedis(server=server))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_HOST", "redis.internal")
    return server


def test_two_servers_share_one_count_per_visitor(shared_redis):
    app_1 = _login_app(auth_shared._build_limiter())
    app_2 = _login_app(auth_shared._build_limiter())
    # nginx on loopback is the TCP peer on each server.
    with TestClient(app_1, client=("127.0.0.1", 50001)) as server_1, TestClient(
        app_2, client=("127.0.0.1", 50002)
    ) as server_2:
        assert [_post(server_1), _post(server_2), _post(server_1)] == [200, 200, 200]

        # Per-process memory allowed 3 more here; the shared count is spent.
        assert _post(server_2) == 429
        assert _post(server_2, visitor_ip="203.0.113.20") == 200


def test_a_redis_outage_falls_back_to_local_counts_not_to_errors(shared_redis):
    shared_redis.connected = False
    app = _login_app(auth_shared._build_limiter())
    with TestClient(app, client=("127.0.0.1", 50003)) as client:
        assert [_post(client) for _ in range(3)] == [200, 200, 200]
        assert _post(client) == 429
