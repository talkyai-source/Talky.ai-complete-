"""Admin health must distinguish absent observations from successful probes."""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints.admin.base import router as base_router
from app.api.v1.endpoints.admin.health import system
from app.api.v1.endpoints import telephony_bridge
from app.core import container
from app.infrastructure.metrics import provider_observations


@pytest.fixture
def health_client(monkeypatch):
    # Stable host metrics keep these tests focused on the HTTP health contract.
    monkeypatch.setattr(system.psutil, "virtual_memory", lambda: SimpleNamespace(
        used=1024 * 1024, total=2 * 1024 * 1024, percent=50.0,
    ))
    monkeypatch.setattr(system.psutil, "cpu_percent", lambda **kwargs: 10.0)
    monkeypatch.setattr(system.psutil, "disk_usage", lambda path: SimpleNamespace(percent=25.0, free=1024**3))
    # Real health composition, isolated read ports; no provider/PBX network.
    monkeypatch.setattr(container, "get_container", lambda: SimpleNamespace(is_initialized=False))
    monkeypatch.setattr(telephony_bridge, "_adapter", SimpleNamespace(name="unsupported-test-port"))
    monkeypatch.setattr(provider_observations, "read_recent", AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(base_router)
    app.include_router(system.router)
    user = CurrentUser(id="health-test-admin", email="admin@example.test", role="platform_admin")
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=1), execute=AsyncMock())
    @asynccontextmanager
    async def transaction():
        yield
    connection.transaction = transaction
    @asynccontextmanager
    async def acquire(**kwargs):
        yield connection
    database = SimpleNamespace(pool=SimpleNamespace(acquire=acquire))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: database
    with TestClient(app) as client:
        yield client, connection


@pytest.mark.parametrize("path", ["/system-health", "/health/detailed"])
@pytest.mark.parametrize("keys_configured", [False, True])
def test_unmeasured_providers_never_claim_health_or_latency(health_client, monkeypatch, path, keys_configured):
    for name in ("DEEPGRAM_API_KEY", "GROQ_API_KEY", "CARTESIA_API_KEY"):
        if keys_configured:
            monkeypatch.setenv(name, "synthetic-configured-but-unverified-key")
        else:
            monkeypatch.delenv(name, raising=False)
    client, database = health_client
    response = client.get(path)
    assert response.status_code == 200
    providers = {item["name"]: item for item in response.json()["providers"]}
    for name in ("STT", "LLM", "TTS"):
        item = providers[name]
        assert item["status"] == "unknown"
        assert item["latency_ms"] is None
        assert item["latency_display"] == "Not measured"
        assert item.get("last_check") is None
    database.fetchval.assert_awaited_once_with("SELECT 1")
    observed = providers["Database"]
    assert observed["status"] in {"operational", "degraded"}
    assert isinstance(observed["latency_ms"], int) and observed["latency_ms"] >= 0
    assert observed["last_check"]


def test_failed_database_probe_reports_failure_without_a_zero_latency_measurement(health_client):
    client, database = health_client
    database.fetchval.side_effect = RuntimeError(
        "private database connection diagnostic"
    )
    response = client.get("/health/detailed")
    assert response.status_code == 200
    observed = next(item for item in response.json()["providers"] if item["name"] == "Database")
    assert observed["status"] == "down"
    assert observed["latency_ms"] is None
    assert observed["latency_display"] == "Unavailable"
    assert observed["last_check"]
    assert "private database" not in response.text
