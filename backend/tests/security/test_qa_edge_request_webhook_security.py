"""QA-PEN-023 / QA-SEC-061,068 behavioral reproductions, without live services."""
import asyncio
import threading
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.core import api_security_middleware as api_security
from app.core.security.csrf import CSRFMiddleware
from app.core.security.webhook_verification import (
    compute_signature,
    compute_signature_with_timestamp,
    verify_signature,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/example", "/api/v1/auth/login"])
@pytest.mark.parametrize("declared", [None, "1", "2048"])
async def test_oversized_actual_body_rejected_before_handler(monkeypatch, path, declared):
    monkeypatch.setattr(api_security, "MAX_REQUEST_BODY_SIZE", 1024)
    called = []
    app = FastAPI()

    @app.post(path)
    async def endpoint(request: Request):
        called.append(True)
        return {"size": len(await request.body())}

    app.add_middleware(api_security.APISecurityMiddleware)

    async def chunks():
        yield b"a" * 512
        yield b"b" * 513
        raise AssertionError("must stop reading immediately after the size cap")

    headers = {"content-length": declared} if declared else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=chunks(), headers=headers)
    assert response.status_code == 413
    assert not called


@pytest.mark.asyncio
async def test_bounded_body_is_unchanged_and_streaming_response_survives(monkeypatch):
    monkeypatch.setattr(api_security, "MAX_REQUEST_BODY_SIZE", 1024)
    app = FastAPI()

    @app.post("/api/v1/example")
    async def endpoint(request: Request):
        body = await request.body()
        async def response_chunks():
            yield body[:512]
            await asyncio.sleep(0)
            yield body[512:]
        return StreamingResponse(response_chunks())

    app.add_middleware(api_security.APISecurityMiddleware)
    async def chunks():
        yield b"a" * 512
        yield b"b" * 512
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/example", content=chunks())
    assert response.status_code == 200
    assert response.content == b"a" * 512 + b"b" * 512


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/example", "/api/v1/auth/login"])
async def test_qa_twelve_megabyte_chunked_body_is_413(path):
    app = FastAPI()
    handler = AsyncMock()

    @app.post(path)
    async def endpoint(request: Request):
        await handler()
        return {"size": len(await request.body())}

    app.add_middleware(api_security.APISecurityMiddleware)
    async def chunks():
        for _ in range(12):
            yield b"x" * (1024 * 1024)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=chunks())
    assert response.status_code == 413
    handler.assert_not_awaited()


def test_untimestamped_hmac_is_rejected():
    assert verify_signature(b"{}", compute_signature(b"{}", "test-secret"), "test-secret")[0] is False


def test_non_ascii_signature_is_invalid_instead_of_server_error():
    assert verify_signature(b"{}", "\u00e9" * 64, "test-secret", str(int(time.time())))[0] is False


def test_timestamp_is_signed_and_cannot_be_refreshed(monkeypatch):
    monkeypatch.setattr("app.core.security.webhook_verification.time.time", lambda: 2000)
    signature = compute_signature_with_timestamp(b"{}", "test-secret", 2000)
    assert verify_signature(b"{}", signature, "test-secret", "2000")[0] is True
    monkeypatch.setattr("app.core.security.webhook_verification.time.time", lambda: 2400)
    assert verify_signature(b"{}", signature, "test-secret", "2000")[0] is False
    assert verify_signature(b"{}", signature, "test-secret", "2400")[0] is False


@pytest.mark.parametrize("offset,accepted", [(299, True), (300, True), (301, False), (-300, True), (-301, False)])
def test_signed_timestamp_allows_only_five_minutes_clock_skew(monkeypatch, offset, accepted):
    monkeypatch.setattr("app.core.security.webhook_verification.time.time", lambda: 2000)
    timestamp = 2000 + offset
    signature = compute_signature_with_timestamp(b"{}", "test-secret", timestamp)
    assert verify_signature(b"{}", signature, "test-secret", str(timestamp))[0] is accepted


@pytest.mark.asyncio
async def test_duplicate_content_lengths_are_rejected_without_dispatch():
    app = FastAPI()
    handler = AsyncMock()

    @app.post("/api/v1/auth/login")
    async def endpoint():
        await handler()
        return {"ok": True}

    app.add_middleware(api_security.APISecurityMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/login", content=b"{}", headers=[("Content-Length", "2"), ("Content-Length", "1")])
    assert response.status_code == 400
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/auth/passkeys/key-id", "/api/v1/auth/passkeys/register/begin",
    "/api/v1/auth/login-extra", "/api/v1/auth/signup/extra",
    "/api/v1/webhooks/configure", "/api/v1/webhooks/secure/configure",
])
async def test_cookie_mutations_do_not_inherit_prefix_exemption(path):
    middleware = CSRFMiddleware(app=None, allowed_origins=["https://app.example"])
    request = Request({"type": "http", "method": "DELETE" if path.endswith("key-id") else "POST", "path": path, "headers": [(b"cookie", b"session=example")]})
    next_handler = AsyncMock()
    response = await middleware.dispatch(request, next_handler)
    assert response.status_code == 403
    next_handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/webhooks/call/goal-achieved", "/api/v1/webhooks/call/mark-spam"])
async def test_real_webhook_route_needs_hmac_but_not_browser_origin(monkeypatch, path):
    from app.api.v1.dependencies import get_db_client
    from app.api.v1.endpoints import webhooks, webhooks_secure
    from app.core.security.webhook_verification import create_webhook_signature_headers
    monkeypatch.setattr(webhooks_secure, "get_webhook_secret_from_db", AsyncMock(return_value="test-secret"))
    goal = AsyncMock(return_value={"ok": True})
    spam = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(webhooks, "dispatch_goal_achieved", goal)
    monkeypatch.setattr(webhooks, "dispatch_mark_spam", spam)
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: None
    app.add_middleware(CSRFMiddleware, allowed_origins=["https://app.example"])
    body = b'{"call_id":"call-1"}'
    headers = create_webhook_signature_headers(body, "test-secret")
    headers["X-Tenant-ID"] = "11111111-1111-1111-1111-111111111111"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, content=body, headers=headers)
        assert response.status_code == 200
        for invalid_signature in [b"0" * 64, b"\xe9" * 64]:
            headers["X-Webhook-Signature"] = invalid_signature
            response = await client.post(path, content=body, headers=headers)
            assert response.status_code == 401
        headers["X-Webhook-Signature"] = compute_signature(body, "test-secret")
        headers.pop("X-Webhook-Timestamp")
        response = await client.post(path, content=body, headers=headers)
        assert response.status_code == 401
    assert goal.await_count + spam.await_count == 1


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be"])
def test_salesforce_dtd_after_4kb_rejected_before_entity_expansion(encoding):
    from app.api.v1.endpoints.salesforce import parse_outbound_message
    declaration = "utf-16" if encoding.startswith("utf-16") else encoding
    raw = (f'<?xml version="1.0" encoding="{declaration}"?>' + " " * 5000 + '<!DOCTYPE x [<!ENTITY a "injected">]><x>&a;</x>').encode(encoding)
    with pytest.raises(ValueError, match="DTD not allowed"):
        parse_outbound_message(raw)


@pytest.mark.parametrize("literal_kind", ["cdata", "comment"])
def test_salesforce_long_xml_accepts_declaration_text_in_literals(literal_kind):
    from app.api.v1.endpoints.salesforce import parse_outbound_message

    text = 'Customer asked about <!DOCTYPE html> and <!ENTITY example "text">.'
    description = (
        f"<![CDATA[{text}]]>" if literal_kind == "cdata"
        else f"<!--{text}-->Ordinary notes"
    )
    raw = (
        '<Envelope><Body><notifications><OrganizationId>test-org</OrganizationId>'
        + " " * 5000
        + '<Notification><sObject><Phone>+15555550123</Phone>'
        + f'<Description>{description}</Description>'
        + '</sObject></Notification></notifications></Body></Envelope>'
    ).encode("utf-8")

    org_id, records = parse_outbound_message(raw)

    assert org_id == "test-org"
    assert records == [{
        "Phone": "+15555550123",
        "Description": text if literal_kind == "cdata" else "Ordinary notes",
    }]


@pytest.mark.asyncio
async def test_salesforce_parser_runs_off_event_loop(monkeypatch):
    from app.api.v1.endpoints import salesforce
    event_thread = threading.get_ident()
    observed = []
    monkeypatch.setattr(salesforce, "_verify_webhook", lambda *args: ({}, {}))
    def parser(raw):
        observed.append(threading.get_ident())
        return None, []
    monkeypatch.setattr(salesforce, "parse_outbound_message", parser)
    request = AsyncMock()
    request.body.return_value = b"<x/>"
    response = await salesforce.salesforce_outbound_message("tenant", "token", request, None)
    assert response.status_code == 200
    assert observed and observed[0] != event_thread
