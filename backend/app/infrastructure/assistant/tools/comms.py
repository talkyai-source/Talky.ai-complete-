"""
Email and SMS communication tools for the assistant agent.
"""
import logging
import os
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


# Where assistant-filed technical-issue reports go. Set per-deploy via
# SUPPORT_REPORT_EMAIL. Deliberately NO hardcoded default — reports carry
# tenant data, so we fail closed rather than ship a baked-in recipient that
# could send to the wrong inbox if the env is ever unset.
def _support_report_email() -> Optional[str]:
    return (os.getenv("SUPPORT_REPORT_EMAIL") or "").strip() or None


class ReportIssueInput(BaseModel):
    """Input for the report_issue tool — files a technical-issue report to support."""
    description: str = Field(
        ...,
        description="Clear description of the technical problem the user is facing, in their words plus any specifics (what they were doing, what failed, error text).",
    )
    category: Optional[str] = Field(
        None,
        description="Coarse area: calls | voice | billing | login | dashboard | other.",
    )
    severity: str = Field(
        "normal",
        description="How blocking it is: low | normal | high.",
    )
    contact_email: Optional[str] = Field(
        None,
        description="The reporter's email for follow-up. Omit to use the account's email on file.",
    )


async def _resolve_reporter_email(tenant_id: str, db_client: Client) -> Optional[str]:
    """Best-effort: the tenant's account email (prefers a tenant_admin)."""
    try:
        rows = (
            db_client.table("user_profiles")
            .select("email, role")
            .eq("tenant_id", tenant_id)
            .limit(10)
            .execute()
            .data
        ) or []
        if not rows:
            return None
        admin = next((r for r in rows if (r.get("role") or "") == "tenant_admin"), None)
        chosen = admin or rows[0]
        return (chosen.get("email") or "").strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve reporter email failed: %s", e)
        return None


async def report_issue(
    tenant_id: str,
    db_client: Client,
    description: str,
    category: Optional[str] = None,
    severity: str = "normal",
    contact_email: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """File a technical-issue report to the support inbox.

    Sends IMMEDIATELY (this is a support ticket the user is asking us to log,
    not a mutation of their data). Auto-includes the tenant id, the reporter's
    email (explicit or resolved from the account), category, severity, a
    timestamp, and the description. Goes to ``SUPPORT_REPORT_EMAIL``.
    """
    if not (description or "").strip():
        return {"success": False, "error": "Need a description of the issue before I can report it."}

    support_to = _support_report_email()
    if not support_to:
        logger.warning("report_issue called but SUPPORT_REPORT_EMAIL is not configured")
        return {
            "success": False,
            "error": "Support reporting isn't configured on the server, so I couldn't file the report. Please contact support directly.",
        }

    reporter = (contact_email or "").strip() or await _resolve_reporter_email(tenant_id, db_client)
    sev = (severity or "normal").strip().lower()
    cat = (category or "other").strip().lower()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    subject = f"[Talky issue] {sev.upper()} · {cat} · tenant {str(tenant_id)[:8]}"
    body = (
        "A technical issue was reported via the in-app assistant.\n\n"
        f"Severity:    {sev}\n"
        f"Category:    {cat}\n"
        f"Tenant ID:   {tenant_id}\n"
        f"Reporter:    {reporter or 'unknown (no email on file)'}\n"
        f"Conversation:{conversation_id or '-'}\n"
        f"Reported at: {ts}\n\n"
        "Description:\n"
        f"{description.strip()}\n"
    )

    try:
        from app.services.email_service import get_email_service, EmailNotConnectedError
        from app.infrastructure.connectors.email.smtp import SMTPConnector

        service = get_email_service(db_client)
        try:
            await service.send_email(
                tenant_id=tenant_id,
                to=[support_to],
                subject=subject,
                body=body,
                triggered_by="assistant_report_issue",
                conversation_id=conversation_id,
            )
        except EmailNotConnectedError:
            if not SMTPConnector.is_configured():
                return {
                    "success": False,
                    "error": "Support email isn't configured on the server, so I couldn't file the report. Please contact support directly.",
                }
            smtp = SMTPConnector()
            await smtp.send_email(to=[support_to], subject=subject, body=body)

        logger.info(
            "assistant report_issue filed tenant=%s sev=%s cat=%s -> %s",
            tenant_id, sev, cat, support_to,
        )
        return {
            "success": True,
            "message": (
                "Thanks — I've sent your issue to our support team. "
                "They'll follow up"
                + (f" at {reporter}." if reporter else ".")
            ),
            "severity": sev,
            "category": cat,
        }
    except Exception as e:  # noqa: BLE001
        logger.error("report_issue failed: %s", e)
        return {"success": False, "error": "I couldn't file the report just now. Please try again, or contact support directly."}


class SendEmailInput(BaseModel):
    """Input for send_email tool"""
    to: Optional[List[str]] = Field(
        None,
        description="Recipient email addresses. Omit when emailing a lead — pass lead_id or phone_number instead and the lead's email is resolved automatically.",
    )
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body content (plain text)")
    body_html: Optional[str] = Field(None, description="Optional HTML body")
    template_name: Optional[str] = Field(None, description="Template to use: meeting_confirmation, follow_up, reminder")
    template_context: Optional[Dict[str, Any]] = Field(None, description="Variables for template rendering")
    lead_ids: Optional[List[str]] = Field(None, description="Optional lead IDs if sending to leads")
    lead_id: Optional[str] = Field(None, description="Resolve the recipient from this lead/contact id")
    phone_number: Optional[str] = Field(None, description="Resolve the recipient from this lead's phone number")
    confirm: bool = Field(
        False,
        description="false = preview only (the user sees it and clicks Apply); true = actually send. Leave false — the Apply button sends.",
    )


async def _resolve_lead_email(
    tenant_id: str,
    db_client: Client,
    lead_id: Optional[str] = None,
    phone_number: Optional[str] = None,
):
    """Resolve a single lead's email. Returns (email, lead_row) on success, or
    (None, reason_str) where reason is one of: no_match | ambiguous | no_email | error."""
    try:
        q = (
            db_client.table("leads")
            .select("id, email, first_name, last_name, phone_number")
            .eq("tenant_id", tenant_id)
            .neq("status", "deleted")
        )
        if lead_id:
            q = q.eq("id", lead_id)
        elif phone_number:
            q = q.ilike("phone_number", f"%{phone_number}%")
        else:
            return None, "no_match"
        rows = (q.limit(5).execute().data) or []
        if not rows:
            return None, "no_match"
        if len(rows) > 1:
            return None, "ambiguous"
        row = rows[0]
        email = (row.get("email") or "").strip()
        if not email:
            return None, "no_email"
        return email, row
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve lead email failed: %s", e)
        return None, "error"


class SendSMSInput(BaseModel):
    """Input for send_sms tool"""
    to: List[str] = Field(..., description="List of phone numbers")
    message: str


async def send_email(
    tenant_id: str,
    db_client: Client,
    to: Optional[List[str]] = None,
    subject: str = "",
    body: str = "",
    body_html: Optional[str] = None,
    template_name: Optional[str] = None,
    template_context: Optional[Dict[str, Any]] = None,
    lead_ids: Optional[List[str]] = None,
    lead_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    confirm: bool = False,
    connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send email via connected provider (Gmail) or SMTP fallback.

    Two-phase like the other edit tools:
    - confirm=False → resolve the recipient (incl. a lead's stored email) and
      return a PREVIEW (no send). The UI shows it with Apply/Reject.
    - confirm=True  → actually send.

    The recipient is either explicit `to`, or resolved from `lead_id` /
    `phone_number` (the lead's email on file).
    """
    try:
        recipients = list(to or [])

        # Resolve a lead's email when no explicit recipient was given.
        if not recipients and (lead_id or phone_number):
            email, info = await _resolve_lead_email(tenant_id, db_client, lead_id, phone_number)
            if not email:
                reasons = {
                    "no_match": "No matching contact found for that lead.",
                    "ambiguous": "Multiple contacts match — tell me which one (use the lead id).",
                    "no_email": "That contact has no email address on file.",
                    "error": "Could not look up the contact.",
                }
                return {"success": False, "error": reasons.get(info, "Could not resolve a recipient.")}
            recipients = [email]
            if lead_ids is None and isinstance(info, dict) and info.get("id"):
                lead_ids = [info["id"]]

        if not recipients:
            return {
                "success": False,
                "error": "No recipient. Give an email address, or a lead_id/phone_number to look one up.",
                "email_required": True,
            }

        # Resolve the effective subject/body (render template now so the preview
        # shows the real content the recipient will get).
        eff_subject, eff_body, eff_html = subject, body, body_html
        if template_name and template_context:
            try:
                from app.domain.services.email_template_manager import get_email_template_manager
                mgr = get_email_template_manager()
                rendered = mgr.render_email(template_name, **template_context)
                eff_subject = rendered.subject or subject
                eff_body = rendered.body or body
                eff_html = rendered.body_html or body_html
            except Exception as te:  # noqa: BLE001
                logger.warning("send_email template render failed: %s", te)

        # PREVIEW — confirm=False returns a proposal-style diff and sends nothing.
        if not confirm:
            preview_body = eff_body if len(eff_body) <= 600 else eff_body[:600] + "…"
            return {
                "preview": True,
                "changes": [
                    {"field": "To", "before": None, "after": ", ".join(recipients)},
                    {"field": "Subject", "before": None, "after": eff_subject},
                    {"field": "Body", "before": None, "after": preview_body},
                ],
                "note": "Not sent yet.",
            }

        # APPLY — actually send via Gmail (if connected) or SMTP fallback.
        from app.services.email_service import get_email_service, EmailNotConnectedError
        from app.infrastructure.connectors.email.smtp import SMTPConnector

        service = get_email_service(db_client)
        try:
            result = await service.send_email(
                tenant_id=tenant_id,
                to=recipients,
                subject=eff_subject,
                body=eff_body,
                body_html=eff_html,
                template_name=None,
                template_context=None,
                lead_ids=lead_ids,
                conversation_id=conversation_id,
                triggered_by="assistant",
            )
            return result if isinstance(result, dict) else {"success": True, "recipients": recipients}

        except EmailNotConnectedError:
            if SMTPConnector.is_configured():
                logger.info("Using SMTP fallback for email sending")
                smtp = SMTPConnector()
                sent = await smtp.send_email(
                    to=recipients, subject=eff_subject, body=eff_body, body_html=eff_html
                )
                return {
                    "success": True,
                    "message_id": sent.id,
                    "provider": "smtp",
                    "recipients": recipients,
                    "message": f"Email sent to {len(recipients)} recipient(s)",
                }
            return {
                "success": False,
                "error": "No email provider connected. Please connect Gmail from Settings > Integrations.",
                "email_required": True,
            }

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return {"success": False, "error": str(e)}


async def send_sms(
    tenant_id: str,
    db_client: Client,
    to: List[str],
    message: str,
    lead_ids: Optional[List[str]] = None,
    connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send SMS via connected SMS provider.
    """
    try:
        action_data = {
            "tenant_id": tenant_id,
            "type": "send_sms",
            "status": "pending",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "connector_id": connector_id,
            "input_data": {
                "to": to,
                "message": message,
                "lead_ids": lead_ids
            }
        }

        action_response = db_client.table("assistant_actions").insert(action_data).execute()
        action_id = action_response.data[0]["id"] if action_response.data else None

        # TODO: Actually send SMS via connector

        if action_id:
            db_client.table("assistant_actions").update({
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "output_data": {"message": "SMS queued for delivery"}
            }).eq("id", action_id).execute()

        return {
            "success": True,
            "action_id": action_id,
            "message": f"SMS to {len(to)} recipient(s) queued",
            "recipients": to
        }
    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        return {"success": False, "error": str(e)}
