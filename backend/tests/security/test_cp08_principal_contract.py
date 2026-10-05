"""CP08 explicit identity expectations and configured CORS preflight."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from app.core import app_bootstrap
from app.core.security.principal import assert_expected_identity


@pytest.mark.parametrize("tenant", [None, "tenant-a"])
def test_expected_identity_accepts_exact_owner_including_explicit_no_tenant(tenant):
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-talky-expected-user", b"user-a"),
                (b"x-talky-expected-tenant", (tenant or "").encode()),
            ],
        }
    )
    assert_expected_identity(request, {"id": "user-a", "tenant_id": tenant})


def test_expected_empty_tenant_is_not_absent_or_a_tenant_selector():
    request = Request({"type": "http", "headers": [(b"x-talky-expected-tenant", b"")]})
    with pytest.raises(HTTPException) as error:
        assert_expected_identity(request, {"id": "user-a", "tenant_id": "tenant-a"})
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "identity_changed"


async def test_configured_cors_allows_identity_consistency_headers(monkeypatch):
    origin = "https://synthetic.example"
    monkeypatch.setattr(
        app_bootstrap, "get_settings", lambda: SimpleNamespace(allowed_origins=[origin])
    )
    configured = FastAPI()
    app_bootstrap.configure_middleware(configured)
    cors = next(item for item in configured.user_middleware if item.cls is CORSMiddleware)
    app = FastAPI()
    app.add_middleware(cors.cls, **cors.kwargs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.example"
    ) as client:
        result = await client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-talky-expected-user,x-talky-expected-tenant",
            },
        )
    assert result.status_code == 200
    assert result.headers["access-control-allow-origin"] == origin
    allowed = result.headers["access-control-allow-headers"].lower()
    assert "x-talky-expected-user" in allowed and "x-talky-expected-tenant" in allowed
