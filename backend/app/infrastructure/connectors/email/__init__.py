"""
Email Provider Package
"""
from app.infrastructure.connectors.email.base import EmailProvider, EmailMessage
from app.infrastructure.connectors.email.gmail import GmailConnector
from app.infrastructure.connectors.email.smtp import SMTPConnector

__all__ = ["EmailProvider", "EmailMessage", "GmailConnector", "SMTPConnector"]

