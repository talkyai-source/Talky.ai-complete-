"""
CRM Provider Base Class
Abstract interface for CRM integrations.
"""
from abc import abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability


class CRMContact(BaseModel):
    """Represents a CRM contact."""
    id: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    properties: Dict[str, Any] = {}
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        extra = "allow"
    
    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p)


class CRMDeal(BaseModel):
    """Represents a CRM deal/opportunity."""
    id: Optional[str] = None
    name: str = ""
    stage: Optional[str] = None
    amount: Optional[float] = None
    close_date: Optional[datetime] = None
    contact_ids: List[str] = []
    properties: Dict[str, Any] = {}
    
    class Config:
        extra = "allow"


class CRMProvider(BaseConnector):
    """
    Abstract base class for CRM providers.
    
    Extends BaseConnector with CRM-specific methods.
    """
    
    @property
    def connector_type(self) -> str:
        return "crm"
    
    @property
    def capabilities(self) -> List[ConnectorCapability]:
        return [
            ConnectorCapability.CREATE_CONTACT,
            ConnectorCapability.UPDATE_CONTACT,
            ConnectorCapability.LIST_CONTACTS,
            ConnectorCapability.GET_CONTACT,
            ConnectorCapability.CREATE_DEAL
        ]
    
    @abstractmethod
    async def create_contact(
        self,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMContact:
        """Create a new CRM contact."""
        pass
    
    @abstractmethod
    async def update_contact(
        self,
        contact_id: str,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMContact:
        """Update an existing CRM contact."""
        pass
    
    @abstractmethod
    async def get_contact(self, contact_id: str) -> CRMContact:
        """Get a contact by ID."""
        pass
    
    @abstractmethod
    async def list_contacts(
        self,
        limit: int = 100,
        search: Optional[str] = None
    ) -> List[CRMContact]:
        """List contacts with optional search."""
        pass
    
    @abstractmethod
    async def find_contact_by_email(self, email: str) -> Optional[CRMContact]:
        """Find a contact by email address."""
        pass
    
    @abstractmethod
    async def create_deal(
        self,
        name: str,
        stage: str,
        amount: Optional[float] = None,
        close_date: Optional[datetime] = None,
        contact_ids: Optional[List[str]] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMDeal:
        """Create a new deal/opportunity."""
        pass
