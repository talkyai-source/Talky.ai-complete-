"""Dismissing a needs_review receipt on a disposable PostgreSQL (needs TEST_DATABASE_URL at head 0095)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_webhook_reconciliation import BillingReconciliation
from app.domain.services.billing_webhooks import BillingWebhookProcessor
from tests.integration import test_billing_webhook_migration as migration_fixtures

pytestmark = pytest.mark.integration
webhook_db = migration_fixtures.webhook_db


async def test_dismiss_closes_one_receipt_audits_it_and_a_redelivery_is_acknowledged(webhook_db):
    f = webhook_db
    async with acquire_with_tenant(f.pool, None) as conn:
        await conn.execute("""INSERT INTO processed_webhook_events(event_id,event_type,state,provider_mode,legacy_claim)
            VALUES($1,'invoice.paid','needs_review','test',FALSE)""", f.prefix)
    recovery = BillingReconciliation(f.pool, object())
    reason = "QA test receipt reviewed; closed without replay."
    assert (await recovery.dismiss(f.prefix, operator="synthetic operator", reason=reason))["changed"] is True
    assert (await recovery.dismiss(f.prefix, operator="synthetic operator", reason=reason))["changed"] is False
    async with acquire_with_tenant(f.pool, None) as conn:
        state = await conn.fetchval("SELECT state FROM processed_webhook_events WHERE event_id=$1", f.prefix)
        logs = await conn.fetch("SELECT operator,decision FROM billing_webhook_review_log WHERE event_id=$1", f.prefix)
    assert state == "dismissed" and [(r["operator"], r["decision"]) for r in logs] == [("synthetic operator", "dismiss")]
    handler = AsyncMock()
    event = {"id": f.prefix, "type": "invoice.paid", "livemode": False, "created": 1700000000,
             "api_version": "2025-09-30.clover", "data": {"object": {"id": "in_x", "object": "invoice"}}}
    assert (await BillingWebhookProcessor(f.pool).process(event, handler))["status"] == "duplicate"
    handler.assert_not_awaited()
