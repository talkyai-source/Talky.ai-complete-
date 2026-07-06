"""P0-5 regression: /webhooks/call/* must be HMAC-signature-verified.

These routes mutate call/lead state (goal-achieved, mark-spam). They used to
accept a raw call_id/lead_id with NO verification — anyone could forge call
outcomes or block arbitrary leads cross-tenant. They now require a valid
per-tenant HMAC signature, exactly like /webhooks/secure/call/*.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security.webhook_verification import create_webhook_signature_headers
from app.api.v1.dependencies import get_db_client

SECRET = "whsec_test_p0_5_secret"


def _client(monkeypatch, *, secret):
    """Minimal app mounting the webhooks router, with the secret lookup mocked."""
    import app.api.v1.endpoints.webhooks as wh

    async def _fake_secret(tenant_id, name):
        return secret

    monkeypatch.setattr(wh, "get_webhook_secret_from_db", _fake_secret)

    app = FastAPI()
    app.include_router(wh.router, prefix="/api/v1")
    app.dependency_overrides[get_db_client] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def test_goal_achieved_rejects_forged_signature(monkeypatch):
    """Secret configured, but a bogus signature → 401 (forgery blocked)."""
    client = _client(monkeypatch, secret=SECRET)
    r = client.post(
        "/api/v1/webhooks/call/goal-achieved",
        content=b'{"call_id":"c1"}',
        headers={"X-Webhook-Signature": "sha256=deadbeef", "X-Tenant-ID": "t1"},
    )
    assert r.status_code == 401


def test_mark_spam_rejects_forged_signature(monkeypatch):
    client = _client(monkeypatch, secret=SECRET)
    r = client.post(
        "/api/v1/webhooks/call/mark-spam",
        content=b'{"call_id":"c1","lead_id":"l1"}',
        headers={"X-Webhook-Signature": "sha256=deadbeef", "X-Tenant-ID": "t1"},
    )
    assert r.status_code == 401


def test_rejects_when_no_secret_configured(monkeypatch):
    """Even a correctly-signed body is refused if the tenant has no secret."""
    client = _client(monkeypatch, secret=None)
    payload = b'{"call_id":"c1"}'
    headers = create_webhook_signature_headers(payload, SECRET)
    headers["X-Tenant-ID"] = "t1"
    r = client.post("/api/v1/webhooks/call/goal-achieved", content=payload, headers=headers)
    assert r.status_code == 401


def test_missing_signature_header_is_rejected(monkeypatch):
    """No X-Webhook-Signature header at all → rejected (never processed)."""
    client = _client(monkeypatch, secret=SECRET)
    r = client.post(
        "/api/v1/webhooks/call/goal-achieved",
        content=b'{"call_id":"c1"}',
        headers={"X-Tenant-ID": "t1"},
    )
    assert r.status_code in (401, 422)  # missing required header


def test_goal_achieved_accepts_valid_signature(monkeypatch):
    """Correct secret + valid signature → processed (200)."""
    client = _client(monkeypatch, secret=SECRET)

    class _CS:
        async def mark_goal_achieved(self, call_id):
            return {"ok": True, "call_id": call_id}

    class _Container:
        call_service = _CS()

    import app.core.container as container
    monkeypatch.setattr(container, "get_container", lambda: _Container())

    payload = b'{"call_id":"c1"}'
    headers = create_webhook_signature_headers(payload, SECRET)
    headers["X-Tenant-ID"] = "t1"
    r = client.post("/api/v1/webhooks/call/goal-achieved", content=payload, headers=headers)
    assert r.status_code == 200
    assert r.json()["call_id"] == "c1"
