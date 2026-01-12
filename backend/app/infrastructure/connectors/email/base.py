"""
Email Provider Base Class
Abstract interface for email integrations.
"""
from abc import abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability


class EmailMessage(BaseModel):
    """Represents an email message."""
    id: Optional[str] = None
    thread_id: Optional[str] = None
    subject: str = ""
    body: str = ""
    body_html: Optional[str] = None
    from_email: Optional[str] = None
    to: List[str] = []
    cc: List[str] = []
    bcc: List[str] = []
    reply_to: Optional[str] = None
    sent_at: Optional[datetime] = None
    attachments: List[Dict[str, Any]] = []
    
    class Config:
        extra = "allow"


class EmailProvider(BaseConnector):
    """
    Abstract base class for email providers.
    
    Extends BaseConnector with email-specific methods.
    """
    
    @property
    def connector_type(self) -> str:
        return "email"
    
    @property
    def capabilities(self) -> List[ConnectorCapability]:
        return [
            ConnectorCapability.SEND_EMAIL,
            ConnectorCapability.READ_EMAIL,
            ConnectorCapability.LIST_EMAILS
        ]
    
    @abstractmethod
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
        Send an email.
        
        Args:
            to: List of recipient emails
            subject: Email subject
            body: Plain text body
            body_html: Optional HTML body
            cc: Carbon copy recipients
            bcc: Blind carbon copy recipients
            reply_to: Reply-to address
            attachments: List of {filename, content, mime_type}
            
        Returns:
            Sent EmailMessage with provider's message ID
        """
        pass
    
    @abstractmethod
    async def get_email(self, message_id: str) -> EmailMessage:
        """Get a single email by ID."""
        pass
    
    @abstractmethod
    async def list_emails(
        self,
        max_results: int = 20,
        query: Optional[str] = None,
        unread_only: bool = False
    ) -> List[EmailMessage]:
        """List emails with optional filtering."""
        pass
