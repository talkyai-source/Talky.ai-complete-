"""HTTP contracts for the monitoring backport on the deployed legacy router.

Authentication is overridden only for these isolated route unit tests. No
application lifespan, database, Redis, provider, or telephony process starts.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.v1.dependencies import get_db_client, require_platform_admin
from app.api.v1.endpoints.admin.health import alerts, system


def route_app(router):
    app = FastAPI()
    app.include_router(router, prefix="/admin")
    app.dependency_overrides[require_platform_admin] = lambda: SimpleNamespace()
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=object())
    return app


@pytest.mark.asyncio
async def test_legacy_alert_settings_still_import_and_serve_existing_contract(monkeypatch):
    monkeypatch.setattr(alerts, "_alert_settings", alerts.AlertSettings())
    async with AsyncClient(transport=ASGITransport(app=route_app(alerts.router)),
                           base_url="http://test") as client:
        response = await client.get("/admin/alerts/settings")
    assert response.status_code == 200
    assert response.json() == {
        "error_rate_threshold": 5.0,
        "latency_threshold_ms": 500,
        "queue_depth_threshold": 100,
        "memory_threshold_percent": 90.0,
        "cpu_threshold_percent": 80.0,
        "email_notifications": False,
        "slack_notifications": False,
        "slack_webhook_url": None,
    }


@pytest.mark.asyncio
async def test_detailed_health_serves_measured_host_values_with_explicit_scope(monkeypatch):
    metrics = {
        "uptime_seconds": 91,
        "uptime_display": "1m",
        "memory_usage_mb": 512.5,
        "memory_total_mb": 2048.0,
        "memory_percent": 25.02,
        "cpu_usage_percent": 7.5,
        "disk_usage_percent": 30.0,
        "disk_free_gb": 15.25,
    }
    monkeypatch.setattr(system, "_host_metrics", lambda: metrics.copy())
    monkeypatch.setattr(system, "_internal_dependency_health", AsyncMock(return_value=[]))
    async with AsyncClient(transport=ASGITransport(app=route_app(system.router)),
                           base_url="http://test") as client:
        response = await client.get("/admin/health/detailed")
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in metrics} == metrics
    assert body["checked_at"] == body["last_updated"]
    assert body["checked_at"]
    assert "uptime is this API process only" in body["metrics_scope"]


@pytest.mark.asyncio
async def test_failed_host_measurement_is_503_without_fabricated_metrics(monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic unavailable host measurement")

    monkeypatch.setattr(system, "_host_metrics", unavailable)
    monkeypatch.setattr(system, "_internal_dependency_health", AsyncMock(return_value=[]))
    async with AsyncClient(transport=ASGITransport(app=route_app(system.router)),
                           base_url="http://test") as client:
        response = await client.get("/admin/health/detailed")
    assert response.status_code == 503
    assert response.json() == {"detail": "API host metrics could not be measured."}
