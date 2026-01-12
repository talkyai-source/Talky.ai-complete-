"""
Connector Base Classes and Factory
Abstract base class for all connectors with unified factory pattern.

Day 24: Unified Connector System
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Type
from enum import Enum
from pydantic import BaseModel
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ConnectorCapability(str, Enum):
    """Actions a connector can perform"""
    # Calendar capabilities
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    LIST_EVENTS = "list_events"
    GET_AVAILABILITY = "get_availability"
    
    # Email capabilities
    SEND_EMAIL = "send_email"
    READ_EMAIL = "read_email"
    LIST_EMAILS = "list_emails"
    
    # CRM capabilities
    CREATE_CONTACT = "create_contact"
    UPDATE_CONTACT = "update_contact"
    LIST_CONTACTS = "list_contacts"
    GET_CONTACT = "get_contact"
    CREATE_DEAL = "create_deal"
    
    # Drive capabilities
    UPLOAD_FILE = "upload_file"
    DOWNLOAD_FILE = "download_file"
    LIST_FILES = "list_files"
    CREATE_FOLDER = "create_folder"


class OAuthTokens(BaseModel):
    """OAuth token response from provider"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: Optional[int] = None  # Seconds until expiry
    expires_at: Optional[datetime] = None
    scope: Optional[str] = None
    
    class Config:
        extra = "allow"  # Allow additional provider-specific fields


class BaseConnector(ABC):
    """
    Abstract base class for all connectors.
    
    All connectors must:
    - Be bound to a tenant_id (strict tenant isolation)
    - Support OAuth authorization flow
    - Implement token refresh
    - Declare their capabilities
    """
    
    def __init__(self, tenant_id: str, connector_id: str):
        """
        Initialize connector with tenant binding.
        
        Args:
            tenant_id: Required tenant ID (strict binding)
            connector_id: Database connector record ID
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for connector initialization")
        if not connector_id:
            raise ValueError("connector_id is required for connector initialization")
            
        self.tenant_id = tenant_id
        self.connector_id = connector_id
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        logger.info(f"Initialized {self.provider_name} connector for tenant {tenant_id[:8]}...")
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Provider identifier (e.g., 'google_calendar', 'gmail', 'hubspot').
        Must match ConnectorProvider enum value.
        """
        pass
    
    @property
    @abstractmethod
    def connector_type(self) -> str:
        """
        Connector type (calendar, email, crm, drive).
        Must match ConnectorType enum value.
        """
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> List[ConnectorCapability]:
        """List of supported capabilities for this connector."""
        pass
    
    @property
    @abstractmethod
    def oauth_scopes(self) -> List[str]:
        """OAuth scopes required for this connector."""
        pass
    
    @abstractmethod
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None
    ) -> str:
        """
        Generate OAuth authorization URL.
        
        Args:
            redirect_uri: Callback URL after authorization
            state: CSRF protection state parameter
            code_challenge: PKCE code challenge (S256)
            
        Returns:
            Authorization URL to redirect user to
        """
        pass
    
    @abstractmethod
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        """
        Exchange authorization code for tokens.
        
        Args:
            code: Authorization code from callback
            redirect_uri: Same redirect_uri used in authorization
            code_verifier: PKCE code verifier
            
        Returns:
            OAuthTokens with access and refresh tokens
        """
        pass
    
    @abstractmethod
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """
        Refresh access token using refresh token.
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            New OAuthTokens (may include new refresh token)
        """
        pass
    
    async def set_access_token(
        self,
        token: str,
        expires_at: Optional[datetime] = None
    ) -> None:
        """
        Set the access token for API calls.
        
        Args:
            token: Valid access token
            expires_at: Token expiration time
        """
        self._access_token = token
        self._token_expires_at = expires_at
        logger.debug(f"Access token set for {self.provider_name}")
    
    def is_token_expired(self) -> bool:
        """Check if current token is expired or about to expire."""
        if not self._token_expires_at:
            return False
        # Consider expired if within 5 minutes of expiry
        from datetime import timedelta
        buffer = timedelta(minutes=5)
        return datetime.utcnow() >= (self._token_expires_at - buffer)
    
    def has_capability(self, capability: ConnectorCapability) -> bool:
        """Check if connector supports a capability."""
        return capability in self.capabilities
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(tenant={self.tenant_id[:8]}..., type={self.connector_type})"


class ConnectorFactory:
    """
    Factory for creating connector instances.
    
    Follows the same pattern as TelephonyFactory and MediaGatewayFactory.
    All connectors must be registered before use.
    """
    
    _connectors: Dict[str, Type[BaseConnector]] = {}
    
    @classmethod
    def register(cls, provider: str, connector_class: Type[BaseConnector]) -> None:
        """
        Register a connector class for a provider.
        
        Args:
            provider: Provider name (e.g., 'google_calendar')
            connector_class: Class implementing BaseConnector
        """
        cls._connectors[provider] = connector_class
        logger.info(f"Registered connector: {provider}")
    
    @classmethod
    def create(
        cls,
        provider: str,
        tenant_id: str,
        connector_id: str
    ) -> BaseConnector:
        """
        Create a connector instance.
        
        Args:
            provider: Provider name (e.g., 'google_calendar')
            tenant_id: Tenant ID (required for isolation)
            connector_id: Database connector record ID
            
        Returns:
            Configured connector instance
            
        Raises:
            ValueError: If provider is not registered
        """
        if provider not in cls._connectors:
            available = ", ".join(cls._connectors.keys()) if cls._connectors else "None"
            raise ValueError(
                f"Unknown connector provider: {provider}. "
                f"Available: {available}"
            )
        
        connector_class = cls._connectors[provider]
        return connector_class(tenant_id=tenant_id, connector_id=connector_id)
    
    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered provider names."""
        return list(cls._connectors.keys())
    
    @classmethod
    def get_provider_info(cls, provider: str) -> Dict[str, Any]:
        """
        Get information about a registered provider.
        
        Returns:
            Dict with type, capabilities, and scopes
        """
        if provider not in cls._connectors:
            raise ValueError(f"Unknown provider: {provider}")
        
        # Create temporary instance to get metadata
        # We use dummy IDs since we just need class properties
        connector = cls._connectors[provider]
        
        return {
            "provider": provider,
            "type": connector.__dict__.get("_connector_type", "unknown"),
            "capabilities": [],  # Would need instance
            "scopes": []  # Would need instance
        }
    
    @classmethod
    def is_registered(cls, provider: str) -> bool:
        """Check if a provider is registered."""
        return provider in cls._connectors
