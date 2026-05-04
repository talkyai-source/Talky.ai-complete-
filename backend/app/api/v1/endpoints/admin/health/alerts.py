"""Alert settings + external notification helpers.

Module-level `_alert_settings` is mutable state shared by:
  - GET /alerts/settings   (reads it)
  - PUT /alerts/settings   (rebinds it)
  - trigger_alert()        (reads it to decide whether to dispatch)

Keeping all three in this module preserves the original single-file
behaviour: `global _alert_settings` rebinds the same module attribute
every reader sees.

`send_email_alert`, `send_slack_alert`, and `trigger_alert` are public
helpers used by background monitoring code; re-exported from the
package __init__ for compatibility.
"""
from __future__ import annotations

from datetime import datetime
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.postgres_adapter import Client

from .schemas import AlertSettings

router = APIRouter()


# In-memory storage for alert settings (would be in DB in production)
_alert_settings = AlertSettings()


# =============================================================================
# Endpoints — alert settings
# =============================================================================

@router.get("/alerts/settings", response_model=AlertSettings)
async def get_alert_settings(
    admin_user: CurrentUser = Depends(require_admin)
):
    """Get current alert threshold settings."""
    return _alert_settings


@router.put("/alerts/settings", response_model=AlertSettings)
async def update_alert_settings(
    settings: AlertSettings,
    admin_user: CurrentUser = Depends(require_admin)
):
    """
    Update alert threshold settings.

    Note: Email and Slack notifications are prepared but not yet implemented.
    Set the thresholds and notification preferences for future activation.
    """
    global _alert_settings
    _alert_settings = settings

    return _alert_settings


# =============================================================================
# External notification helpers (called by monitoring processes)
# =============================================================================

async def send_email_alert(
    subject: str,
    body: str,
    recipients: List[str]
) -> bool:
    """
    Send email alert notification via configured email provider.

    Day 8: Implemented with SendGrid, AWS SES, or SMTP support.

    Args:
        subject: Email subject
        body: Email body (HTML)
        recipients: List of email addresses

    Returns:
        True if all emails sent successfully, False otherwise
    """
    if not recipients:
        return False

    try:
        from app.domain.services.notification_service import get_notification_service

        notification_service = get_notification_service()
        all_success = True

        for recipient in recipients:
            result = await notification_service.send_email(
                to_email=recipient,
                subject=subject,
                html_body=body,
                text_body=subject,  # Fallback to subject as plain text
            )
            if result.get("status") != "success":
                all_success = False

        return all_success
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"Failed to send email alert: {e}")
        return False


async def send_slack_alert(
    message: str,
    webhook_url: str,
    severity: str = "warning"
) -> bool:
    """
    Send Slack alert notification via webhook.

    Day 8: Implemented with Slack Incoming Webhooks.

    Args:
        message: Alert message
        webhook_url: Slack webhook URL
        severity: Alert severity (info, warning, critical)

    Returns:
        True if sent successfully, False otherwise
    """
    if not webhook_url:
        return False

    try:
        import aiohttp

        # Color coding based on severity
        color_map = {
            "info": "#439FE0",
            "warning": "#FF9500",
            "critical": "#FF3B30",
        }
        color = color_map.get(severity, "#439FE0")

        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"🚨 {severity.upper()} Alert",
                    "text": message,
                    "footer": "Talky.ai Admin Dashboard",
                    "ts": int(datetime.utcnow().timestamp()),
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                result = await response.text()
                return response.status == 200 and result == "ok"
    except ImportError:
        logger = __import__("logging").getLogger(__name__)
        logger.error("aiohttp package not installed for Slack alerts")
        return False
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"Failed to send Slack alert: {e}")
        return False


async def trigger_alert(
    title: str,
    severity: str,
    description: str,
    db_client: Client
) -> str:
    """
    Create incident and optionally send external notifications.

    This function is called by monitoring processes when thresholds are exceeded.
    """
    incident_id = str(uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    try:
        # Create incident record
        db_client.table("incidents").insert({
            "id": incident_id,
            "title": title,
            "severity": severity,
            "status": "open",
            "description": description,
            "triggered_at": now
        }).execute()

        # Send external notifications if enabled (future)
        global _alert_settings
        if _alert_settings.email_notifications:
            await send_email_alert(
                subject=f"[{severity.upper()}] {title}",
                body=description,
                recipients=[]  # Would come from settings
            )

        if _alert_settings.slack_notifications and _alert_settings.slack_webhook_url:
            await send_slack_alert(
                message=f"*{title}*\n{description}",
                webhook_url=_alert_settings.slack_webhook_url,
                severity=severity
            )

        return incident_id

    except Exception:
        return ""
