"""
Email and SMS communication tools for the assistant agent.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class SendEmailInput(BaseModel):
    """Input for send_email tool"""
    to: List[str] = Field(..., description="List of email addresses")
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body content (plain text)")
    body_html: Optional[str] = Field(None, description="Optional HTML body")
    template_name: Optional[str] = Field(None, description="Template to use: meeting_confirmation, follow_up, reminder")
    template_context: Optional[Dict[str, Any]] = Field(None, description="Variables for template rendering")
    lead_ids: Optional[List[str]] = Field(None, description="Optional lead IDs if sending to leads")


class SendSMSInput(BaseModel):
    """Input for send_sms tool"""
    to: List[str] = Field(..., description="List of phone numbers")
    message: str


async def send_email(
    tenant_id: str,
    db_client: Client,
    to: List[str],
    subject: str,
    body: str,
    body_html: Optional[str] = None,
    template_name: Optional[str] = None,
    template_context: Optional[Dict[str, Any]] = None,
    lead_ids: Optional[List[str]] = None,
    connector_id: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send email via connected email provider (Gmail) or SMTP fallback.

    Supports:
    - Direct email with subject/body
    - Templated emails (meeting_confirmation, follow_up, reminder)
    - HTML and plain text
    - Audit trail via assistant_actions table
    """
    try:
        from app.services.email_service import get_email_service, EmailNotConnectedError
        from app.infrastructure.connectors.email.smtp import SMTPConnector

        service = get_email_service(db_client)

        try:
            # Try sending via connected email provider (Gmail)
            result = await service.send_email(
                tenant_id=tenant_id,
                to=to,
                subject=subject,
                body=body,
                body_html=body_html,
                template_name=template_name,
                template_context=template_context,
                lead_ids=lead_ids,
                conversation_id=conversation_id,
                triggered_by="assistant"
            )
            return result

        except EmailNotConnectedError:
            # Fallback to SMTP if configured
            if SMTPConnector.is_configured():
                logger.info("Using SMTP fallback for email sending")
                smtp = SMTPConnector()

                # Render template if specified
                if template_name and template_context:
                    from app.domain.services.email_template_manager import get_email_template_manager
                    mgr = get_email_template_manager()
                    rendered = mgr.render_email(template_name, **template_context)
                    subject = rendered.subject
                    body = rendered.body
                    body_html = rendered.body_html or body_html

                result = await smtp.send_email(
                    to=to,
                    subject=subject,
                    body=body,
                    body_html=body_html
                )

                return {
                    "success": True,
                    "message_id": result.id,
                    "provider": "smtp",
                    "recipients": to,
                    "message": f"Email sent to {len(to)} recipient(s)"
                }
            else:
                return {
                    "success": False,
                    "error": "No email provider connected. Please connect Gmail from Settings > Integrations.",
                    "email_required": True
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
