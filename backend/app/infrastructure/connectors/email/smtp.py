"""
SMTP Connector
System-level SMTP fallback for email sending when no OAuth provider is connected.

Day 26: AI Email System

CONFIGURATION:
This is a platform-level fallback configured by the admin via environment variables.
Users do NOT configure SMTP - they either connect Gmail via OAuth or the system
automatically falls back to this SMTP configuration.

Environment Variables:
    SMTP_HOST: SMTP server hostname (e.g., smtp.gmail.com)
    SMTP_PORT: SMTP port (default: 587 for TLS)
    SMTP_USER: SMTP username/email
    SMTP_PASSWORD: SMTP password or app password
    SMTP_FROM_EMAIL: Default sender email address
    SMTP_FROM_NAME: Default sender display name (optional)
    SMTP_USE_TLS: Use TLS (default: true)
"""
import os
import ssl
import logging
import smtplib
from typing import List, Dict, Any, Optional
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability, OAuthTokens
from app.infrastructure.connectors.email.base import EmailProvider, EmailMessage

logger = logging.getLogger(__name__)


class SMTPConfigError(Exception):
    """Raised when SMTP is not properly configured."""
    pass


class SMTPConnector(EmailProvider):
    """
    SMTP email connector for system-level fallback.
    
    This connector is used when:
    1. A tenant has not connected Gmail via OAuth
    2. The platform wants to send system emails (notifications, etc.)
    
    Unlike OAuth-based connectors, SMTP uses environment variables
    for configuration and doesn't require per-tenant setup.
    """
    
    def __init__(self, tenant_id: str = "system", connector_id: str = "smtp-fallback"):
        """
        Initialize SMTP connector.
        
        Args:
            tenant_id: Tenant ID (defaults to "system" for platform emails)
            connector_id: Connector ID (defaults to "smtp-fallback")
        """
        # Don't call parent __init__ if using default values that would fail validation
        self.tenant_id = tenant_id
        self.connector_id = connector_id
        self._access_token = None
        self._token_expires_at = None
        
        # Load configuration from environment
        self._load_config()
        
        logger.info(f"Initialized SMTP connector (host: {self.host})")
    
    def _load_config(self) -> None:
        """Load SMTP configuration from environment variables."""
        self.host = os.getenv("SMTP_HOST")
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.user = os.getenv("SMTP_USER")
        self.password = os.getenv("SMTP_PASSWORD")
        self.from_email = os.getenv("SMTP_FROM_EMAIL")
        self.from_name = os.getenv("SMTP_FROM_NAME", "Talky.ai")
        self.use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        
        if not all([self.host, self.user, self.password, self.from_email]):
            logger.warning("SMTP not fully configured - fallback will not be available")
    
    @property
    def provider_name(self) -> str:
        return "smtp"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return []  # SMTP doesn't use OAuth
    
    @classmethod
    def is_configured(cls) -> bool:
        """Check if SMTP is properly configured via environment variables."""
        return all([
            os.getenv("SMTP_HOST"),
            os.getenv("SMTP_USER"),
            os.getenv("SMTP_PASSWORD"),
            os.getenv("SMTP_FROM_EMAIL")
        ])
    
    def _validate_config(self) -> None:
        """Validate that SMTP is properly configured."""
        if not all([self.host, self.user, self.password, self.from_email]):
            raise SMTPConfigError(
                "SMTP not configured. Required environment variables: "
                "SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL"
            )
    
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None
    ) -> str:
        """SMTP doesn't use OAuth - not applicable."""
        raise NotImplementedError("SMTP connector doesn't use OAuth")
    
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        """SMTP doesn't use OAuth - not applicable."""
        raise NotImplementedError("SMTP connector doesn't use OAuth")
    
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """SMTP doesn't use OAuth - not applicable."""
        raise NotImplementedError("SMTP connector doesn't use OAuth")
    
    async def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        body_html: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> EmailMessage:
        """
        Send an email via SMTP.
        
        Args:
            to: List of recipient emails
            subject: Email subject
            body: Plain text body
            body_html: Optional HTML body
            cc: Carbon copy recipients
            bcc: Blind carbon copy recipients
            reply_to: Reply-to address
            attachments: Not yet supported
            
        Returns:
            EmailMessage with send confirmation
        """
        self._validate_config()
        
        # Create message
        if body_html:
            message = MIMEMultipart("alternative")
            message.attach(MIMEText(body, "plain", "utf-8"))
            message.attach(MIMEText(body_html, "html", "utf-8"))
        else:
            message = MIMEText(body, "plain", "utf-8")
        
        # Set headers
        message["Subject"] = subject
        message["From"] = formataddr((self.from_name, self.from_email))
        message["To"] = ", ".join(to)
        
        if cc:
            message["Cc"] = ", ".join(cc)
        if reply_to:
            message["Reply-To"] = reply_to
        
        # All recipients for sendmail
        all_recipients = to + (cc or []) + (bcc or [])
        
        try:
            # Connect and send
            if self.use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(self.host, self.port) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.user, self.password)
                    server.sendmail(self.from_email, all_recipients, message.as_string())
            else:
                with smtplib.SMTP(self.host, self.port) as server:
                    server.login(self.user, self.password)
                    server.sendmail(self.from_email, all_recipients, message.as_string())
            
            # Generate pseudo message ID
            message_id = f"smtp-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
            
            logger.info(f"Email sent via SMTP to {len(to)} recipients")
            
            return EmailMessage(
                id=message_id,
                subject=subject,
                body=body,
                body_html=body_html,
                to=to,
                cc=cc or [],
                bcc=bcc or [],
                sent_at=datetime.utcnow()
            )
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            raise ValueError(f"Email authentication failed. Please check SMTP credentials.")
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            raise ValueError(f"Failed to send email: {str(e)}")
    
    async def get_email(self, message_id: str) -> EmailMessage:
        """SMTP is send-only - not applicable."""
        raise NotImplementedError("SMTP connector is send-only")
    
    async def list_emails(
        self,
        max_results: int = 20,
        query: Optional[str] = None,
        unread_only: bool = False
    ) -> List[EmailMessage]:
        """SMTP is send-only - not applicable."""
        raise NotImplementedError("SMTP connector is send-only")


# Factory registration (optional - SMTP is typically used directly)
# ConnectorFactory.register("smtp", SMTPConnector)
