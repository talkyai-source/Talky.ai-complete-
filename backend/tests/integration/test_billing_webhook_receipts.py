"""Real receipt transactions, concurrency and notification fences; no API calls."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_webhooks import (
    BillingWebhookProcessor, BillingWebhookRetryable, BillingWebhookReviewRequired,
)
from app.domain.services.billing_webhook_notifications import drain_billing_notifications
from tests.integration import test_billing_webhook_migration as migration_fixtures

pytestmark = pytest.mark.integration
webhook_db = migration_fixtures.webhook_db


def event(fixture, suffix=""):
    return {"id": fixture.prefix + suffix, "object": "event", "type": "invoice.paid",
            "livemode": False, "created": 1700000000, "api_version": "2025-09-30.clover",
            "data": {"object": {"id": "in_synthetic", "object": "invoice"}}}


def notice(fixture, *, recipient="synthetic@example.com", key=None):
    return {"delivery_key": key or fixture.prefix, "tenant_id": str(fixture.tenant),
            "kind": "receipt", "recipient": recipient, "subject": "Synthetic receipt",
            "body": "Synthetic acceptance fixture"}


async def effect(conn, evt):
    await conn.execute("""INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
        VALUES($1,'synthetic handler','test_effect','Synthetic transaction effect')""", evt["id"])


async def inspect(fixture, identity=None):
    async with acquire_with_tenant(fixture.pool, None) as conn:
        identity = identity or fixture.prefix
        row = await conn.fetchrow("SELECT * FROM processed_webhook_events WHERE event_id=$1", identity)
        effects = await conn.fetchval("SELECT COUNT(*) FROM billing_webhook_review_log WHERE event_id=$1 AND decision='test_effect'", identity)
        notifications = await conn.fetch("SELECT * FROM billing_webhook_notifications WHERE event_id=$1", identity)
        return row, effects, notifications


async def test_failed_business_transaction_retries_then_only_completed_is_duplicate(webhook_db):
    f = webhook_db
    attempts = 0

    async def handler(conn, evt):
        nonlocal attempts
        attempts += 1
        await effect(conn, evt)
        if attempts == 1:
            raise RuntimeError("Synthetic failure after a durable mutation statement")
        return {"status": "handled", "tenant_id": str(f.tenant), "notifications": [notice(f)]}

    with pytest.raises(BillingWebhookRetryable):
        await BillingWebhookProcessor(f.pool).process(event(f), handler)
    row, count, notifications = await inspect(f)
    assert row["state"] == "failed" and row["completed_at"] is None
    assert count == 0 and notifications == []
    assert (await BillingWebhookProcessor(f.pool).process(event(f), handler))["status"] == "handled"
    row, count, notifications = await inspect(f)
    assert row["state"] == "completed" and row["completed_at"] is not None
    assert row["attempt_count"] == 2 and count == 1 and len(notifications) == 1
    assert (await BillingWebhookProcessor(f.pool).process(event(f), handler))["status"] == "duplicate"
    assert attempts == 2


async def test_notification_intent_failure_rolls_back_business_and_completion(webhook_db):
    f = webhook_db

    async def bad_intent(conn, evt):
        await effect(conn, evt)
        return {"status": "handled", "notifications": [{"delivery_key": f.prefix}]}

    with pytest.raises(BillingWebhookRetryable):
        await BillingWebhookProcessor(f.pool).process(event(f), bad_intent)
    row, count, notifications = await inspect(f)
    assert row["state"] == "failed" and count == 0 and notifications == []


async def test_concurrent_delivery_cannot_acknowledge_in_progress_as_completed(webhook_db):
    f = webhook_db
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(conn, evt):
        nonlocal calls
        calls += 1
        await effect(conn, evt)
        entered.set()
        await release.wait()
        return {"status": "handled"}

    first = asyncio.create_task(BillingWebhookProcessor(f.pool).process(event(f), handler))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises(BillingWebhookRetryable, match="event_busy"):
            await asyncio.wait_for(BillingWebhookProcessor(f.pool).process(event(f), handler), 3)
        assert calls == 1
        release.set()
        assert (await first)["status"] == "handled"
    finally:
        release.set()
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
    assert (await inspect(f))[1] == 1


async def test_cancelled_worker_rolls_back_and_restart_releases_transaction_lock(webhook_db):
    f = webhook_db
    entered = asyncio.Event()

    async def interrupted(conn, evt):
        await effect(conn, evt)
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(BillingWebhookProcessor(f.pool).process(event(f), interrupted))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row, count, _ = await inspect(f)
    assert row["state"] == "pending" and count == 0

    async def recovered(conn, evt):
        await effect(conn, evt)
        return {"status": "handled"}

    result = await asyncio.wait_for(BillingWebhookProcessor(f.pool).process(event(f), recovered), 3)
    assert result["status"] == "handled" and (await inspect(f))[1] == 1


async def test_committed_business_with_lost_response_is_not_replayed(webhook_db):
    f = webhook_db

    async def handler(conn, evt):
        await effect(conn, evt)
        return {"status": "handled", "notifications": [notice(f)]}

    await BillingWebhookProcessor(f.pool).process(event(f), handler)  # response deliberately lost
    forbidden = AsyncMock(side_effect=AssertionError("Completed work must not replay"))
    retry = event(f)
    retry["pending_webhooks"] = 0
    result = await BillingWebhookProcessor(f.pool).process(retry, forbidden)
    assert result["status"] == "duplicate"
    forbidden.assert_not_awaited()
    _, count, notices = await inspect(f)
    assert count == 1 and len(notices) == 1


async def test_unavailable_receipt_storage_never_runs_handler(webhook_db):
    class UnavailablePool:
        def acquire(self, **kwargs):
            raise ConnectionError("Synthetic storage unavailable")

    handler = AsyncMock()
    with pytest.raises(BillingWebhookRetryable):
        await BillingWebhookProcessor(UnavailablePool()).process(event(webhook_db), handler)
    handler.assert_not_awaited()
    assert (await inspect(webhook_db))[0] is None


async def test_legacy_claim_needs_review_and_is_never_replayed_or_backfilled_completed(webhook_db):
    f = webhook_db
    async with acquire_with_tenant(f.pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type) VALUES($1,'invoice.paid')", f.prefix)
    handler = AsyncMock()
    with pytest.raises(BillingWebhookReviewRequired):
        await BillingWebhookProcessor(f.pool).process(event(f), handler)
    handler.assert_not_awaited()
    row, count, _ = await inspect(f)
    assert row["state"] == "legacy_unverified" and row["completed_at"] is None and count == 0


async def test_conflicting_event_content_cannot_replay_a_completed_identity(webhook_db):
    f = webhook_db
    handler = AsyncMock(return_value={"status": "ignored"})
    await BillingWebhookProcessor(f.pool).process(event(f), handler)
    conflicting = deepcopy(event(f))
    conflicting["data"]["object"]["id"] = "in_other_synthetic"
    with pytest.raises(BillingWebhookReviewRequired, match="event_identity_conflict"):
        await BillingWebhookProcessor(f.pool).process(conflicting, handler)
    assert handler.await_count == 1
    assert (await inspect(f))[0]["state"] == "completed"


def smtp_service(enabled=True):
    return SimpleNamespace(email_enabled=enabled, email_provider="smtp", smtp_host="synthetic.invalid",
                           smtp_user="synthetic", smtp_password="synthetic-unused")


async def queue_notice(f, *, recipient="synthetic@example.com"):
    async def handler(conn, evt):
        await effect(conn, evt)
        return {"status": "handled", "notifications": [notice(f, recipient=recipient)]}
    await BillingWebhookProcessor(f.pool).process(event(f), handler)


async def test_concurrent_notification_drains_send_one_committed_intent(webhook_db):
    f = webhook_db
    await queue_notice(f)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def sender(service, row):
        nonlocal calls
        calls += 1
        assert (await inspect(f))[2][0]["status"] == "sending"
        entered.set()
        await release.wait()
        return {"status": "success", "message_id": "synthetic_provider_receipt"}

    first = asyncio.create_task(drain_billing_notifications(f.pool, service=smtp_service(), sender=sender))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert await drain_billing_notifications(f.pool, service=smtp_service(), sender=sender) == 0
        release.set()
        await first
    finally:
        release.set()
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
    row, effects, notices = await inspect(f)
    assert calls == 1 and effects == 1 and row["state"] == "completed"
    assert notices[0]["status"] == "accepted" and notices[0]["accepted_at"] is not None
    assert notices[0]["provider_message_id"] == "synthetic_provider_receipt"


async def test_uncertain_send_is_recorded_and_never_automatically_resent(webhook_db):
    f = webhook_db
    await queue_notice(f)
    sender = AsyncMock(side_effect=RuntimeError("Synthetic lost acceptance response"))
    await drain_billing_notifications(f.pool, service=smtp_service(), sender=sender)
    await drain_billing_notifications(f.pool, service=smtp_service(), sender=sender)
    row, effects, notices = await inspect(f)
    assert row["state"] == "completed" and effects == 1 and sender.await_count == 1
    assert notices[0]["status"] == "unknown" and notices[0]["accepted_at"] is None


async def test_sender_crash_retains_fence_then_ages_to_unknown_without_resend(webhook_db):
    f = webhook_db
    await queue_notice(f)
    entered = asyncio.Event()

    async def interrupted(service, row):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(drain_billing_notifications(f.pool, service=smtp_service(), sender=interrupted))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await inspect(f))[2][0]["status"] == "sending"
    async with acquire_with_tenant(f.pool, None) as conn:
        await conn.execute("UPDATE billing_webhook_notifications SET started_at=now()-interval '6 minutes' WHERE event_id=$1", f.prefix)
    forbidden = AsyncMock(side_effect=AssertionError("A timed-out sender cannot be blindly replayed"))
    await drain_billing_notifications(f.pool, service=smtp_service(), sender=forbidden)
    forbidden.assert_not_awaited()
    assert (await inspect(f))[2][0]["status"] == "unknown"


async def test_disabled_email_is_failed_before_send_and_financial_event_stays_complete(webhook_db):
    f = webhook_db
    await queue_notice(f)
    sender = AsyncMock()
    await drain_billing_notifications(f.pool, service=smtp_service(False), sender=sender)
    sender.assert_not_awaited()
    row, effects, notices = await inspect(f)
    assert row["state"] == "completed" and effects == 1
    assert notices[0]["status"] == "failed_before_send" and notices[0]["last_error_code"] == "email_disabled"


async def test_missing_recipient_remains_visible_without_external_send(webhook_db):
    f = webhook_db
    await queue_notice(f, recipient=None)
    sender = AsyncMock()
    await drain_billing_notifications(f.pool, service=smtp_service(), sender=sender)
    sender.assert_not_awaited()
    row, effects, notices = await inspect(f)
    assert row["state"] == "completed" and effects == 1
    assert notices[0]["status"] == "recipient_missing"


async def test_superseded_unsent_notification_is_never_delivered(webhook_db):
    f = webhook_db
    await queue_notice(f)
    async with acquire_with_tenant(f.pool, None) as conn:
        await conn.execute("UPDATE billing_webhook_notifications SET status='superseded' WHERE event_id=$1", f.prefix)
    sender = AsyncMock(side_effect=AssertionError("Superseded notification must not send"))
    assert await drain_billing_notifications(f.pool, service=smtp_service(), sender=sender) == 0
    sender.assert_not_awaited()
    row, effects, notices = await inspect(f)
    assert row["state"] == "completed" and effects == 1 and notices[0]["status"] == "superseded"
