"""
Email API Endpoints
FastAPI endpoints for email template management and sending.

Wraps the existing EmailTemplateManager and EmailService.
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.domain.services.email_template_manager import get_email_template_manager
from app.services.email_service import get_email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["Email"])


class EmailTemplateResponse(BaseModel):
    id: str
    name: str
    html: str
    thumbnail_url: Optional[str] = None
    locked: Optional[bool] = None
    updated_at: Optional[str] = None


class SendEmailRequest(BaseModel):
    to: List[str] = Field(..., min_length=1, max_length=50)
    subject: Optional[str] = None
    body: Optional[str] = None
    body_html: Optional[str] = None
    template_id: Optional[str] = None
    template_context: Optional[dict] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    reply_to: Optional[str] = None


class SendEmailResponse(BaseModel):
    message_id: Optional[str] = None
    status: str = "accepted"


@router.get("/templates", response_model=List[EmailTemplateResponse])
async def list_email_templates(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    List available email templates.

    Returns templates from the EmailTemplateManager, transformed
    to match the frontend's expected schema.
    """
    manager = get_email_template_manager()
    template_names = manager.list_templates()

    templates = []
    for name in template_names:
        info = manager.get_template_info(name)
        if not info:
            continue

        tmpl = manager.get_template(name)
        templates.append(EmailTemplateResponse(
            id=name,
            name=info.get("description", name),
            html=tmpl.body_html_template if tmpl else "",
            thumbnail_url=None,
            locked=True,
            updated_at=None,
        ))

    return templates


@router.post("/send", response_model=SendEmailResponse)
async def send_email(
    request: SendEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """
    Send an email.

    Delegates to EmailService.send_email() which handles:
    - Template rendering (if template_id provided)
    - Content validation
    - Sending via connected email provider (Gmail, etc.)
    - Audit trail creation
    """
    email_svc = get_email_service(db_client)

    try:
        result = await email_svc.send_email(
            tenant_id=current_user.tenant_id,
            to=request.to,
            subject=request.subject or "",
            body=request.body or "",
            body_html=request.body_html,
            cc=request.cc,
            bcc=request.bcc,
            reply_to=request.reply_to,
            template_name=request.template_id,
            template_context=request.template_context,
        )
        return SendEmailResponse(
            message_id=result.get("message_id"),
            status=result.get("status", "accepted"),
        )
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        error_msg = str(e)

        if "not connected" in error_msg.lower():
            raise HTTPException(
                status_code=400,
                detail="No email provider connected. Please connect an email account first.",
            )

        raise HTTPException(
            status_code=500,
            detail=f"Failed to send email: {error_msg}",
        )
