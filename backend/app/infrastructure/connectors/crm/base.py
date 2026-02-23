"""
CRM Provider Base Class
Abstract interface for CRM integrations.

Day 24: Unified Connector System
Day 30: Added log_call() and create_note() abstract methods
"""
from abc import abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability


class CRMProvider(BaseConnector):
    """
    Abstract base class for CRM providers.

    Extends BaseConnector with CRM-specific methods for:
    - Contact search
    - Call logging
    - Note creation
    """

    @property
    def connector_type(self) -> str:
        return "crm"

    @property
    def capabilities(self) -> List[ConnectorCapability]:
        return [
            ConnectorCapability.LOG_CALL,
            ConnectorCapability.CREATE_NOTE,
        ]

    @abstractmethod
    async def search_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Search for a contact by email or phone.

        Returns:
            Contact dict with at least 'id' key, or None if not found.
        """
        ...

    @abstractmethod
    async def create_contact(
        self,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new contact.

        Returns:
            Created contact dict with 'id' key.
        """
        ...

    @abstractmethod
    async def log_call(
        self,
        contact_id: str,
        call_body: str,
        duration_seconds: int,
        outcome: str = "COMPLETED",
        call_direction: str = "OUTBOUND",
        timestamp: Optional[datetime] = None,
    ) -> str:
        """
        Log a call activity associated with a contact.

        Returns:
            Provider's call activity ID.
        """
        ...

    @abstractmethod
    async def create_note(
        self,
        contact_id: str,
        note_body: str,
        timestamp: Optional[datetime] = None,
    ) -> str:
        """
        Create a note attached to a contact.

        Returns:
            Provider's note ID.
        """
        ...

