"""C2: database capacity failures survive the shared HTTP error boundary."""
import asyncpg
import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core.db import DatabasePoolTimeoutError
from app.core.error_handlers import register_error_handlers


@pytest.mark.parametrize("error_type", [
    asyncpg.QueryCanceledError, asyncpg.LockNotAvailableError, DatabasePoolTimeoutError,
])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_database_timeout_returns_retryable_503(error_type, wrapped):
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/synthetic-timeout")
    async def route():
        try:
            raise error_type("synthetic private query detail")
        except Exception as exc:
            if wrapped:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://synthetic",
    ) as client:
        response = await client.get("/synthetic-timeout")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "private query" not in response.text


async def test_unrelated_wrapped_error_remains_500():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/synthetic-error")
    async def route():
        try:
            raise ValueError("not a database timeout")
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Operation failed") from exc

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://synthetic",
    ) as client:
        response = await client.get("/synthetic-error")
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
