"""
Notification Service - Email & Slack
Handles system notifications and alerts for suspensions, billing events, etc.

Day 8: Comprehensive notification system with email and Slack support
"""
import logging
import os
from typing import Optional, Dict, Any
from enum import Enum
from datetime import datetime
from html import escape

from app.core import config
from app.domain.services import email_service as account_mail

logger = logging.getLogger(__name__)


class NotificationChannel(str, Enum):
    """Supported notification channels"""
    EMAIL = "email"
    SLACK = "slack"
    BOTH = "both"


class NotificationType(str, Enum):
    """Types of notifications"""
    USER_SUSPENDED = "user_suspended"
    TENANT_SUSPENDED = "tenant_suspended"
    PARTNER_SUSPENDED = "partner_suspended"
    BILLING_PAYMENT_FAILED = "billing_payment_failed"
    BILLING_PAYMENT_SUCCESS = "billing_payment_success"
    ABUSE_DETECTED = "abuse_detected"
    CALL_LIMIT_EXCEEDED = "call_limit_exceeded"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_COMPROMISED = "account_compromised"
    SESSION_HIJACKING = "session_hijacking"
    SECURITY_ALERT = "security_alert"
    CAMPAIGN_FAILURES = "campaign_failures"
    LOW_MINUTES = "low_minutes"
    QUALIFIED_LEAD = "qualified_lead"


class NotificationService:
    """
    Service for sending notifications via email and/or Slack.

    Features:
    - Email notifications via SMTP/SendGrid
    - Slack notifications via webhooks
    - Template-based messages
    - Retry logic for failed sends
    - Audit logging of all notifications
    """

    def __init__(self):
        """Initialize notification service with environment variables."""
        # Account notifications share the Microsoft 365 account sender. These
        # attributes also let the billing outbox preflight that actual transport.
        settings = config.get_settings()
        self.email_enabled = os.getenv("EMAIL_ENABLED", "true").lower() == "true"
        self.email_provider = "smtp"
        self.sendgrid_api_key = os.getenv("SENDGRID_API_KEY", "")
        self.smtp_host = account_mail.account_smtp_host()
        self.smtp_port = account_mail.EmailService.SMTP_PORT
        self.smtp_user = settings.email_user
        self.smtp_password = settings.email_pass
        self.from_email = settings.from_email or settings.email_user
        self.from_name = settings.brand_name
        self.frontend_url = settings.frontend_url

        # Slack configuration
        self.slack_enabled = os.getenv("SLACK_ENABLED", "true").lower() == "true"
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.slack_channel = os.getenv("SLACK_CHANNEL", "#alerts")

        # AWS SES configuration
        self.aws_region = os.getenv("AWS_REGION", "us-east-1")
        self.aws_access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        self.aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")

        logger.info(
            f"NotificationService initialized (email={self.email_enabled}, slack={self.slack_enabled})"
        )

    # =========================================================================
    # Email Notifications
    # =========================================================================

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send through the shared account sender without retrying unknown outcomes.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML email body
            text_body: Plain text fallback
            reply_to: Reply-to address

        Returns:
            Dict with confirmed status; the boolean sender has no message ID.
        """
        if not self.email_enabled:
            logger.debug("Email disabled, skipping send")
            return {"status": "skipped", "reason": "email_disabled"}

        try:
            accepted = await account_mail.get_email_service().send_email(
                recipient_email=to_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                reply_to=reply_to,
            )
            if accepted is True:
                return {"status": "success"}
            return {"status": "error", "reason": "send_outcome_unconfirmed"}
        except Exception as e:
            logger.error("Account notification send failed (%s)", type(e).__name__)
            return {"status": "error", "reason": "send_outcome_unconfirmed"}

    async def _send_via_sendgrid(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send email via SendGrid API."""
        if not self.sendgrid_api_key:
            logger.warning("SendGrid API key not configured")
            return {"status": "error", "reason": "sendgrid_not_configured"}

        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail

            message = Mail(
                from_email=(self.from_email, self.from_name),
                to_emails=to_email,
                subject=subject,
                plain_text_content=text_body,
                html_content=html_body,
            )

            if reply_to:
                message.reply_to = reply_to

            sg = SendGridAPIClient(self.sendgrid_api_key)
            if hasattr(self, "delivery_timeout_seconds"):
                sg.client.timeout = self.delivery_timeout_seconds
            response = sg.send(message)

            logger.info(
                f"Email sent via SendGrid to {to_email}, status={response.status_code}"
            )
            return {
                "status": "success",
                "message_id": (response.headers or {}).get("X-Message-Id"),
            }
        except ImportError:
            logger.error("sendgrid package not installed")
            return {"status": "error", "reason": "sendgrid_not_installed"}
        except Exception as e:
            logger.error(f"SendGrid send failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def _send_via_smtp(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send email via SMTP."""
        if not self.smtp_host or not self.smtp_user or not self.smtp_password:
            logger.warning("SMTP not fully configured")
            return {"status": "error", "reason": "smtp_not_configured"}

        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            if reply_to:
                msg["Reply-To"] = reply_to

            if text_body:
                msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            # Connect and send
            timeout_options = ({"timeout": self.delivery_timeout_seconds}
                               if hasattr(self, "delivery_timeout_seconds") else {})
            with smtplib.SMTP(self.smtp_host, self.smtp_port, **timeout_options) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info(f"Email sent via SMTP to {to_email}")
            return {"status": "success", "message_id": f"smtp_{int(datetime.now().timestamp())}"}
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return {"status": "error", "reason": str(e)}

    async def _send_via_ses(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send email via AWS SES."""
        if not self.aws_access_key or not self.aws_secret_key:
            logger.warning("AWS SES credentials not configured")
            return {"status": "error", "reason": "ses_not_configured"}

        try:
            import boto3
            from botocore.config import Config

            transport_options = {}
            if hasattr(self, "delivery_timeout_seconds"):
                transport_options["config"] = Config(
                    connect_timeout=self.delivery_timeout_seconds,
                    read_timeout=self.delivery_timeout_seconds,
                    retries={"max_attempts": 0},
                )
            client = boto3.client(
                "ses",
                region_name=self.aws_region,
                aws_access_key_id=self.aws_access_key,
                aws_secret_access_key=self.aws_secret_key,
                **transport_options,
            )

            kwargs = {
                "Source": self.from_email,
                "Destination": {"ToAddresses": [to_email]},
                "Message": {
                    "Subject": {"Data": subject},
                    "Body": {"Html": {"Data": html_body}},
                },
            }

            if text_body:
                kwargs["Message"]["Body"]["Text"] = {"Data": text_body}

            if reply_to:
                kwargs["ReplyToAddresses"] = [reply_to]

            response = client.send_email(**kwargs)
            logger.info(f"Email sent via SES to {to_email}, id={response['MessageId']}")
            return {"status": "success", "message_id": response["MessageId"]}
        except ImportError:
            logger.error("boto3 package not installed")
            return {"status": "error", "reason": "boto3_not_installed"}
        except Exception as e:
            logger.error(f"SES send failed: {e}")
            return {"status": "error", "reason": str(e)}

    # =========================================================================
    # Slack Notifications
    # =========================================================================

    async def send_slack_notification(
        self,
        title: str,
        message: str,
        severity: str = "info",  # info, warning, critical
        fields: Optional[Dict[str, str]] = None,
        channel: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send notification to Slack via webhook.

        Args:
            title: Notification title
            message: Notification message
            severity: Severity level (info, warning, critical)
            fields: Additional fields to include
            channel: Override default channel

        Returns:
            Dict with status and response
        """
        if not self.slack_enabled or not self.slack_webhook_url:
            logger.debug("Slack disabled or webhook not configured")
            return {"status": "skipped", "reason": "slack_not_configured"}

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
                "channel": channel or self.slack_channel,
                "attachments": [
                    {
                        "color": color,
                        "title": title,
                        "text": message,
                        "ts": int(datetime.now().timestamp()),
                    }
                ],
            }

            if fields:
                payload["attachments"][0]["fields"] = [
                    {"title": k, "value": v, "short": True} for k, v in fields.items()
                ]

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.slack_webhook_url, json=payload
                ) as response:
                    result_text = await response.text()
                    if response.status == 200 and result_text == "ok":
                        logger.info(f"Slack notification sent to {channel or self.slack_channel}")
                        return {"status": "success", "response": result_text}
                    else:
                        logger.warning(
                            f"Slack notification failed: {response.status} {result_text}"
                        )
                        return {
                            "status": "error",
                            "reason": f"http_{response.status}",
                        }
        except ImportError:
            logger.error("aiohttp package not installed")
            return {"status": "error", "reason": "aiohttp_not_installed"}
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")
            return {"status": "error", "reason": str(e)}

    # =========================================================================
    # Pre-built notification templates
    # =========================================================================

    async def notify_suspension(
        self,
        user_id: str,
        user_email: str,
        suspension_type: str,
        reason: str,
        channels: NotificationChannel = NotificationChannel.BOTH,
    ) -> Dict[str, Any]:
        """
        Send suspension notification to user.

        Args:
            user_id: User UUID
            user_email: User email address
            suspension_type: Type of suspension (user, tenant, partner)
            reason: Suspension reason
            channels: Notification channels to use
        """
        subject = f"Your {suspension_type.capitalize()} Account Has Been Suspended"
        support_link = account_mail.frontend_link("/support", base_url=self.frontend_url)
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h1 style="color: #FF3B30;">Account Suspended</h1>
                <p>Your {escape(suspension_type)} account has been suspended.</p>
                <p><strong>Reason:</strong> {escape(reason)}</p>
                <p>If you believe this is in error, please contact our support team.</p>
                <p>
                    <a href="{escape(support_link, quote=True)}" style="color: #007AFF;">Contact Support</a>
                </p>
                <p>{escape(self.from_name)}</p>
            </body>
        </html>
        """
        text_body = f"Your {suspension_type} account has been suspended. Reason: {reason}\nSupport: {support_link}\n{self.from_name}"

        results = {}

        if channels in (NotificationChannel.EMAIL, NotificationChannel.BOTH):
            results["email"] = await self.send_email(
                to_email=user_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )

        if channels in (NotificationChannel.SLACK, NotificationChannel.BOTH):
            results["slack"] = await self.send_slack_notification(
                title=f"{suspension_type.upper()} Suspended",
                message=f"User {user_id} suspended: {reason}",
                severity="critical",
                fields={"User": user_id, "Type": suspension_type, "Reason": reason},
            )

        return results

    async def notify_billing_failure(
        self,
        user_email: str,
        amount: float,
        error_message: str,
        channels: NotificationChannel = NotificationChannel.BOTH,
    ) -> Dict[str, Any]:
        """Send payment failure notification."""
        subject = "Payment Failed - Action Required"
        billing_link = account_mail.frontend_link("/billing", base_url=self.frontend_url)
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h1 style="color: #FF3B30;">Payment Failed</h1>
                <p>Your payment of ${amount:.2f} failed.</p>
                <p><strong>Error:</strong> {escape(error_message)}</p>
                <p>Please update your payment method to avoid service interruption.</p>
                <p>
                    <a href="{escape(billing_link, quote=True)}" style="color: #007AFF;">Update Payment Method</a>
                </p>
                <p>{escape(self.from_name)}</p>
            </body>
        </html>
        """
        text_body = f"Your payment of ${amount:.2f} failed: {error_message}\nBilling: {billing_link}\n{self.from_name}"

        results = {}

        if channels in (NotificationChannel.EMAIL, NotificationChannel.BOTH):
            results["email"] = await self.send_email(
                to_email=user_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )

        if channels in (NotificationChannel.SLACK, NotificationChannel.BOTH):
            results["slack"] = await self.send_slack_notification(
                title="Payment Failed",
                message=f"Payment failure: {error_message}",
                severity="warning",
                fields={"Amount": f"${amount:.2f}", "Error": error_message},
            )

        return results

    async def notify_security_alert(
        self,
        user_email: str,
        alert_type: str,
        details: str,
        channels: NotificationChannel = NotificationChannel.BOTH,
    ) -> Dict[str, Any]:
        """Send security alert notification."""
        subject = "Security Alert - Action Required"
        security_link = account_mail.frontend_link("/security", base_url=self.frontend_url)
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333;">
                <h1 style="color: #FF9500;">Security Alert</h1>
                <p><strong>Alert Type:</strong> {escape(alert_type)}</p>
                <p><strong>Details:</strong> {escape(details)}</p>
                <p>Please review your account security immediately.</p>
                <p>
                    <a href="{escape(security_link, quote=True)}" style="color: #007AFF;">Review Security</a>
                </p>
                <p>{escape(self.from_name)}</p>
            </body>
        </html>
        """
        text_body = f"Security Alert: {alert_type} - {details}\nSecurity: {security_link}\n{self.from_name}"

        results = {}

        if channels in (NotificationChannel.EMAIL, NotificationChannel.BOTH):
            results["email"] = await self.send_email(
                to_email=user_email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )

        if channels in (NotificationChannel.SLACK, NotificationChannel.BOTH):
            results["slack"] = await self.send_slack_notification(
                title="Security Alert",
                message=f"{alert_type}: {details}",
                severity="critical",
                fields={"Type": alert_type, "Details": details},
            )

        return results


# Singleton instance
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get or create notification service singleton."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
