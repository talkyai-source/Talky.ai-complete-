"""
Lead Domain Models
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class Lead(BaseModel):
    """Lead/Contact for calling"""
    id: str
    # MULTI-TENANT: Uncomment the line below to enable multi-tenancy
    # tenant_id: str  # Tenant identifier for multi-tenant isolation
    campaign_id: str
    phone_number: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    custom_fields: Dict[str, Any] = {}  # Additional data
    created_at: datetime
    last_called_at: Optional[datetime] = None
    call_attempts: int = 0
    status: str = "pending"  # pending, called, completed, dnc
