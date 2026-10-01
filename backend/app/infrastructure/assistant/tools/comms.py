"""
Email and SMS communication tools for the assistant agent.
"""
import re
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
    confirm: bool = Field(False, description="False previews the report; the user applies it to send.")
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
    tenant_id: str, db_client: Client, description: str,
    category: Optional[str] = None, severity: str = "normal",
    contact_email: Optional[str] = None, conversation_id: Optional[str] = None,
    confirm: bool = False, _prepared_report: Optional[dict] = None,
) -> Dict[str, Any]:
    """Preview a support report, then submit the exact approved content."""
    if not (description or "").strip():
        return {"success": False, "status": "failed", "error": "Need a description of the issue."}
    support_to = _support_report_email()
    if not support_to:
        return {"success": False, "status": "failed", "error": "Support reporting is not configured."}
    reporter = (contact_email or "").strip()
    if not reporter and not (confirm and _prepared_report is not None):
        reporter = await _resolve_reporter_email(tenant_id, db_client)
    sev, cat = (severity or "normal").strip().lower(), (category or "other").strip().lower()
    subject = f"[Talky issue] {sev.upper()} / {cat} / tenant {str(tenant_id)[:8]}"
    body = ("A technical issue was reported via the in-app assistant.\n\n"
        f"Severity: {sev}\nCategory: {cat}\nTenant ID: {tenant_id}\nReporter: {reporter or 'unknown'}\n"
        f"Conversation: {conversation_id or '-'}\nReported at: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Description:\n{description.strip()}\n")
    if confirm and _prepared_report is not None:
        if _prepared_report.get("to") != support_to:
            return {"success": False, "status": "failed", "error": "Support destination changed; preview again."}
        subject, body = _prepared_report["subject"], _prepared_report["body"]
    if not confirm:
        return {"preview": True, "changes": [
            {"field": "To", "before": None, "after": support_to},
            {"field": "Subject", "before": None, "after": subject},
            {"field": "Body", "before": None, "after": body}], "note": "Not sent yet.",
            "_apply_args": {"description": description, "category": cat, "severity": sev,
                "contact_email": reporter, "_prepared_report": {"to": support_to, "subject": subject, "body": body}}}
    try:
        from app.services.email_service import get_email_service, EmailNotConnectedError
        from app.infrastructure.connectors.email.smtp import SMTPConnector
        try:
            result = await get_email_service(db_client).send_email(tenant_id=tenant_id, to=[support_to], subject=subject,
                body=body, triggered_by="assistant_report_issue", conversation_id=conversation_id)
        except EmailNotConnectedError:
            if not SMTPConnector.is_configured():
                return {"success": False, "status": "failed", "error": "Support email is not configured."}
            sent = await SMTPConnector().send_email(to=[support_to], subject=subject, body=body)
            if not getattr(sent, "id", None):
                return {"success": False, "status": "unknown", "error": "Support send returned no receipt."}
            result = {"success": True, "status": "accepted", "message_id": sent.id, "provider": "smtp"}
        if not isinstance(result, dict):
            return {"success": False, "status": "unknown", "confirmation_allowed": False}
        if result.get("success") is not True:
            return result
        if not result.get("message_id"):
            return {**result, "success": False, "status": "unknown", "confirmation_allowed": False,
                    "error": "Support send returned no receipt."}
        return {**result, "confirmation_allowed": True,
            "message": "The email provider accepted your support report.", "severity": sev, "category": cat}
    except Exception as exc:
        logger.error("report_issue failed: %s", type(exc).__name__)
        return {"success": False, "status": "unknown", "confirmation_allowed": False,
            "error": "The report outcome is unconfirmed. Do not resend automatically."}


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
    confirm: bool = Field(False, description="False previews only; the user applies it to send.")


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
    Send email through the tenant connected provider.

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
            preview_body = eff_body
            return {
                "preview": True,
                "changes": [
                    {"field": "To", "before": None, "after": ", ".join(recipients)},
                    {"field": "Subject", "before": None, "after": eff_subject},
                    {"field": "Body", "before": None, "after": preview_body},
                ],
                "note": "Not sent yet.",
                "_apply_args": {"to": recipients, "subject": eff_subject, "body": eff_body,
                    "body_html": eff_html, "lead_ids": lead_ids, "connector_id": connector_id},
            }

        from app.services.email_service import get_email_service, EmailNotConnectedError
        try:
            result = await get_email_service(db_client).send_email(
                tenant_id=tenant_id, to=recipients, subject=eff_subject, body=eff_body,
                body_html=eff_html, template_name=None, template_context=None,
                lead_ids=lead_ids, conversation_id=conversation_id, triggered_by="assistant",
                connector_id=connector_id)
            return result if isinstance(result, dict) else {"success": False, "status": "unknown"}
        except EmailNotConnectedError:
            return {"success": False, "status": "failed", "email_required": True,
                "error": "No tenant email provider connected. Connect Gmail from Settings > Integrations."}

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return {"success": False, "status": "unknown" if confirm else "failed",
                "confirmation_allowed": False, "error": str(e)}


async def send_sms(
    tenant_id: str, db_client: Client, to: List[str], message: str,
    lead_ids: Optional[List[str]] = None, connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None, confirm: bool = False,
) -> Dict[str, Any]:
    """Preview then submit SMS through SMSService and return real receipts."""
    recipients = list(dict.fromkeys(number.strip() for number in to))
    if not recipients or not message.strip() or any(not re.fullmatch(r"\+[1-9]\d{7,14}", n) for n in recipients):
        return {"success": False, "status": "failed", "error": "Provide the message and complete international phone numbers."}
    if not confirm:
        return {"preview": True, "changes": [
            {"field": "To", "before": None, "after": ", ".join(recipients)},
            {"field": "Message", "before": None, "after": message}], "note": "Not sent yet.",
            "_apply_args": {"to": recipients, "message": message, "lead_ids": lead_ids}}
    from app.services.sms_service import get_sms_service
    receipts = []
    try:
        service = get_sms_service(db_client)
        for number in recipients:
            result = await service.send_sms(tenant_id=tenant_id, to_number=number, message=message,
                triggered_by="assistant")
            receipts.append({**result, "to_number": number})
        success = all(r.get("success") is True and r.get("message_id") for r in receipts)
        state = "accepted" if success else "unknown" if any(r.get("status") == "unknown" for r in receipts) else "failed"
        return {"success": bool(success), "status": state, "confirmation_allowed": bool(success),
            "receipts": receipts, "recipients": recipients,
            "message": "SMS accepted by the provider." if success else "Some SMS requests were not confirmed; review individual receipts."}
    except Exception as exc:
        logger.error("send_sms failed: %s", type(exc).__name__)
        return {"success": False, "status": "unknown" if receipts else "failed", "confirmation_allowed": False,
            "receipts": receipts, "error": "SMS request could not be completed."}
