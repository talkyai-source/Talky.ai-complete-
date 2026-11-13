"""F2-02: real mounted routers and configured middleware, synthetic ports only.

Authentication repository ports return a synthetic user only for an explicit
fixture header. Token parsing/session crypto are covered elsewhere; these tests
prove the real routes actually require the correct authentication boundary.
No application lifespan, environment file, provider or external socket runs.
"""
from __future__ import annotations

import socket
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request


INTERNAL = "synthetic-operational-token-not-a-real-secret"
API = "/api/v1"
DEEP = API + "/healthz/deep"
WORKERS = API + "/healthz/workers"
DETAIL = API + "/health/detailed"
SIP = API + "/sip/telephony/status"
PREFETCH = API + "/ai-options/voices/prefetch-status"
DEMO = API + "/webhooks/secure/verify-test"


@pytest.fixture
async def mounted(monkeypatch, tmp_path):
    # Patch configuration loading before importing the real router aggregator.
    from app.core import config, dotenv_compat
    import dotenv
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL)
    monkeypatch.setenv("CARTESIA_API_KEY", "synthetic-key-never-sent")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-key-never-sent")
    settings = config.Settings(_env_file=None, environment="production",
        jwt_secret="synthetic-mounted-ops-secret-only", talky_alert_webhook_url=None,
        talky_alert_email_to=None)
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Operational endpoint tests must not open sockets")
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)

    from app.api.v1.routes import api_router
    from app.api.operational import register_operational_routes
    from app.api.v1 import dependencies
    from app.api.v1.endpoints import telephony_bridge
    from app.api.v1.endpoints.ai_options import preview
    from app.core import app_bootstrap, jwt_security, operational_alerts, security_headers_middleware
    from app.infrastructure.tts import elevenlabs_catalog

    for module in (app_bootstrap, jwt_security, operational_alerts, security_headers_middleware):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    heartbeat = str(time.time())
    redis = NS(ping=AsyncMock(return_value=True), get=AsyncMock(return_value=heartbeat))
    pool = NS(fetchval=AsyncMock(return_value=1))
    sessions = NS(get_active_session_count=lambda: 17)
    container = NS(is_initialized=True, redis_enabled=True, redis=redis, db_pool=pool,
                   _session_manager=sessions, session_manager=sessions, llm_provider=None)
    monkeypatch.setattr("app.core.container.get_container", lambda: container)
    limiter = NS(check_all_tiers=AsyncMock(return_value=(True, {}, None)))
    monkeypatch.setattr("app.core.security.api_security.get_api_rate_limiter", lambda *a, **k: limiter)
    adapter = NS(health_check=AsyncMock(return_value=True), connected=True, name="synthetic-adapter")
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(telephony_bridge, "_get_orchestrator", lambda: NS())
    monkeypatch.setattr(telephony_bridge, "get_state_backend", lambda: NS(voice_session_count=lambda: 17))
    monkeypatch.setattr("app.domain.services.global_concurrency.current_count", AsyncMock(return_value=23))
    monkeypatch.setattr("app.domain.services.global_concurrency.resolve_global_cap", lambda: 64)
    monkeypatch.setattr(preview, "_VOICE_PREVIEW_CACHE_DIR", tmp_path / "empty-f32")
    monkeypatch.setattr(elevenlabs_catalog, "_ELEVENLABS_PREVIEW_CACHE_DIR", tmp_path / "empty-mp3")
    monkeypatch.setattr(elevenlabs_catalog, "elevenlabs_api_key", lambda: "synthetic-key-never-sent")

    authentication_calls = []

    async def optional_user(request: Request):
        role = request.headers.get("x-qa-authenticated-role")
        authentication_calls.append(role)
        if role not in {"user", "tenant_admin", "platform_admin"}:
            return None
        return dependencies.CurrentUser(id="11111111-1111-4111-8111-111111111111",
            tenant_id="22222222-2222-4222-8222-222222222222", role=role,
            email="synthetic@example.invalid")

    async def current_user(request: Request):
        user = await optional_user(request)
        if user is None:
            raise HTTPException(401, "Synthetic unauthenticated principal")
        return user

    app = FastAPI()
    app.include_router(api_router, prefix=API)
    register_operational_routes(app)
    app.dependency_overrides[dependencies.get_optional_user] = optional_user
    app.dependency_overrides[dependencies.get_current_user] = current_user
    app_bootstrap.configure_middleware(app)
    try:
        yield NS(app=app, adapter=adapter, pool=pool, redis=redis, container=container,
                 limiter=limiter, settings=settings, authentication_calls=authentication_calls)
    finally:
        # Restore socket methods before Windows creates the next loop self-pipe.
        monkeypatch.undo()


async def request(world, path, *, headers=None, loopback=False):
    transport = httpx.ASGITransport(app=world.app, raise_app_exceptions=False,
        client=("127.0.0.1" if loopback else "203.0.113.20", 43111))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.synthetic.example") as client:
        return await client.get(path, headers=headers or {})


async def test_existing_root_health_stays_public(mounted):
    response = await request(mounted, "/health")
    assert response.status_code == 200 and response.json()["status"] == "healthy"


@pytest.mark.parametrize("path,expected", [(DEEP, {"ready": True, "alerting": "unconfigured"}),
                                          (WORKERS, {"healthy": True})])
@pytest.mark.parametrize("headers,query", [({}, ""), ({"X-Internal-Service-Token": "wrong"}, ""),
    ({"X-Forwarded-For": "127.0.0.1"}, ""), ({}, "?internal_token=" + INTERNAL)])
async def test_public_monitor_responses_are_minimal_even_with_untrusted_hints(mounted, path, expected, headers, query):
    response = await request(mounted, path + query, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == expected, "Anonymous monitoring response exposed internal dependency/worker details"
    assert INTERNAL not in response.text


@pytest.mark.parametrize("path", [DEEP, WORKERS])
async def test_loopback_without_internal_token_gets_only_public_status(mounted, path):
    response = await request(mounted, path, loopback=True)
    assert response.status_code == 200
    assert set(response.json()) == ({"ready", "alerting"} if path == DEEP else {"healthy"})


async def test_public_deep_preserves_failure_http_status_and_alert_configuration(mounted):
    mounted.pool.fetchval.side_effect = ConnectionError("synthetic-private-database-host")
    response = await request(mounted, DEEP)
    assert response.status_code == 503
    assert response.json() == {"ready": False, "alerting": "unconfigured"}
    assert "database-host" not in response.text


async def test_public_workers_preserve_failure_http_status_without_names_or_timestamps(mounted):
    mounted.redis.get.return_value = None
    response = await request(mounted, WORKERS)
    assert response.status_code == 503
    assert response.json() == {"healthy": False}


@pytest.mark.parametrize("path", [DEEP, WORKERS])
async def test_internal_monitor_retains_real_diagnostic_details(mounted, path):
    response = await request(mounted, path, headers={"X-Internal-Service-Token": INTERNAL})
    assert response.status_code == 200
    payload = response.json()
    if path == DEEP:
        assert payload == {"ready": True, "db": "ok", "redis": "ok", "alerting": "unconfigured"}
    else:
        assert payload["healthy"] is True
        assert {item["name"] for item in payload["workers"]} == {"dialer", "voice", "reminder"}
        assert all("age_seconds" in item and "last_beat_epoch" in item for item in payload["workers"])


async def test_unset_internal_secret_cannot_enable_diagnostics(mounted, monkeypatch):
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN")
    response = await request(mounted, DEEP, headers={"X-Internal-Service-Token": INTERNAL})
    assert response.json() == {"ready": True, "alerting": "unconfigured"}
    assert (await request(mounted, DETAIL, headers={"X-Internal-Service-Token": INTERNAL})).status_code == 401


async def test_non_ascii_internal_header_cannot_crash_or_reveal_diagnostics(mounted):
    headers = {b"x-internal-service-token": b"\xff"}
    response = await request(mounted, DEEP, headers=headers)
    assert response.status_code == 200 and response.json() == {"ready": True, "alerting": "unconfigured"}
    assert (await request(mounted, DETAIL, headers=headers)).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Service-Token": "wrong"},
    {"x-qa-authenticated-role": "platform_admin"}, {"X-Real-IP": "127.0.0.1"}])
async def test_detailed_health_requires_internal_token_not_user_role_or_forwarded_ip(mounted, headers):
    response = await request(mounted, DETAIL, headers=headers)
    assert response.status_code == 401, response.text
    assert "active_sessions" not in response.text and "redis_enabled" not in response.text


async def test_internal_token_can_read_detailed_health(mounted):
    response = await request(mounted, DETAIL, headers={"X-Internal-Service-Token": INTERNAL})
    assert response.status_code == 200
    assert response.json()["active_sessions"] == 17


@pytest.mark.parametrize("headers,expected", [({}, 401), ({"X-Internal-Service-Token": "wrong"}, 401),
    ({"x-qa-authenticated-role": "user"}, 403), ({"x-qa-authenticated-role": "tenant_admin"}, 403)])
async def test_global_sip_status_rejects_anonymous_and_tenant_users_before_adapter_probe(mounted, headers, expected):
    response = await request(mounted, SIP, headers=headers)
    assert response.status_code == expected, response.text
    mounted.adapter.health_check.assert_not_awaited()
    assert "synthetic-adapter" not in response.text and "global_current" not in response.text


@pytest.mark.parametrize("headers", [{"X-Internal-Service-Token": INTERNAL}, {"x-qa-authenticated-role": "platform_admin"}])
async def test_sip_status_preserves_authorized_internal_or_platform_admin_view(mounted, headers):
    response = await request(mounted, SIP, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["active_sessions"] == 17
    mounted.adapter.health_check.assert_awaited_once()


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Service-Token": "wrong"}])
async def test_voice_prefetch_status_requires_a_user_before_exposing_key_presence(mounted, headers):
    response = await request(mounted, PREFETCH, headers=headers)
    assert response.status_code == 401, response.text
    assert "key_configured" not in response.text


async def test_authenticated_voice_prefetch_status_preserves_existing_response(mounted):
    response = await request(mounted, PREFETCH, headers={"x-qa-authenticated-role": "user"})
    assert response.status_code == 200, response.text
    assert response.json()["cartesia_key_configured"] is True
    assert response.json()["preview_samples_cached"] == 0
    assert mounted.authentication_calls == ["user"], "Route never invoked its required principal dependency"


async def test_demo_signature_route_is_not_registered_in_production(mounted):
    response = await request(mounted, DEMO)
    assert response.status_code == 404, response.text
    assert DEMO not in mounted.app.openapi()["paths"]


@pytest.mark.parametrize("path", ["/health", DEEP, WORKERS])
async def test_real_public_monitors_still_bypass_global_ip_rate_limit(mounted, path):
    mounted.limiter.check_all_tiers.return_value = (False, {}, "synthetic denied bucket")
    response = await request(mounted, path)
    assert response.status_code == 200, response.text
    mounted.limiter.check_all_tiers.assert_not_awaited()
