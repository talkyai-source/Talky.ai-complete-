"""Billing email intents commit with billing, but delivery never replays money.

`accepted` means transport acceptance, not inbox delivery. A send whose outcome
is unknown is fenced for operator reconciliation; it is never blindly retried.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging

from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)


async def store_notifications(conn, event_id, event_type, notifications, *, legacy_claim=False):
    for item in notifications:
        uncertain = legacy_claim or item.get("prior_delivery_unknown", False)
        status = "unknown" if uncertain else ("pending" if item.get("recipient") else "recipient_missing")
        await conn.execute(
            """INSERT INTO billing_webhook_notifications
               (delivery_key,event_id,event_type,tenant_id,kind,recipient,subject,body,status,last_error_code)
               VALUES($1,$2,$3,$4::uuid,$5,$6,$7,$8,$9,$10)
               ON CONFLICT(delivery_key) DO NOTHING""",
            item["delivery_key"],
            event_id,
            event_type,
            item.get("tenant_id"),
            item["kind"],
            item.get("recipient"),
            item["subject"],
            item["body"],
            status,
            "legacy_delivery_unverified" if uncertain else None,
        )


def preflight(service) -> str | None:
    if not service.email_enabled:
        return "email_disabled"
    provider = service.email_provider
    if provider == "sendgrid":
        if not service.sendgrid_api_key:
            return "sendgrid_not_configured"
        if importlib.util.find_spec("sendgrid") is None:
            return "sendgrid_not_installed"
    elif provider == "smtp":
        if not (service.smtp_host and service.smtp_user and service.smtp_password):
            return "smtp_not_configured"
    elif provider == "ses":
        if not (service.aws_access_key and service.aws_secret_key):
            return "ses_not_configured"
        if importlib.util.find_spec("boto3") is None:
            return "ses_not_installed"
    else:
        return "unknown_email_provider"
    return None


async def _send(service, row):
    # Existing adapters contain synchronous SDK I/O. Isolate those calls from
    # the worker loop, bound their sockets, and retain uncertainty on timeout.
    service.delivery_timeout_seconds = 8
    return await asyncio.wait_for(
        asyncio.to_thread(
            lambda: asyncio.run(
                service.send_email(
                    to_email=row["recipient"],
                    subject=row["subject"],
                    html_body=row["body"],
                )
            )
        ),
        timeout=25,
    )


async def drain_billing_notifications(pool, *, service=None, sender=None, limit=10):
    if service is None:
        from app.domain.services.notification_service import NotificationService

        service = NotificationService()
    sender = sender or _send
    processed = 0
    # Retain crash evidence. Age only changes the label, never grants resend.
    async with acquire_with_tenant(pool, None, timeout=5) as conn:
        await conn.execute(
            """UPDATE billing_webhook_notifications SET status='unknown',
               last_error_code='send_interrupted',updated_at=now()
               WHERE status='sending' AND started_at < now()-interval '5 minutes'"""
        )
    for _ in range(max(0, min(limit, 25))):
        async with acquire_with_tenant(pool, None, timeout=5) as conn:
            row = await conn.fetchrow(
                """SELECT * FROM billing_webhook_notifications
                   WHERE status='pending' OR (status='failed_before_send'
                     AND attempt_count<5 AND updated_at<now()-interval '5 minutes')
                   ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            if row is None:
                break
            row = dict(row)
            error = preflight(service)
            if not row["recipient"]:
                status, error = "recipient_missing", "recipient_missing"
            else:
                status = "failed_before_send" if error else "sending"
            await conn.execute(
                """UPDATE billing_webhook_notifications SET status=$2,
                   attempt_count=attempt_count+1,last_error_code=$3,updated_at=now(),
                   started_at=CASE WHEN $2='sending' THEN now() ELSE started_at END
                   WHERE id=$1""",
                row["id"],
                status,
                error,
            )
        processed += 1
        if status != "sending":
            continue
        # The sending fence above is committed BEFORE the first external call.
        try:
            result = await sender(service, row)
            accepted = result.get("status") == "success"
            status = "accepted" if accepted else "unknown"
            error = None if accepted else "send_outcome_unconfirmed"
            message_id = result.get("message_id") if accepted else None
            # These legacy adapters fabricate these IDs, so they cannot be
            # represented as provider evidence.
            if message_id and message_id.startswith(("smtp_", "sendgrid_")):
                message_id = None
        except Exception as exc:
            status, error, message_id = "unknown", "send_outcome_unconfirmed", None
            logger.warning(
                "Billing notification outcome unknown id=%s error_type=%s",
                row["id"],
                type(exc).__name__,
            )
        async with acquire_with_tenant(pool, None, timeout=5) as conn:
            await conn.execute(
                """UPDATE billing_webhook_notifications SET status=$2,last_error_code=$3,
                   provider_message_id=$4,updated_at=now(),
                   accepted_at=CASE WHEN $2='accepted' THEN now() ELSE accepted_at END
                   WHERE id=$1 AND status='sending'""",
                row["id"],
                status,
                error,
                message_id,
            )
    return processed
