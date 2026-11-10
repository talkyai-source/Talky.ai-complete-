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

import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def frontend_link(path_or_link: str, *, base_url: str | None = None) -> str:
    """Keep the account path/query on the configured frontend, never its old host."""
    base = urlsplit(base_url or get_settings().frontend_url)
    target = urlsplit(path_or_link)
    path = f"{base.path.rstrip('/')}/{target.path.lstrip('/')}"
    return urlunsplit((base.scheme, base.netloc, path, target.query, target.fragment))


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
        self.from_email = getattr(settings, "from_email", None) or self.sender_email
        self.brand_name = getattr(settings, "brand_name", "Talk-Lee")
        self.frontend_url = settings.frontend_url

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
        reply_to: Optional[str] = None,
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
            msg["From"] = formataddr((self.brand_name, self.from_email))
            msg["To"] = recipient_email
            if reply_to:
                msg["Reply-To"] = reply_to

            # Attach plain text (optional fallback)
            if text_body:
                msg.attach(MIMEText(text_body, "plain"))

            # Attach HTML body
            msg.attach(MIMEText(html_body, "html"))

            # Send via SMTP. Microsoft 365 on port 587 uses STARTTLS:
            # the connection opens plaintext, then upgrades to TLS after
            # EHLO. aiosmtplib's `use_tls=True` is for implicit TLS on
            # port 465 (SMTPS) and produces "SSL: WRONG_VERSION_NUMBER"
            # against 587. Correct flag is `start_tls=True`.
            async with asyncio.timeout(20):
                async with aiosmtplib.SMTP(
                    hostname=self.SMTP_HOST,
                    port=self.SMTP_PORT,
                    start_tls=self.SMTP_USE_TLS,
                    timeout=10,
                ) as smtp:
                    await smtp.login(self.sender_email, self.sender_password)
                    await smtp.send_message(msg)

            logger.info("Account email accepted by SMTP")
            return True

        except Exception as exc:
            logger.error(
                "Account email outcome unconfirmed error_type=%s", type(exc).__name__,
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
        verification_link = frontend_link(verification_link, base_url=self.frontend_url)
        html_greeting = escape(name_greeting)
        html_link = escape(verification_link, quote=True)
        html_brand = escape(self.brand_name)

        subject = "Please verify your email address"

        text_body = f"""
{name_greeting}

Thank you for signing up! Please verify your email address by clicking the link below:

{verification_link}

This link will expire in 24 hours.

If you did not create this account, please ignore this email.

Best regards,
{self.brand_name} Team
        """.strip()

        html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <div style="max-width: 600px; margin: 0 auto;">
      <h2>Email Verification</h2>
      <p>{html_greeting}</p>
      <p>Thank you for signing up! Please verify your email address by clicking the button below:</p>

      <div style="margin: 30px 0; text-align: center;">
        <a href="{html_link}"
           style="background-color: #007bff; color: white; padding: 12px 30px; text-decoration: none; border-radius: 4px; display: inline-block;">
          Verify Email Address
        </a>
      </div>

      <p style="font-size: 12px; color: #666;">
        Or copy and paste this link in your browser:<br>
        <code>{html_link}</code>
      </p>

      <p style="font-size: 12px; color: #666;">
        This link will expire in 24 hours.
      </p>

      <p style="font-size: 12px; color: #666;">
        If you did not create this account, please ignore this email.
      </p>

      <hr style="margin: 40px 0; border: none; border-top: 1px solid #ddd;">
      <p style="font-size: 12px; color: #666;">
        © 2026 {html_brand}. All rights reserved.
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

    async def send_signup_code_email(
        self,
        recipient_email: str,
        recipient_name: Optional[str],
        code: str,
        expires_in_minutes: int = 15,
    ) -> bool:
        """
        Send a 6-digit signup verification code.

        Used by the two-step signup flow where the code is verified BEFORE
        the user_profiles row is created (unlike send_verification_email
        which sends a click-to-verify link AFTER account creation).
        """
        name_greeting = (
            f"Hi {recipient_name}," if recipient_name else "Hi,"
        )

        subject = f"Your {self.brand_name} verification code: {code}"
        html_greeting = escape(name_greeting)
        html_code = escape(str(code))
        html_expiry = escape(str(expires_in_minutes))
        html_brand = escape(self.brand_name)

        text_body = f"""
{name_greeting}

Your {self.brand_name} verification code is: {code}

Enter this code on the signup page to continue. The code expires in
{expires_in_minutes} minutes.

If you did not request this code, please ignore this email.

— {self.brand_name}
        """.strip()

        html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <div style="max-width: 600px; margin: 0 auto;">
      <h2>Your verification code</h2>
      <p>{html_greeting}</p>
      <p>Enter this code on the signup page to continue:</p>
      <div style="margin: 30px 0; text-align: center;">
        <div style="display: inline-block; background:#f5f5f5;
                    padding: 16px 32px; font-size: 32px;
                    letter-spacing: 8px; font-weight: bold;
                    font-family: monospace; border-radius: 8px;">
          {html_code}
        </div>
      </div>
      <p style="font-size: 13px; color: #666;">
        The code expires in {html_expiry} minutes. If you did not
        request this code, you can safely ignore this email.
      </p>
      <hr style="margin: 40px 0; border: none; border-top: 1px solid #ddd;">
      <p style="font-size: 12px; color: #666;">
        © 2026 {html_brand}. All rights reserved.
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

    async def send_password_reset_email(
        self,
        recipient_email: str,
        recipient_name: Optional[str],
        code: str,
        expires_in_minutes: int = 15,
    ) -> bool:
        """Send a reset-specific code without implying a new account was created."""
        greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
        reset_link = frontend_link("/auth/forgot-password", base_url=self.frontend_url)
        subject = f"Your {self.brand_name} password reset code"
        text_body = (
            f"{greeting}\n\nSomeone asked to reset the password for your {self.brand_name} account.\n\n"
            f"Your password reset code is: {code}\nEnter it in the reset form you already opened.\n"
            f"To start a new reset request, visit:\n{reset_link}\n\n"
            f"The code expires in {expires_in_minutes} minutes.\n"
            f"If this wasn't you, ignore this email. Your password has not changed.\n\n— {self.brand_name}"
        )
        html_body = f"""<html><body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto;">
            <h2>Password reset</h2><p>{escape(greeting)}</p>
            <p>Someone asked to reset the password for your {escape(self.brand_name)} account.</p>
            <p>Your password reset code is:</p>
            <p style="font-family: monospace; font-size: 32px; letter-spacing: 8px;">{escape(str(code))}</p>
            <p>Enter it in the reset form you already opened.</p>
            <p>To start a new reset request, visit the <a href="{escape(reset_link, quote=True)}">password recovery page</a>.</p>
            <p>The code expires in {escape(str(expires_in_minutes))} minutes.</p>
            <p>If this wasn't you, ignore this email. Your password has not changed.</p>
            <p>— {escape(self.brand_name)}</p></div></body></html>"""
        return await self.send_email(recipient_email=recipient_email, subject=subject,
                                     html_body=html_body, text_body=text_body)

    async def send_team_invite_email(
        self,
        recipient_email: str,
        workspace_name: str,
        inviter: str,
        role_name: str,
        token: str,
        expires_at: str,
    ) -> bool:
        """QA F1-15 / D4-2: invite someone to join a workspace.

        The token rides in the link's fragment, which browsers never send to
        a server, so it stays out of access logs and Referer headers.
        """
        link = frontend_link(f"/auth/accept-invite#token={token}", base_url=self.frontend_url)
        # One line each: a workspace or person name must never add a header.
        workspace = " ".join(str(workspace_name).split()) or "a workspace"
        who = " ".join(str(inviter).split()) or "Someone"
        role = _INVITE_ROLE_LABELS.get(role_name, role_name)
        until = datetime.fromisoformat(expires_at).strftime("%d %B %Y")
        subject = f"{who} invited you to {workspace} on {self.brand_name}"
        text_body = (
            f"Hi,\n\n{who} invited you to join {workspace} on {self.brand_name} as {role}.\n\n"
            f"Accept the invite and choose your password here:\n{link}\n\n"
            f"The link works until {until} and can be used once.\n"
            f"If you were not expecting this, ignore this email.\n\n— {self.brand_name}"
        )
        html_body = f"""<html><body style="font-family: Arial, sans-serif; color: #333;">
            <div style="max-width: 600px; margin: 0 auto;">
            <h2>You're invited</h2><p>Hi,</p>
            <p>{escape(who)} invited you to join {escape(workspace)} on {escape(self.brand_name)} as {escape(role)}.</p>
            <p><a href="{escape(link, quote=True)}">Accept the invite and choose your password</a></p>
            <p>The link works until {escape(until)} and can be used once.</p>
            <p>If you were not expecting this, ignore this email.</p>
            <p>— {escape(self.brand_name)}</p></div></body></html>"""
        return await self.send_email(recipient_email=recipient_email, subject=subject,
                                     html_body=html_body, text_body=text_body)


# The names the Team page shows for each role an invite can carry.
_INVITE_ROLE_LABELS = {
    "tenant_admin": "an admin",
    "campaign_manager": "a campaign manager",
    "user": "a standard user",
    "agent": "an agent",
    "billing_user": "a billing contact",
    "readonly": "a read-only member",
}


# Singleton instance
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get or create the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
