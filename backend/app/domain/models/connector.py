"""
Connector Domain Models
Defines external integration types (calendar, email, CRM, etc.)
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ConnectorType(str, Enum):
    """Types of external connectors"""
    CALENDAR = "calendar"
    EMAIL = "email"
    CRM = "crm"
    DRIVE = "drive"
    SMS = "sms"


class ConnectorProvider(str, Enum):
    """Supported providers for each connector type"""
    # Calendar
    GOOGLE_CALENDAR = "google_calendar"
    MICROSOFT_OUTLOOK = "microsoft_outlook"
    
    # Email
    GMAIL = "gmail"
    MICROSOFT_EMAIL = "microsoft_email"
    SENDGRID = "sendgrid"
    
    # CRM
    SALESFORCE = "salesforce"
    HUBSPOT = "hubspot"
    
    # SMS
    TWILIO = "twilio"
    VONAGE_SMS = "vonage_sms"
    
    # Drive/Storage
    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"


class ConnectorStatus(str, Enum):
    """Status of a connector"""
    PENDING = "pending"      # OAuth flow not complete
    ACTIVE = "active"        # Ready to use
    ERROR = "error"          # Authentication error
    DISCONNECTED = "disconnected"  # User disconnected
    EXPIRED = "expired"      # Token expired, needs refresh


class Connector(BaseModel):
    """External service connector registration"""
    id: str
    tenant_id: str
    type: ConnectorType
    provider: ConnectorProvider
    name: Optional[str] = None
    status: ConnectorStatus = ConnectorStatus.PENDING
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    
    class Config:
        use_enum_values = True


class ConnectorAccountStatus(str, Enum):
    """Status of a connector account (OAuth tokens)"""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ConnectorAccount(BaseModel):
    """OAuth tokens for a connector (tokens stored encrypted in DB)"""
    id: str
    connector_id: str
    tenant_id: str
    external_account_id: Optional[str] = None
    account_email: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    status: ConnectorAccountStatus = ConnectorAccountStatus.ACTIVE
    token_expires_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    # Note: access_token_encrypted and refresh_token_encrypted
    # are NOT included in this model for security
    # They are only accessed via the encryption service
    
    class Config:
        use_enum_values = True


# Provider capabilities mapping
PROVIDER_CAPABILITIES = {
    ConnectorProvider.GOOGLE_CALENDAR: {
        "type": ConnectorType.CALENDAR,
        "actions": ["book_meeting", "update_meeting", "cancel_meeting", "list_events"],
        "oauth_scopes": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events"
        ]
    },
    ConnectorProvider.GMAIL: {
        "type": ConnectorType.EMAIL,
        "actions": ["send_email", "read_email"],
        "oauth_scopes": [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly"
        ]
    },
    ConnectorProvider.TWILIO: {
        "type": ConnectorType.SMS,
        "actions": ["send_sms"],
        "requires_oauth": False  # Uses API key instead
    },
    ConnectorProvider.VONAGE_SMS: {
        "type": ConnectorType.SMS,
        "actions": ["send_sms"],
        "requires_oauth": False
    }
}
