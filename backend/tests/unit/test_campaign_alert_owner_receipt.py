"""H35: the actual alert list binds its response to the authenticated reader."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints import alerts


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
OTHER = "bbeeffaa-1234-4abc-8def-0123456789ab"
ACTOR = "ccddeeff-1234-4abc-8def-0123456789ab"
SECOND = "ddeeffaa-1234-4abc-8def-0123456789ab"


@pytest.mark.parametrize("tenant,actor", [(TENANT, ACTOR), (OTHER, ACTOR), (TENANT, SECOND)])
async def test_mounted_alert_list_returns_current_tenant_and_user_even_when_empty(monkeypatch, tenant, actor):
    observed = []

    class Connection:
        async def fetch(self, query, *args):
            assert "WHERE tenant_id = $1" in query
            assert args == (tenant, 51)
            observed.append(query)
            return []

    @asynccontextmanager
    async def acquire(pool, owner):
        assert owner == tenant
        yield Connection()

    monkeypatch.setattr(alerts, "acquire_with_tenant", acquire)
    app = FastAPI()
    app.include_router(alerts.router, prefix="/api/v1")
    app.dependency_overrides[dependencies.get_current_user] = lambda: SimpleNamespace(tenant_id=tenant, id=actor)
    app.dependency_overrides[dependencies.get_db_client] = lambda: SimpleNamespace(pool=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        response = await client.get("/api/v1/alerts")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None, "tenant_id": tenant, "user_id": actor}
    assert len(observed) == 1
