"""
Email Service - Microsoft 365 SMTP Integration (GoDaddy)
Handles sending transactional emails using Microsoft 365 SMTP services.

Configuration:
  - Host: smtp.office365.com
  - Port: 587 (STARTTLS)
  - Credentials: From environment (EMAIL_USER, EMAIL_PASS)
  - Sender: noreply@talkleeai.com

Microsoft 365 Authentication:
  - Supports App Password (recommended if MFA enabled)
  - Supports direct M365 password if SMTP AUTH enabled
  - Requires SMTP AUTH enabled in M365 Admin Center
"""

from __future__ import annotations

import logging
from typing import Optional

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """Async email service using Microsoft 365 SMTP (GoDaddy)."""

    # Microsoft 365 SMTP Configuration
    SMTP_HOST = "smtp.office365.com"
    SMTP_PORT = 587
    SMTP_USE_TLS = True

    def __init__(
        self,
        sender_email: Optional[str] = None,
        sender_password: Optional[str] = None,
    ):
        """
        Initialize email service with Microsoft 365 credentials.

        Args:
            sender_email: Email address for sending (defaults to settings.email_user)
            sender_password: Microsoft 365 password or App Password (defaults to settings.email_pass)

        Note:
            If sender_password fails, ensure:
            1. SMTP AUTH is enabled in Microsoft 365 Admin Center
            2. Use App Password if MFA is enabled on the account
            3. Check GoDaddy domain is properly configured for Microsoft 365
        """
        settings = get_settings()
        self.sender_email = sender_email or settings.email_user
        self.sender_password = sender_password or settings.email_pass

        if not self.sender_email or not self.sender_password:
            logger.warning(
                "Email service initialized without credentials. "
                "Set EMAIL_USER and EMAIL_PASS in environment. "
                "See troubleshooting guide for common Microsoft 365 SMTP issues."
            )

    async def send_email(
        self,
        recipient_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
    ) -> bool:
        """
        Send an email via Office 365 SMTP.

        Args:
            recipient_email: Recipient's email address
            subject: Email subject line
            html_body: HTML email body
            text_body: Plain text fallback (optional)

        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.sender_email or not self.sender_password:
            logger.error("Email service not configured with credentials")
            return False

        try:
            # Create message container
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.sender_email
            msg["To"] = recipient_email

            # Attach plain text (optional fallback)
            if text_body:
                msg.attach(MIMEText(text_body, "plain"))

            # Attach HTML body
            msg.attach(MIMEText(html_body, "html"))

            # Send via SMTP
            async with aiosmtplib.SMTP(
                hostname=self.SMTP_HOST,
                port=self.SMTP_PORT,
                use_tls=self.SMTP_USE_TLS,
            ) as smtp:
                await smtp.login(self.sender_email, self.sender_password)
                await smtp.send_message(msg)

            logger.info(f"Email sent successfully to {recipient_email}")
            return True

        except Exception as exc:
            logger.error(
                f"Failed to send email to {recipient_email}: {exc}",
                exc_info=True,
            )
            return False

    async def send_verification_email(
        self,
        recipient_email: str,
        recipient_name: Optional[str],
        verification_link: str,
    ) -> bool:
        """
        Send an email verification link.

        Args:
            recipient_email: User's email address
            recipient_name: User's name (optional, for personalization)
            verification_link: Full URL to verification endpoint

        Returns:
            True if email sent successfully, False otherwise
        """
        name_greeting = (
            f"Hello {recipient_name},"
            if recipient_name
            else "Hello,"
        )

        subject = "Please verify your email address"

        text_body = f"""
{name_greeting}

Thank you for signing up! Please verify your email address by clicking the link below:

{verification_link}

This link will expire in 24 hours.

If you did not create this account, please ignore this email.

Best regards,
Talky.ai Team
        """.strip()

        html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <div style="max-width: 600px; margin: 0 auto;">
      <h2>Email Verification</h2>
      <p>{name_greeting}</p>
      <p>Thank you for signing up! Please verify your email address by clicking the button below:</p>

      <div style="margin: 30px 0; text-align: center;">
        <a href="{verification_link}"
           style="background-color: #007bff; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; display: inline-block;">
          Verify Email Address
        </a>
      </div>

      <p style="font-size: 12px; color: #666;">
        Or copy and paste this link in your browser:<br>
        <code>{verification_link}</code>
      </p>

      <p style="font-size: 12px; color: #666;">
        This link will expire in 24 hours.
      </p>

      <p style="font-size: 12px; color: #666;">
        If you did not create this account, please ignore this email.
      </p>

      <hr style="margin: 40px 0; border: none; border-top: 1px solid #ddd;">
      <p style="font-size: 12px; color: #666;">
        © 2026 Talky.ai. All rights reserved.
      </p>
    </div>
  </body>
</html>
        """.strip()

        return await self.send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )


# Singleton instance
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get or create the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
