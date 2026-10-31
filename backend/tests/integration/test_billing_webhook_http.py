"""Signed HTTP to real PostgreSQL financial effects, with synthetic SDK reads.

The actual router, full middleware, Stripe signature verifier, dispatcher and
transaction processor run. Only external Stripe reads are replaced; no provider
account, notification sender or application background worker is started.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import stripe
from fastapi import FastAPI

from app.api.v1.endpoints import billing as api
from app.core.app_bootstrap import configure_middleware
from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_service import BillingService
from app.domain.services.billing_webhook_notifications import drain_billing_notifications
from app.domain.services.billing_webhook_reconciliation import BillingReconciliation
from tests.integration import test_billing_topup_events as topup_fixtures
from tests.integration import test_billing_webhook_migration as migration_fixtures
from tests.integration.test_billing_webhook_receipts import smtp_service

pytestmark = pytest.mark.integration
webhook_db = migration_fixtures.webhook_db
topup_db = topup_fixtures.topup_db
SECRET = "synthetic-cp03-financial-http-signing-secret"
PATH = "/api/v1/billing/webhooks"


@pytest.fixture
def financial_app(topup_db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic_cp03_not_a_provider_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("STRIPE_MOCK_MODE", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    topup_db.verified_events = {}

    async def retrieve(self, resource, method, identity):
        # The fixture returns installed stripe.StripeObject instances and
        # rejects every operation other than an expected object retrieval.
        if resource == "Event":
            assert method == "retrieve" and identity in topup_db.verified_events
            return stripe.StripeObject.construct_from(deepcopy(topup_db.verified_events[identity]), "sk_test_synthetic")
        return await topup_db.data.provider._stripe_call(resource, method, identity)

    monkeypatch.setattr(BillingService, "_stripe_call", retrieve)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[api.get_db_client] = lambda: SimpleNamespace(pool=topup_db.db.pool)
    app.dependency_overrides[api.get_audit_logger] = lambda: SimpleNamespace(log=AsyncMock())
    configure_middleware(app)
    return app


async def deliver(app, envelope):
    body = json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")
    stamp = int(time.time())
    digest = hmac.new(SECRET.encode(), str(stamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    # A new client/request exercises a fresh BillingService instance on retry.
    # Loopback avoids the global Redis limiter, without removing middleware.
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43211))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(PATH, content=body, headers={
            "content-type": "application/json", "stripe-signature": f"t={stamp},v1={digest}",
        })


async def test_signed_paid_http_commits_one_credit_and_intent_then_acknowledges_duplicate(topup_db, financial_app):
    envelope = topup_fixtures.event(topup_db, "http_paid")
    response = await deliver(financial_app, envelope)
    assert response.status_code == 200 and response.json()["status"] == "handled"
    assert response.headers.get("x-request-id")
    provider_reads = len(topup_db.data.calls)
    assert provider_reads > 0
    duplicate = await deliver(financial_app, envelope)
    assert duplicate.status_code == 200 and duplicate.json()["status"] == "duplicate"
    assert len(topup_db.data.calls) == provider_reads
    result = await topup_fixtures.state(topup_db)
    assert result["allocated"] == result["enforced"] == 1250 and result["order"] == "paid"
    assert len(result["ledger"]) == len(result["notifications"]) == len(result["receipts"]) == 1
    assert result["ledger"][0]["minutes_delta"] == 250
    assert result["ledger"][0]["amount_cents"] == 2500
    assert result["notifications"][0]["status"] == "pending"
    assert result["receipts"][0]["state"] == "completed"
    async with acquire_with_tenant(topup_db.db.pool, None) as conn:
        assert await conn.fetchval("SELECT legacy_claim FROM processed_webhook_events WHERE event_id=$1", envelope["id"]) is False


async def test_signed_http_notification_failure_rolls_back_credit_then_same_event_recovers(topup_db, financial_app, monkeypatch):
    from app.domain.services import billing_webhook_notifications as notifications

    original = notifications.store_notifications
    failed = False

    async def fail_after_insert(*args, **kwargs):
        nonlocal failed
        await original(*args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError("Synthetic failure after notification SQL")

    monkeypatch.setattr(notifications, "store_notifications", fail_after_insert)
    envelope = topup_fixtures.event(topup_db, "http_retry")
    response = await deliver(financial_app, envelope)
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    result = await topup_fixtures.state(topup_db)
    assert result["allocated"] == result["enforced"] == 1000 and result["order"] == "pending"
    assert result["ledger"] == result["notifications"] == []
    assert result["receipts"][0]["state"] == "failed"
    retry = await deliver(financial_app, envelope)
    assert retry.status_code == 200 and retry.json()["status"] == "handled"
    result = await topup_fixtures.state(topup_db)
    assert result["allocated"] == result["enforced"] == 1250 and result["order"] == "paid"
    assert len(result["ledger"]) == len(result["notifications"]) == len(result["receipts"]) == 1
    assert result["notifications"][0]["status"] == "pending"
    assert result["receipts"][0]["state"] == "completed"
    async with acquire_with_tenant(topup_db.db.pool, None) as conn:
        assert await conn.fetchval("SELECT attempt_count FROM processed_webhook_events WHERE event_id=$1", envelope["id"]) == 2


async def test_signed_http_wrong_customer_requires_review_without_credit(topup_db, financial_app):
    async with acquire_with_tenant(topup_db.db.admin, None) as conn:
        await conn.execute("UPDATE tenants SET stripe_customer_id=$2 WHERE id=$1", topup_db.db.tenant, "cus_other_" + topup_db.db.tenant.hex)
    envelope = topup_fixtures.event(topup_db, "http_wrong_customer")
    response = await deliver(financial_app, envelope)
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    reads = len(topup_db.data.calls)
    assert reads > 0
    retry = await deliver(financial_app, envelope)
    assert retry.status_code == 503 and len(topup_db.data.calls) == reads
    result = await topup_fixtures.state(topup_db)
    assert result["allocated"] == result["enforced"] == 1000 and result["order"] == "pending"
    assert result["ledger"] == result["notifications"] == []
    assert len(result["receipts"]) == 1 and result["receipts"][0]["state"] == "needs_review"


async def test_reviewed_legacy_http_retry_recovers_credit_without_resending_uncertain_email(topup_db, financial_app):
    envelope = topup_fixtures.event(topup_db, "http_legacy")
    topup_db.verified_events[envelope["id"]] = envelope
    async with acquire_with_tenant(topup_db.db.pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type) VALUES($1,$2)", envelope["id"], envelope["type"])
    assert (await deliver(financial_app, envelope)).status_code == 503
    assert (await topup_fixtures.state(topup_db))["ledger"] == []
    billing = BillingService(SimpleNamespace(pool=topup_db.db.pool))
    review = await BillingReconciliation(topup_db.db.pool, billing).authorize_retry(
        envelope["id"], operator="synthetic operator",
        reason="Verified provider payment and local ledger; legacy email delivery remains unknown.",
    )
    assert review["financial_effects_applied"] is False
    assert (await topup_fixtures.state(topup_db))["ledger"] == []
    response = await deliver(financial_app, envelope)
    assert response.status_code == 200 and response.json()["status"] == "handled"
    result = await topup_fixtures.state(topup_db)
    assert result["allocated"] == result["enforced"] == 1250 and result["order"] == "paid"
    assert len(result["ledger"]) == len(result["notifications"]) == 1
    assert result["notifications"][0]["status"] == "unknown"
    async with acquire_with_tenant(topup_db.db.pool, None) as conn:
        assert await conn.fetchval("SELECT legacy_claim FROM processed_webhook_events WHERE event_id=$1", envelope["id"]) is True
        assert await conn.fetchval("SELECT last_error_code FROM billing_webhook_notifications WHERE event_id=$1", envelope["id"]) == "legacy_delivery_unverified"
    sender = AsyncMock(side_effect=AssertionError("Legacy delivery must not be resent"))
    assert await drain_billing_notifications(topup_db.db.pool, service=smtp_service(), sender=sender) == 0
    sender.assert_not_awaited()
