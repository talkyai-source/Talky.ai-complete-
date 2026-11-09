"""Durable campaign/allowance alerts; external email never holds a transaction.

Call and qualification producers use their existing transaction. Historical
activity is not bulk-notified. Only confirmed transport acceptance is accepted;
an interrupted or unconfirmed send is fenced for operator review, never replayed.
"""
from __future__ import annotations

import asyncio
from html import escape
import json
import logging
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.core.db_utils import acquire_with_tenant
from app.domain.services.email_service import frontend_link
from app.domain.services.minutes_quota import MinutesStatus, compute_minutes_status

logger = logging.getLogger(__name__)
FAILURE_THRESHOLD = 5
_MAX_ALERT_RECIPIENTS = 10
_CONNECTED = frozenset({"answered", "goal_achieved", "goal_not_achieved", "voicemail"})
_FAILED = frozenset({"no_answer", "busy", "failed", "timeout", "spam", "invalid",
                     "unavailable", "disconnected", "rejected"})


async def _enqueue(conn, tenant_id, key, kind, title, description, *, campaign_id=None, call_id=None, lead_id=None):
    """Both visible event and mail intent commit with the source transition."""
    event_id = uuid5(NAMESPACE_URL, f"talklee:campaign-alert:{tenant_id}:{key}")
    # D4-9: every active tenant admin gets the alert. No single "owner" is
    # guessed among several (F3-08); with none, the intent is kept as
    # recipient_missing. One admin keeps the plain key; several get one
    # row each, so a repeat producer still adds nothing.
    admins = await conn.fetch(
        """SELECT id, email FROM user_profiles
           WHERE tenant_id=$1::uuid AND role='tenant_admin' AND is_active IS NOT FALSE
             AND email IS NOT NULL
           ORDER BY email LIMIT $2""", tenant_id, _MAX_ALERT_RECIPIENTS,
    )
    if len(admins) == 1:
        deliveries = [(key, admins[0]["email"])]
    else:
        deliveries = [(f"{key}:{admin['id']}", admin["email"]) for admin in admins] or [(key, None)]
    metadata = {"kind": kind}
    for name, value in (("related_campaign_id", campaign_id), ("call_id", call_id), ("lead_id", lead_id)):
        if value:
            metadata[name] = str(value)
    link = frontend_link("/billing" if kind == "low_minutes" else "/campaigns")
    body = f"<p>{escape(description)}</p><p><a href=\"{escape(link, quote=True)}\">Review in your dashboard</a></p>"
    await conn.execute(
        """INSERT INTO security_events
           (event_id,tenant_id,event_type,severity,status,title,description,evidence,alert_type,detection_source)
           VALUES($1,$2::uuid,$3,$4,'open',$5,$6,$7::jsonb,$8,'campaign_alerts')
           ON CONFLICT(event_id) DO NOTHING""",
        event_id, tenant_id, kind, "INFO" if kind == "qualified_lead" else "MEDIUM",
        title, description, json.dumps(metadata), "System" if kind == "low_minutes" else "Campaign",
    )
    for delivery_key, recipient in deliveries:
        await conn.execute(
            """INSERT INTO campaign_alert_outbox
               (id,tenant_id,event_id,delivery_key,kind,recipient,subject,body,status,last_error_code)
               VALUES($1,$2::uuid,$3,$4,$5,$6,$7,$8,$9,$10)
               ON CONFLICT(tenant_id,delivery_key) DO NOTHING""",
            uuid4(), tenant_id, event_id, delivery_key, kind, recipient, title, body,
            "pending" if recipient else "recipient_missing", None if recipient else "recipient_missing",
        )


async def record_terminal_call(conn, tenant_id, call_id, *, threshold=FAILURE_THRESHOLD):
    """Count each real outbound completed call once, in settlement lock order.

    A campaign job deferral is not a call. Repeat provider callbacks cannot claim
    the same call again. Connected outcomes reset the streak; unknown outcomes
    do not manufacture failures. This is independent of terminal-lead counters.
    """
    if type(threshold) is not int or not 1 <= threshold <= 1000:
        raise ValueError("Campaign failure threshold must be between 1 and 1000")
    row = await conn.fetchrow(
        """UPDATE calls SET campaign_alert_processed_at=now()
           WHERE id=$1 AND tenant_id=$2::uuid AND campaign_alert_processed_at IS NULL
             AND status='completed' AND direction='outbound' AND is_test IS NOT TRUE
             AND campaign_id IS NOT NULL
             AND EXISTS (SELECT 1 FROM campaigns p WHERE p.id=calls.campaign_id AND p.tenant_id=$2::uuid)
           RETURNING campaign_id,outcome""", call_id, tenant_id,
    )
    if row is None:
        return
    outcome = str(row["outcome"] or "")
    if outcome not in _CONNECTED | _FAILED:
        return
    campaign_id = row["campaign_id"]
    await conn.execute(
        """INSERT INTO campaign_alert_state(tenant_id,campaign_id)
           VALUES($1::uuid,$2) ON CONFLICT(tenant_id,campaign_id) DO NOTHING""", tenant_id, campaign_id,
    )
    state = await conn.fetchrow(
        """SELECT failure_count,failure_episode FROM campaign_alert_state
           WHERE tenant_id=$1::uuid AND campaign_id=$2 FOR UPDATE""", tenant_id, campaign_id,
    )
    count = 0 if outcome in _CONNECTED else int(state["failure_count"]) + 1
    episode = (state["failure_episode"] or uuid4()) if count else None
    await conn.execute(
        """UPDATE campaign_alert_state SET failure_count=$3,failure_episode=$4,updated_at=now()
           WHERE tenant_id=$1::uuid AND campaign_id=$2""", tenant_id, campaign_id, count, episode,
    )
    if count == threshold:
        await _enqueue(conn, tenant_id, f"failures:{campaign_id}:{episode}", "campaign_failures",
                       "Consecutive campaign call failures", f"A campaign has {count} consecutive failed calls. Review it before continuing.",
                       campaign_id=campaign_id, call_id=call_id)


async def record_low_minutes(conn, tenant_id, status: MinutesStatus):
    """Use the canonical meter, preserving unknown state instead of rearming."""
    if status.state == "unavailable":
        return
    if status.state == "unlimited" or status.unlimited:
        low = False
    elif (status.state == "known" and type(status.allocated) is int and status.allocated >= 0
          and type(status.remaining_minutes) is int and status.remaining_minutes >= 0):
        low = status.remaining_minutes < 20 or status.remaining_minutes * 10 < status.allocated
    else:
        return
    await conn.execute(
        """INSERT INTO tenant_alert_state(tenant_id) VALUES($1::uuid)
           ON CONFLICT(tenant_id) DO NOTHING""", tenant_id,
    )
    state = await conn.fetchrow(
        """SELECT low_minutes_active,low_minutes_episode FROM tenant_alert_state
           WHERE tenant_id=$1::uuid FOR UPDATE""", tenant_id,
    )
    if bool(state["low_minutes_active"]) == low:
        return
    episode = uuid4() if low else None
    await conn.execute(
        """UPDATE tenant_alert_state SET low_minutes_active=$2,low_minutes_episode=$3,updated_at=now()
           WHERE tenant_id=$1::uuid""", tenant_id, low, episode,
    )
    if low:
        await _enqueue(conn, tenant_id, f"minutes:{episode}", "low_minutes", "Your minutes are running low",
                       f"Your account has {status.remaining_minutes} minutes remaining. Review your allowance in Billing.")


async def record_qualified_lead(conn, tenant_id, lead_id, call_id):
    row = await conn.fetchrow(
        """SELECT c.campaign_id FROM leads l JOIN calls c
             ON c.id=l.qualified_call_id AND c.lead_id=l.id AND c.tenant_id=$1::uuid
           WHERE l.tenant_id=$1::uuid AND l.id=$2 AND c.id=$3 AND l.is_lead=TRUE
             AND (c.campaign_id IS NULL OR EXISTS (
                 SELECT 1 FROM campaigns p WHERE p.id=c.campaign_id AND p.tenant_id=$1::uuid))""",
        tenant_id, lead_id, call_id,
    )
    if row is not None:
        await _enqueue(conn, tenant_id, f"lead:{lead_id}:{call_id}", "qualified_lead", "A new qualified lead is ready",
                       "A contact has qualified as a lead. Review the call and follow up in your dashboard.",
                       campaign_id=row["campaign_id"], call_id=call_id, lead_id=lead_id)


def _preflight(service):
    if not service.email_enabled:
        return "email_disabled"
    if not (service.smtp_host and service.smtp_user and service.smtp_password):
        return "smtp_not_configured"
    return None


async def _finish(pool, tenant_id, identity, token, status):
    async with acquire_with_tenant(pool, tenant_id, timeout=5) as conn:
        await conn.execute(
            """UPDATE campaign_alert_outbox SET status=$4,last_error_code=$5,updated_at=now(),
                 accepted_at=CASE WHEN $4='accepted' THEN now() ELSE accepted_at END
               WHERE tenant_id=$1::uuid AND id=$2 AND claim_token=$3 AND status='sending'""",
            tenant_id, identity, token, status, None if status == "accepted" else "send_outcome_unconfirmed",
        )


async def deliver_tenant_alerts(pool, tenant_id, *, service=None, limit=10):
    if service is None:
        from app.domain.services.notification_service import NotificationService
        service = NotificationService()
    processed = 0
    async with acquire_with_tenant(pool, tenant_id, timeout=5) as conn:
        await conn.execute(
            """UPDATE campaign_alert_outbox SET status='unknown',last_error_code='send_interrupted',updated_at=now()
               WHERE tenant_id=$1::uuid AND status='sending' AND started_at<now()-interval '5 minutes'""", tenant_id,
        )
    for _ in range(max(0, min(limit, 25))):
        async with acquire_with_tenant(pool, tenant_id, timeout=5) as conn:
            row = await conn.fetchrow(
                """SELECT * FROM campaign_alert_outbox WHERE tenant_id=$1::uuid
                   AND (status='pending' OR (status='failed_before_send' AND attempt_count<5
                        AND updated_at<now()-interval '5 minutes'))
                   ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""", tenant_id,
            )
            if row is None:
                break
            token = uuid4()
            error = _preflight(service)
            status = "failed_before_send" if error else "sending"
            if not row["recipient"]:
                status, error = "recipient_missing", "recipient_missing"
            await conn.execute(
                """UPDATE campaign_alert_outbox SET status=$3,claim_token=$4,attempt_count=attempt_count+1,
                     last_error_code=$5,updated_at=now(),
                     started_at=CASE WHEN $3='sending' THEN now() ELSE started_at END
                   WHERE tenant_id=$1::uuid AND id=$2 AND status IN ('pending','failed_before_send')""",
                tenant_id, row["id"], status, token, error,
            )
        processed += 1
        if status != "sending":
            continue
        cancelled = None
        status = "unknown"
        try:
            result = await asyncio.wait_for(service.send_email(
                to_email=row["recipient"], subject=row["subject"], html_body=row["body"]), timeout=25)
            if isinstance(result, dict) and result.get("status") == "success":
                status = "accepted"
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception as exc:
            logger.warning("Campaign alert send outcome unknown: %s", type(exc).__name__)
        # If persistence itself fails, the committed sending fence still forbids
        # replay. A stale-claim sweep labels it unknown, never pending.
        await _finish(pool, tenant_id, row["id"], token, status)
        if cancelled is not None:
            raise cancelled
    return processed


async def process_alert_tick(pool, *, after_tenant=None, limit=100, service=None):
    """One bounded tenant-directory page; all subsequent work is tenant-scoped."""
    async with acquire_with_tenant(pool, None, timeout=5) as conn:
        # Deliberate system directory enumeration, not a tenant-data exemption.
        tenants = await conn.fetch(
            """SELECT id FROM tenants WHERE ($1::uuid IS NULL OR id>$1::uuid)
               ORDER BY id LIMIT $2""", after_tenant, max(1, min(limit, 100)),
        )
    remaining_deliveries = 10
    for tenant in tenants:
        tenant_id = str(tenant["id"])
        try:
            async with acquire_with_tenant(pool, tenant_id, timeout=5) as conn:
                status = await asyncio.wait_for(compute_minutes_status(conn, tenant_id), timeout=5)
                await record_low_minutes(conn, tenant_id, status)
        except Exception as exc:
            logger.warning("Campaign allowance observation unavailable: %s", type(exc).__name__)
        if remaining_deliveries:
            try:
                processed = await deliver_tenant_alerts(pool, tenant_id, service=service, limit=remaining_deliveries)
                remaining_deliveries -= processed
            except Exception as exc:
                # The exception may follow external acceptance but precede its
                # final DB write. Consumption is unknown, so this tick cannot
                # grant a fresh send allowance to later tenants.
                remaining_deliveries = 0
                logger.warning("Campaign alert delivery deferred: %s", type(exc).__name__)
    return str(tenants[-1]["id"]) if tenants else None
