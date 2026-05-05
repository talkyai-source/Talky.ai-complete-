from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api.v1.dependencies import get_audit_logger, get_db_client
from app.api.v1.endpoints.auth.login import router


def test_login_payload_is_registered_as_request_body():
    app = FastAPI()
    app.include_router(router, prefix="/auth")

    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/auth/login"
    )

    assert [param.name for param in route.dependant.body_params] == ["body"]
    assert "body" not in [param.name for param in route.dependant.query_params]


@pytest.mark.asyncio
async def test_login_request_validation_uses_resolved_login_model():
    app = FastAPI()
    app.include_router(router, prefix="/auth")

    conn = SimpleNamespace(
        fetchval=AsyncMock(return_value=0),
        fetchrow=AsyncMock(return_value=None),
        execute=AsyncMock(),
    )
    acquire_context = AsyncMock()
    acquire_context.__aenter__.return_value = conn
    acquire_context.__aexit__.return_value = None
    pool = MagicMock()
    pool.acquire.return_value = acquire_context

    audit_logger = SimpleNamespace(log_security_event=AsyncMock())
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=pool)
    app.dependency_overrides[get_audit_logger] = lambda: audit_logger

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/auth/login",
            json={"email": "missing@example.com", "password": "password123"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."
