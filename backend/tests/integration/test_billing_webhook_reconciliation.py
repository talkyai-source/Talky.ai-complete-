"""Operator authorization is recorded; financial recovery remains transactional."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_webhook_reconciliation import BillingReconciliation
from app.domain.services.billing_webhooks import BillingWebhookProcessor, BillingWebhookReviewRequired
from tests.integration import test_billing_webhook_migration as migration_fixtures
from tests.integration.test_billing_webhook_receipts import effect, event, inspect, queue_notice

pytestmark = pytest.mark.integration
webhook_db = migration_fixtures.webhook_db


def billing_fixture(fixture, verified_event=None, current=None):
    verified_event = verified_event or event(fixture)
    current = current or {"id": "in_synthetic", "livemode": False, "status": "paid"}

    async def provider(resource, method, identity):
        assert method == "retrieve"
        if resource == "Event":
            return deepcopy(verified_event)
        assert resource == "Invoice" and identity == verified_event["data"]["object"]["id"]
        return deepcopy(current)

    async def handler(conn, evt):
        await effect(conn, evt)
        return {"status": "handled"}

    return SimpleNamespace(billing_mode="test", _stripe_call=AsyncMock(side_effect=provider),
                           _apply_webhook_event=AsyncMock(side_effect=handler))


async def seed_legacy(fixture):
    previous = datetime(2020, 1, 2, 3, 4, tzinfo=UTC)
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("""INSERT INTO processed_webhook_events(event_id,event_type,processed_at)
            VALUES($1,'invoice.paid',$2)""", fixture.prefix, previous)
    return previous


async def test_review_authorization_preserves_legacy_evidence_and_applies_nothing_until_retry(webhook_db):
    f = webhook_db
    previous = await seed_legacy(f)
    billing = billing_fixture(f)
    recovery = BillingReconciliation(f.pool, billing)
    with pytest.raises(ValueError, match="recorded reconciliation"):
        await recovery.retry(f.prefix)
    billing._apply_webhook_event.assert_not_awaited()
    result = await recovery.authorize_retry(f.prefix, operator="synthetic operator", reason="Provider and local receipts reviewed; resume idempotent state update.")
    assert result["financial_effects_applied"] is False and result["state"] == "pending"
    row, effects, notices = await inspect(f)
    assert row["processed_at"] == previous and row["completed_at"] is None
    assert row["state"] == "pending" and row["provider_mode"] == "test"
    assert row["legacy_claim"] is True
    assert row["payload_hash"] and row["event_payload"]["id"] == f.prefix
    assert effects == 0 and notices == []
    async with acquire_with_tenant(f.pool, None) as conn:
        log = await conn.fetchrow("SELECT * FROM billing_webhook_review_log WHERE event_id=$1", f.prefix)
        assert log["operator"] == "synthetic operator" and log["decision"] == "authorize_idempotent_retry"
        assert "Provider and local receipts" in log["reason"]
    billing._apply_webhook_event.assert_not_awaited()
    assert (await recovery.retry(f.prefix))["status"] == "handled"
    assert (await recovery.retry(f.prefix))["status"] == "duplicate"
    assert billing._apply_webhook_event.await_count == 1 and (await inspect(f))[1] == 1


@pytest.mark.parametrize("operator,reason", [("", "Substantive review reason"), ("operator", "short")])
async def test_retry_authorization_requires_operator_and_substantive_reason(webhook_db, operator, reason):
    f = webhook_db
    await seed_legacy(f)
    billing = billing_fixture(f)
    with pytest.raises(ValueError, match="operator"):
        await BillingReconciliation(f.pool, billing).authorize_retry(f.prefix, operator=operator, reason=reason)
    billing._stripe_call.assert_not_awaited()
    assert (await inspect(f))[0]["state"] == "legacy_unverified"


@pytest.mark.parametrize("mismatch", ["event_id", "mode", "payload", "account", "current_object"])
async def test_provider_identity_conflict_cannot_authorize_review_receipt(webhook_db, mismatch):
    f = webhook_db
    handler = AsyncMock(side_effect=BillingWebhookReviewRequired("synthetic_manual_review"))
    with pytest.raises(BillingWebhookReviewRequired):
        await BillingWebhookProcessor(f.pool).process(event(f), handler)
    supplied = deepcopy(event(f))
    current = {"id": "in_synthetic", "livemode": False, "status": "paid"}
    if mismatch == "event_id":
        supplied["id"] += "_different"
    elif mismatch == "mode":
        supplied["livemode"] = True
    elif mismatch == "payload":
        supplied["data"]["object"]["description"] = "Altered receipt"
    elif mismatch == "account":
        supplied["account"] = "acct_unexpected"
    else:
        current["id"] = "in_other_synthetic"
    billing = billing_fixture(f, supplied, current)
    recovery = BillingReconciliation(f.pool, billing)
    with pytest.raises(ValueError, match="recorded reconciliation"):
        await recovery.retry(f.prefix)
    with pytest.raises(ValueError):
        await recovery.authorize_retry(f.prefix, operator="synthetic operator", reason="Compare provider evidence before any retry.")
    billing._apply_webhook_event.assert_not_awaited()
    row, effects, notices = await inspect(f)
    assert row["state"] == "needs_review" and row["completed_at"] is None
    assert effects == 0 and notices == []
    async with acquire_with_tenant(f.pool, None) as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM billing_webhook_review_log WHERE event_id=$1", f.prefix) == 0


@pytest.mark.parametrize("status", ["failed_before_send", "unknown", "sending", "accepted", "recipient_missing", "superseded"])
async def test_operator_can_retry_only_proved_unsent_notification(webhook_db, status):
    f = webhook_db
    await queue_notice(f)
    async with acquire_with_tenant(f.pool, None) as conn:
        identity = await conn.fetchval("""UPDATE billing_webhook_notifications
            SET status=$2,last_error_code='synthetic_failure' WHERE event_id=$1 RETURNING id""", f.prefix, status)
    billing = billing_fixture(f)
    recovery = BillingReconciliation(f.pool, billing)
    kwargs = {"operator": "synthetic operator", "reason": "Sender configuration repaired; known unsent delivery reviewed."}
    if status == "failed_before_send":
        assert await recovery.retry_unsent_notification(identity, **kwargs) == {
            "notification_id": str(identity), "status": "pending", "sent": False,
        }
    else:
        with pytest.raises(ValueError, match="Only a known pre-send failure"):
            await recovery.retry_unsent_notification(identity, **kwargs)
    row, effects, notices = await inspect(f)
    assert row["state"] == "completed" and effects == 1
    assert notices[0]["status"] == ("pending" if status == "failed_before_send" else status)
    assert notices[0]["last_error_code"] == (None if status == "failed_before_send" else "synthetic_failure")
    billing._stripe_call.assert_not_awaited()
    billing._apply_webhook_event.assert_not_awaited()
    async with acquire_with_tenant(f.pool, None) as conn:
        reviews = await conn.fetch("SELECT decision,reason FROM billing_webhook_review_log WHERE event_id=$1 AND decision='retry_unsent_notification'", f.prefix)
        assert len(reviews) == (1 if status == "failed_before_send" else 0)
        if reviews:
            assert str(identity) in reviews[0]["reason"]
