"""Middleware rejections use the same request-correlated public envelope."""
import httpx
import pytest
from fastapi import FastAPI

from app.core.api_security_middleware import APISecurityMiddleware
from app.core.jwt_security import JWTValidationError
from app.core.request_id_middleware import RequestIdMiddleware
from app.core.security.csrf import CSRFMiddleware
from app.core.tenant_middleware import TenantMiddleware


RID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
@pytest.mark.parametrize("case,status,code", [
    ("csrf_missing", 403, "forbidden"), ("csrf_origin", 403, "forbidden"),
    ("tenant_token", 401, "unauthorized"), ("tenant_decode", 401, "unauthorized"),
    ("tenant_unavailable", 503, "service_unavailable"),
    ("media_type", 415, "unsupported_media_type"),
])
async def test_middleware_rejections_have_shared_envelope(monkeypatch, case, status, code):
    app = FastAPI()
    called = []

    @app.post("/api/v1/example")
    async def write():
        called.append(True)
        return {"ok": True}

    headers = {"X-Request-ID": RID}
    if case.startswith("csrf"):
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        app.add_middleware(CSRFMiddleware, allowed_origins=["https://allowed.example.invalid"])
        if case == "csrf_origin":
            headers["Origin"] = "https://untrusted.example.invalid"
    elif case.startswith("tenant"):
        app.add_middleware(TenantMiddleware)
        headers["Authorization"] = "Bearer synthetic-invalid"
        error = (RuntimeError("synthetic internal decoder text") if case == "tenant_decode" else
                 JWTValidationError("Authentication temporarily unavailable", status_code=503)
                 if case == "tenant_unavailable" else JWTValidationError("Invalid authentication token"))

        def reject(*args):
            raise error

        monkeypatch.setattr("app.core.tenant_middleware.decode_and_validate_token", reject)
    else:
        app.add_middleware(APISecurityMiddleware)
        headers["Content-Type"] = "application/x-synthetic"
    app.add_middleware(RequestIdMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.post("/api/v1/example", content=b"{}", headers=headers)
    assert response.status_code == status, response.text
    assert response.headers["X-Request-ID"] == RID
    assert set(response.json()) == {"error"}, response.text
    public = response.json()["error"]
    assert set(public) == {"code", "message", "details", "request_id"}
    assert public["code"] == code
    assert public["request_id"] == RID
    assert isinstance(public["message"], str) and public["message"]
    assert "synthetic internal decoder text" not in response.text
    assert not called
    if case.startswith("tenant"):
        assert response.headers["WWW-Authenticate"] == "Bearer"
