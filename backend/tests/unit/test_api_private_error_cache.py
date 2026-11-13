"""Private responses also cross the outer error and initial-login boundaries."""
import asyncio

import httpx
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from app.core.api_security_middleware import APISecurityMiddleware
from app.core.error_handlers import register_error_handlers
from app.core.security.sessions import SESSION_COOKIE_NAME


async def test_authenticated_unhandled_500_response_is_not_cacheable():
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(APISecurityMiddleware)

    @app.get("/api/v1/private")
    async def fail_after_authentication(request: Request):
        request.state.authenticated_user_id = "aabbccdd-1234-4abc-8def-0123456789ab"
        raise RuntimeError("Synthetic private handler failure")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                base_url="http://synthetic") as client:
        response = await asyncio.wait_for(client.get("/api/v1/private"), timeout=2)
    assert response.status_code == 500
    assert response.headers.get("Cache-Control") == "no-store"


async def test_initial_session_cookie_response_is_not_cacheable_without_prior_identity():
    app = FastAPI()
    app.add_middleware(APISecurityMiddleware)

    @app.post("/api/v1/auth/login")
    async def issue_session():
        response = JSONResponse({"access_token": "synthetic-token"},
                                headers={"Cache-Control": "public, max-age=3600"})
        response.set_cookie(SESSION_COOKIE_NAME, "synthetic-session", httponly=True)
        return response

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                base_url="http://synthetic") as client:
        response = await asyncio.wait_for(client.post("/api/v1/auth/login", json={}), timeout=2)
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "no-store"
    assert SESSION_COOKIE_NAME in response.headers["set-cookie"]
