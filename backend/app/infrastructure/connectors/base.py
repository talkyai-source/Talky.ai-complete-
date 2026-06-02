"""
Base Connector Classes
Abstract base class for all external service connectors, factory pattern, and shared types.

Day 24: Unified Connector System
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Type
from datetime import datetime

logger = logging.getLogger(__name__)


class ConnectorCapability(str, Enum):
    """Defines what each connector can do."""
    # Email
    SEND_EMAIL = "send_email"
    READ_EMAIL = "read_email"
    LIST_EMAILS = "list_emails"
    # Calendar
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    LIST_EVENTS = "list_events"
    GET_AVAILABILITY = "get_availability"
    # Drive / Storage
    UPLOAD_FILE = "upload_file"
    DOWNLOAD_FILE = "download_file"
    LIST_FILES = "list_files"
    CREATE_FOLDER = "create_folder"
    # CRM
    LOG_CALL = "log_call"
    CREATE_NOTE = "create_note"


@dataclass
class OAuthTokens:
    """OAuth token response from a provider."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: Optional[int] = None
    expires_at: Optional[datetime] = None
    scope: Optional[str] = None


class BaseConnector(ABC):
    """
    Abstract base class for all external service connectors.

    Every connector must implement:
    - provider_name: unique identifier (e.g. 'google_calendar')
    - connector_type: category (e.g. 'calendar', 'email', 'crm', 'drive')
    - capabilities: list of ConnectorCapability values
    - oauth_scopes: list of OAuth scope strings
    - get_oauth_url(): generate the authorization URL
    - exchange_code(): exchange auth code for tokens
    - refresh_tokens(): refresh an expired access token
    """

    def __init__(self, tenant_id: str, connector_id: str):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not connector_id:
            raise ValueError("connector_id is required")
        self.tenant_id = tenant_id
        self.connector_id = connector_id
        self._access_token: Optional[str] = None

    # ------------------------------------------------------------------
    # Abstract properties
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'google_calendar')."""
        ...

    @property
    @abstractmethod
    def connector_type(self) -> str:
        """Connector category (e.g. 'calendar', 'email', 'crm', 'drive')."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> List[ConnectorCapability]:
        """List of capabilities this connector supports."""
        ...

    @property
    @abstractmethod
    def oauth_scopes(self) -> List[str]:
        """OAuth scopes required by this connector."""
        ...

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None,
    ) -> str:
        """Generate the OAuth authorization URL."""
        ...

    @abstractmethod
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuthTokens:
        """Exchange an authorization code for access/refresh tokens."""
        ...

    @abstractmethod
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh an expired access token."""
        ...

    # ------------------------------------------------------------------
    # Concrete helpers
    # ------------------------------------------------------------------

    async def set_access_token(self, token: str) -> None:
        """Store an access token for subsequent API calls."""
        self._access_token = token

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} provider={self.provider_name} "
            f"type={self.connector_type} tenant={self.tenant_id[:8]}...>"
        )


class ConnectorFactory:
    """
    Factory for creating connector instances.

    Providers register themselves on import:
        ConnectorFactory.register("google_calendar", GoogleCalendarConnector)
    """

    _registry: Dict[str, Type[BaseConnector]] = {}

    @classmethod
    def register(cls, provider_name: str, connector_class: Type[BaseConnector]) -> None:
        """Register a connector class for a provider name."""
        cls._registry[provider_name] = connector_class
        logger.debug(f"Registered connector: {provider_name}")

    @classmethod
    def create(
        cls,
        provider: str,
        tenant_id: str,
        connector_id: str,
    ) -> BaseConnector:
        """Create a connector instance by provider name."""
        connector_class = cls._registry.get(provider)
        if not connector_class:
            raise ValueError(
                f"Unknown connector provider: {provider}. "
                f"Available: {list(cls._registry.keys())}"
            )
        return connector_class(tenant_id=tenant_id, connector_id=connector_id)

    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered provider names."""
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, provider_name: str) -> bool:
        """Check whether a provider is registered."""
        return provider_name in cls._registry

