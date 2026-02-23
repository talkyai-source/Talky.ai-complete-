import jwt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import tenant_middleware as tm
from app.core.tenant_middleware import TenantMiddleware


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TenantMiddleware)

    @app.get("/api/v1/health")
    async def api_health():
        return {"status": "healthy"}

    @app.get("/api/v1/protected")
    async def protected(request: Request):
        return {"tenant_id": getattr(request.state, "tenant_id", None)}

    return app


def test_invalid_token_does_not_break_public_api_health(monkeypatch):
    monkeypatch.setattr(
        tm,
        "_current_jwt_config",
        lambda: ("development", "expected-secret", "HS256"),
    )

    app = _build_test_app()
    client = TestClient(app)

    bad_token = jwt.encode({"sub": "u1"}, "wrong-secret", algorithm="HS256")
    res = client.get("/api/v1/health", headers={"Authorization": f"Bearer {bad_token}"})

    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_invalid_token_returns_401_not_500(monkeypatch):
    monkeypatch.setattr(
        tm,
        "_current_jwt_config",
        lambda: ("development", "expected-secret", "HS256"),
    )

    app = _build_test_app()
    client = TestClient(app)

    bad_token = jwt.encode({"sub": "u1"}, "wrong-secret", algorithm="HS256")
    res = client.get("/api/v1/protected", headers={"Authorization": f"Bearer {bad_token}"})

    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid authentication token"
