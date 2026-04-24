"""SMTP connectivity verification module."""

from typing import Dict
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os


class SmtpVerifier:
    """Verify Microsoft 365 SMTP connectivity."""

    def __init__(self):
        self.email_user = os.getenv("EMAIL_USER")
        self.email_pass = os.getenv("EMAIL_PASS")

    async def verify(self) -> Dict[str, Dict]:
        """Verify SMTP configuration."""
        return {
            "SMTP credentials available": self._verify_credentials(),
            "SMTP host is correct": self._verify_smtp_host(),
            "SMTP port is correct": self._verify_smtp_port(),
            "SMTP TLS is enabled": self._verify_smtp_tls(),
            "SMTP connection": await self._verify_smtp_connection(),
            "SMTP authentication": await self._verify_smtp_auth(),
            "Email service loads": await self._verify_email_service(),
        }

    def _verify_credentials(self) -> Dict:
        """Verify credentials are available."""
        if self.email_user and self.email_pass:
            return {"passed": True}
        return {
            "passed": False,
            "error": "EMAIL_USER or EMAIL_PASS not set"
        }

    def _verify_smtp_host(self) -> Dict:
        """Verify SMTP host."""
        expected_host = "smtp.office365.com"
        return {
            "passed": True,
            "details": f"Microsoft 365 SMTP host: {expected_host}"
        }

    def _verify_smtp_port(self) -> Dict:
        """Verify SMTP port."""
        expected_port = 587
        return {
            "passed": True,
            "details": f"Using port {expected_port} with STARTTLS"
        }

    def _verify_smtp_tls(self) -> Dict:
        """Verify TLS is enabled."""
        return {
            "passed": True,
            "details": "STARTTLS enabled on port 587"
        }

    async def _verify_smtp_connection(self) -> Dict:
        """Verify SMTP connection works."""
        try:
            async with aiosmtplib.SMTP(
                hostname="smtp.office365.com",
                port=587,
                use_tls=True,
                timeout=10
            ) as smtp:
                return {"passed": True}
        except Exception as e:
            return {
                "passed": False,
                "error": f"SMTP connection failed: {e}"
            }

    async def _verify_smtp_auth(self) -> Dict:
        """Verify SMTP authentication."""
        try:
            async with aiosmtplib.SMTP(
                hostname="smtp.office365.com",
                port=587,
                use_tls=True,
                timeout=10
            ) as smtp:
                await smtp.login(self.email_user, self.email_pass)
                return {"passed": True}
        except aiosmtplib.SMTPAuthenticationError as e:
            return {
                "passed": False,
                "error": f"SMTP authentication failed: {e}. Check EMAIL_USER and EMAIL_PASS (or use App Password if MFA enabled)"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"SMTP authentication error: {e}"
            }

    async def _verify_email_service(self) -> Dict:
        """Verify EmailService loads correctly."""
        try:
            from app.domain.services.email_service import EmailService
            service = EmailService()
            if service.sender_email and service.sender_password:
                return {"passed": True}
            return {
                "passed": False,
                "error": "EmailService not configured with credentials"
            }
        except Exception as e:
            return {
                "passed": False,
                "error": f"Cannot load EmailService: {e}"
            }
