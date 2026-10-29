"""Support emails and staff alerts, sent after the ticket transaction commits.

A failed send never undoes the ticket. Every attempt's outcome is recorded as
a ``notification`` ticket event (``email:ticket_created`` -> ``sent``/
``skipped``/``failed``). Customer emails never contain internal notes or the
body of a staff reply; they link back to the ticket.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.email_template_manager import EmailTemplate, get_email_template_manager
from app.domain.services.notification_service import get_notification_service
from app.domain.services.support import ticket_store
from app.domain.services.support.config import SupportSettings
from app.domain.services.support.vocabulary import ticket_display_number

logger = logging.getLogger(__name__)

_TEMPLATES = (
    EmailTemplate(
        name="support_ticket_created", variables=["number", "subject", "link"],
        description="Customer: a support ticket was opened.",
        subject_template="[{{ number }}] We received your support request",
        body_template=("Hi {{ name | default('there') }},\n\nWe received your request \"{{ subject }}\" "
                       "as ticket {{ number }}. Our support team will reply here:\n{{ link }}\n\n"
                       "You can add details or files to the ticket at any time.\n\nTalk-Lee Support"),
        body_html_template=("<p>Hi {{ name | default('there') }},</p><p>We received your request "
                            "&ldquo;{{ subject }}&rdquo; as ticket <strong>{{ number }}</strong>.</p>"
                            "<p><a href=\"{{ link }}\">Open your ticket</a> to follow it or add details.</p>"
                            "<p>Talk-Lee Support</p>")),
    EmailTemplate(
        name="support_staff_reply", variables=["number", "subject", "link"],
        description="Customer: support replied (the reply itself stays in the app).",
        subject_template="[{{ number }}] Support replied to your request",
        body_template=("Hi {{ name | default('there') }},\n\nOur support team replied to ticket {{ number }} "
                       "(\"{{ subject }}\"). Read the reply and respond here:\n{{ link }}\n\nTalk-Lee Support"),
        body_html_template=("<p>Hi {{ name | default('there') }},</p><p>Our support team replied to ticket "
                            "<strong>{{ number }}</strong> (&ldquo;{{ subject }}&rdquo;).</p>"
                            "<p><a href=\"{{ link }}\">Read the reply</a></p><p>Talk-Lee Support</p>")),
    EmailTemplate(
        name="support_ticket_resolved", variables=["number", "subject", "link"],
        description="Customer: ticket resolved, with a rating link.",
        subject_template="[{{ number }}] Your support request was resolved",
        body_template=("Hi {{ name | default('there') }},\n\nTicket {{ number }} (\"{{ subject }}\") was marked "
                       "resolved. If something is still wrong, reply within 7 days to reopen it.\n\n"
                       "How did we do? Rate the help you received:\n{{ link }}\n\nTalk-Lee Support"),
        body_html_template=("<p>Hi {{ name | default('there') }},</p><p>Ticket <strong>{{ number }}</strong> "
                            "(&ldquo;{{ subject }}&rdquo;) was marked resolved. If something is still wrong, "
                            "reply within 7 days to reopen it.</p><p><a href=\"{{ link }}\">Rate the help you "
                            "received</a></p><p>Talk-Lee Support</p>")),
    EmailTemplate(
        name="support_staff_alert", variables=["number", "priority", "category", "headline", "link"],
        description="Staff: urgent/high ticket or missed deadline.",
        subject_template="[{{ priority | upper }}] {{ headline }}: {{ number }}",
        body_template=("{{ headline }}: {{ number }}\nPriority: {{ priority }}\nCategory: {{ category }}\n"
                       "Tenant: {{ tenant }}\nSubject: {{ subject }}\n\n{{ link }}"),
        body_html_template=("<p><strong>{{ headline }}: {{ number }}</strong></p><p>Priority: {{ priority }}<br>"
                            "Category: {{ category }}<br>Tenant: {{ tenant }}<br>Subject: {{ subject }}</p>"
                            "<p><a href=\"{{ link }}\">Open in the staff console</a></p>")),
)


def _register_templates() -> Any:
    manager = get_email_template_manager()
    for template in _TEMPLATES:
        if manager.get_template(template.name) is None:
            manager.add_template(template)
    return manager


def _outcome(result: dict | None) -> str:
    status = (result or {}).get("status")
    return "sent" if status in {"success", "sent"} else "skipped" if status == "skipped" else "failed"


async def _contact(pool, ticket_id: UUID, tenant_id: str) -> dict | None:
    async with acquire_with_tenant(pool, None) as conn:
        row = await conn.fetchrow(
            """SELECT t.id, t.number, t.subject, t.priority, t.category, rq.email, rq.name,
                      tn.business_name AS tenant
               FROM support_tickets t JOIN tenants tn ON tn.id = t.tenant_id
               LEFT JOIN user_profiles rq ON rq.id = t.requester_user_id
               WHERE t.id = $1 AND t.tenant_id = $2""", ticket_id, tenant_id)
    return dict(row) if row else None


async def _record(pool, ticket_id: UUID, tenant_id: str, kind: str, outcome: str) -> None:
    try:
        async with acquire_with_tenant(pool, None) as conn:
            await ticket_store.insert_event(conn, ticket_id=ticket_id, tenant_id=tenant_id, actor_id=None,
                                            type="notification", from_value=kind, to_value=outcome)
    except Exception as exc:  # the send already happened; recording must not raise into the caller
        logger.warning("support_notification_record_failed kind=%s error_type=%s", kind, type(exc).__name__)


async def _email_customer(pool, ticket_id: UUID, tenant_id: str, template: str, settings: SupportSettings) -> str:
    contact = await _contact(pool, ticket_id, tenant_id)
    if contact is None:
        return "skipped"
    if not contact.get("email"):
        outcome = "skipped"
    else:
        rendered = _register_templates().render_email(
            template, name=contact.get("name"), number=ticket_display_number(contact["number"]),
            subject=contact["subject"], link=settings.ticket_url(ticket_id))
        try:
            outcome = _outcome(await get_notification_service().send_email(
                contact["email"], rendered.subject, rendered.body_html or rendered.body, text_body=rendered.body))
        except Exception as exc:
            logger.warning("support_email_failed template=%s error_type=%s", template, type(exc).__name__)
            outcome = "failed"
    await _record(pool, ticket_id, tenant_id, f"email:{template.removeprefix('support_')}", outcome)
    return outcome


async def alert_staff(pool, ticket_id: UUID, tenant_id: str, headline: str, settings: SupportSettings,
                      *, kind: str) -> None:
    """Slack (when configured) and the staff alert address (when set)."""
    contact = await _contact(pool, ticket_id, tenant_id)
    if contact is None:
        return
    number = ticket_display_number(contact["number"])
    link = f"{settings.app_base_url}/admin/support/tickets/{ticket_id}"
    service = get_notification_service()
    try:
        slack = _outcome(await service.send_slack_notification(
            title=f"{headline}: {number}", message=contact["subject"][:150],
            severity="critical" if contact["priority"] == "urgent" else "warning",
            fields={"Priority": contact["priority"], "Category": contact["category"],
                    "Tenant": contact.get("tenant") or "", "Ticket": link}))
    except Exception as exc:
        logger.warning("support_slack_failed error_type=%s", type(exc).__name__)
        slack = "failed"
    await _record(pool, ticket_id, tenant_id, f"slack:{kind}", slack)
    if settings.staff_alert_email:
        rendered = _register_templates().render_email(
            "support_staff_alert", number=number, priority=contact["priority"], category=contact["category"],
            headline=headline, link=link, tenant=contact.get("tenant") or "", subject=contact["subject"][:150])
        try:
            email = _outcome(await service.send_email(settings.staff_alert_email, rendered.subject,
                                                      rendered.body_html, text_body=rendered.body))
        except Exception as exc:
            logger.warning("support_staff_email_failed error_type=%s", type(exc).__name__)
            email = "failed"
        await _record(pool, ticket_id, tenant_id, f"email:{kind}", email)


async def ticket_created(pool, ticket_id: UUID, tenant_id: str, priority: str, settings: SupportSettings) -> None:
    await _email_customer(pool, ticket_id, tenant_id, "support_ticket_created", settings)
    if priority in {"urgent", "high"}:
        await alert_staff(pool, ticket_id, tenant_id, f"New {priority} support ticket", settings,
                          kind="staff_new_ticket")


async def staff_replied(pool, ticket_id: UUID, tenant_id: str, settings: SupportSettings) -> None:
    await _email_customer(pool, ticket_id, tenant_id, "support_staff_reply", settings)


async def ticket_resolved(pool, ticket_id: UUID, tenant_id: str, settings: SupportSettings) -> None:
    await _email_customer(pool, ticket_id, tenant_id, "support_ticket_resolved", settings)
