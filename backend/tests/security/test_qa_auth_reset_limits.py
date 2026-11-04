"""QA-SEC-003 / QA-PEN-026: recovery limits survive code and IP rotation."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.auth import password_reset as reset

EMAIL = "owner@example.com"


def request(ip="203.0.113.9"):
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": b"", "client": (ip, 1234)})


@pytest.fixture
def flow(monkeypatch):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=None))

    @asynccontextmanager
    async def acquire(*args):
        yield conn

    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(reset, "acquire_with_tenant", acquire)
    return redis, conn


async def forgot(email=EMAIL, ip="203.0.113.9"):
    return await reset.forgot_password(request(ip), reset.ForgotPasswordRequest(email=email),
                                      SimpleNamespace(pool=None), SimpleNamespace(log=AsyncMock()))


async def attempt(ip="203.0.113.9"):
    return await reset.reset_password(
        request(ip), reset.ResetPasswordRequest(email=EMAIL, code="000000",
                                               new_password="Synthetic#Passw0rd-2026"),
        SimpleNamespace(pool=None), SimpleNamespace(log=AsyncMock()))


async def test_forgot_caps_ip_even_when_email_changes(flow):
    for n in range(5):
        assert "message" in await forgot(f"owner{n}@example.com")
    with pytest.raises(HTTPException) as exc:
        await forgot("fresh@example.com")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"
    assert "message" in await forgot("fresh@example.com", "203.0.113.10")


async def test_reset_caps_ip_before_even_looking_up_pending_code(flow):
    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            await attempt()
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await attempt()
    assert exc.value.status_code == 429


async def test_daily_email_budget_survives_new_codes_and_source_ips(flow):
    redis, _ = flow
    for n in range(21):
        await redis.setex(reset._reset_redis_key(EMAIL), 900, json.dumps({
            "user_id": "synthetic-user", "code_hash": reset._hash_reset_code(f"{n+1:06d}")
        }))
        with pytest.raises(HTTPException) as exc:
            await attempt(f"203.0.113.{n+1}")
        assert exc.value.status_code == (400 if n < 20 else 429)


async def test_send_cap_fails_closed_without_redis():
    redis = SimpleNamespace(incr=AsyncMock(side_effect=ConnectionError),
                            eval=AsyncMock(side_effect=ConnectionError))
    with pytest.raises(HTTPException) as exc:
        await reset._check_reset_rate_limit(redis, EMAIL)
    assert exc.value.status_code == 503


async def test_concurrent_forgot_requests_share_five_slots(flow):
    results = await asyncio.gather(*(forgot(f"owner{n}@example.com") for n in range(30)),
                                   return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 5
    assert all(isinstance(result, dict) or
               isinstance(result, HTTPException) and result.status_code == 429 for result in results)


async def test_email_send_counter_and_expiry_are_atomic(flow):
    redis, _ = flow
    assert await reset._check_reset_rate_limit(redis, EMAIL)
    assert 0 < await redis.ttl(reset._reset_rate_limit_key(EMAIL)) <= 3600
    assert await reset._check_reset_rate_limit(redis, EMAIL.upper())
    assert await reset._check_reset_rate_limit(redis, EMAIL)
    assert not await reset._check_reset_rate_limit(redis, EMAIL)


@pytest.mark.parametrize("raw", ["broken-json", "[]", '{"code_hash":"é"}'])
async def test_malformed_pending_record_is_rejected_without_server_error(flow, raw):
    redis, _ = flow
    await redis.setex(reset._reset_redis_key(EMAIL), 900, raw)
    with pytest.raises(HTTPException) as exc:
        await attempt()
    assert exc.value.status_code == 400


async def test_pending_record_read_outage_is_503(flow, monkeypatch):
    redis, _ = flow
    monkeypatch.setattr(redis, "get", AsyncMock(side_effect=ConnectionError("synthetic")))
    with pytest.raises(HTTPException) as exc:
        await attempt()
    assert exc.value.status_code == 503


async def test_shared_store_enforces_cap_across_client_instances(flow, monkeypatch):
    server = fakeredis.FakeServer()
    first = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    second = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: first)
    for n in range(5):
        await forgot(f"owner{n}@example.com")
    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: second)
    with pytest.raises(HTTPException) as exc:
        await forgot("fresh@example.com")
    assert exc.value.status_code == 429


@pytest.mark.parametrize("path,payload,initial_status", [
    ("forgot-password", {"email": "owner@example.com"}, 200),
    ("reset-password", {"email": "owner@example.com", "code": "000000",
                        "new_password": "Synthetic#Passw0rd-2026"}, 400),
])
async def test_http_reset_routes_enforce_shared_ip_limit(flow, path, payload, initial_status):
    app = FastAPI()
    app.include_router(reset.router, prefix="/auth")
    app.dependency_overrides[reset.get_db_client] = lambda: SimpleNamespace(pool=None)
    app.dependency_overrides[reset.get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.post(f"/auth/{path}", json=payload) for _ in range(6)]
    assert [response.status_code for response in responses] == [initial_status] * 5 + [429]
    assert responses[-1].headers["Retry-After"] == "60"
