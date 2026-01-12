"""
Tests for Connector Factory
Day 24: Unified Connector System
"""
import pytest
import os

# Set test environment
os.environ.setdefault("CONNECTOR_ENCRYPTION_KEY", "test_key_for_testing_only_32bytes!")


class TestConnectorFactory:
    """Tests for connector factory pattern"""
    
    def test_register_and_create(self):
        """Registered connectors can be created"""
        from app.infrastructure.connectors.base import ConnectorFactory, BaseConnector
        
        # MockConnector for testing
        class MockConnector(BaseConnector):
            @property
            def provider_name(self) -> str:
                return "mock_provider"
            
            @property
            def connector_type(self) -> str:
                return "mock"
            
            @property
            def capabilities(self):
                return []
            
            @property
            def oauth_scopes(self):
                return ["mock.scope"]
            
            def get_oauth_url(self, redirect_uri, state, code_challenge=None):
                return f"https://mock.oauth?state={state}"
            
            async def exchange_code(self, code, redirect_uri, code_verifier=None):
                return None
            
            async def refresh_tokens(self, refresh_token):
                return None
        
        # Register and create
        ConnectorFactory.register("mock_test", MockConnector)
        
        connector = ConnectorFactory.create(
            provider="mock_test",
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert isinstance(connector, MockConnector)
        assert connector.tenant_id == "tenant-123"
        assert connector.connector_id == "connector-456"
        assert connector.provider_name == "mock_provider"
    
    def test_unknown_provider_raises(self):
        """Unknown provider raises ValueError"""
        from app.infrastructure.connectors.base import ConnectorFactory
        
        with pytest.raises(ValueError) as exc_info:
            ConnectorFactory.create(
                provider="totally_unknown_provider",
                tenant_id="tenant-123",
                connector_id="connector-456"
            )
        
        assert "Unknown connector provider" in str(exc_info.value)
    
    def test_list_providers(self):
        """All registered providers are listed"""
        from app.infrastructure.connectors.base import ConnectorFactory
        
        # Import providers to register them
        from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
        from app.infrastructure.connectors.email.gmail import GmailConnector
        
        providers = ConnectorFactory.list_providers()
        
        assert "google_calendar" in providers
        assert "gmail" in providers
    
    def test_tenant_id_required(self):
        """Connector instantiation requires tenant_id"""
        from app.infrastructure.connectors.base import BaseConnector
        
        class TestConnector(BaseConnector):
            @property
            def provider_name(self): return "test"
            @property
            def connector_type(self): return "test"
            @property
            def capabilities(self): return []
            @property
            def oauth_scopes(self): return []
            def get_oauth_url(self, *args, **kwargs): return ""
            async def exchange_code(self, *args, **kwargs): return None
            async def refresh_tokens(self, *args, **kwargs): return None
        
        with pytest.raises(ValueError) as exc_info:
            TestConnector(tenant_id="", connector_id="123")
        
        assert "tenant_id is required" in str(exc_info.value)
    
    def test_connector_id_required(self):
        """Connector instantiation requires connector_id"""
        from app.infrastructure.connectors.base import BaseConnector
        
        class TestConnector(BaseConnector):
            @property
            def provider_name(self): return "test"
            @property
            def connector_type(self): return "test"
            @property
            def capabilities(self): return []
            @property
            def oauth_scopes(self): return []
            def get_oauth_url(self, *args, **kwargs): return ""
            async def exchange_code(self, *args, **kwargs): return None
            async def refresh_tokens(self, *args, **kwargs): return None
        
        with pytest.raises(ValueError) as exc_info:
            TestConnector(tenant_id="123", connector_id="")
        
        assert "connector_id is required" in str(exc_info.value)
    
    def test_is_registered(self):
        """Check if provider is registered"""
        from app.infrastructure.connectors.base import ConnectorFactory
        
        # Import to register
        from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
        
        assert ConnectorFactory.is_registered("google_calendar") is True
        assert ConnectorFactory.is_registered("nonexistent_provider") is False


class TestGoogleCalendarConnector:
    """Tests for Google Calendar connector"""
    
    def test_oauth_url_generation(self):
        """OAuth URL is generated correctly"""
        import os
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
        
        from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
        
        connector = GoogleCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state-123",
            code_challenge="test-challenge"
        )
        
        assert "accounts.google.com" in url
        assert "test-client-id" in url
        assert "state=test-state-123" in url
        assert "code_challenge=test-challenge" in url
        assert "calendar" in url.lower()
    
    def test_provider_properties(self):
        """Connector has correct properties"""
        import os
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
        
        from app.infrastructure.connectors.calendar.google_calendar import GoogleCalendarConnector
        
        connector = GoogleCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert connector.provider_name == "google_calendar"
        assert connector.connector_type == "calendar"
        assert len(connector.oauth_scopes) > 0
        assert any("calendar" in scope for scope in connector.oauth_scopes)


class TestGmailConnector:
    """Tests for Gmail connector"""
    
    def test_oauth_url_generation(self):
        """OAuth URL is generated correctly"""
        import os
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
        
        from app.infrastructure.connectors.email.gmail import GmailConnector
        
        connector = GmailConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state-123"
        )
        
        assert "accounts.google.com" in url
        assert "gmail" in url.lower()
    
    def test_provider_properties(self):
        """Connector has correct properties"""
        import os
        os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
        
        from app.infrastructure.connectors.email.gmail import GmailConnector
        
        connector = GmailConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert connector.provider_name == "gmail"
        assert connector.connector_type == "email"
        assert any("gmail" in scope for scope in connector.oauth_scopes)


class TestHubSpotConnector:
    """Tests for HubSpot connector"""
    
    def test_oauth_url_generation(self):
        """OAuth URL is generated correctly"""
        import os
        os.environ["HUBSPOT_CLIENT_ID"] = "test-hubspot-id"
        os.environ["HUBSPOT_CLIENT_SECRET"] = "test-hubspot-secret"
        
        from app.infrastructure.connectors.crm.hubspot import HubSpotConnector
        
        connector = HubSpotConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state-123"
        )
        
        assert "hubspot.com" in url
        assert "test-hubspot-id" in url
        assert "state=test-state-123" in url
    
    def test_provider_properties(self):
        """Connector has correct properties"""
        import os
        os.environ["HUBSPOT_CLIENT_ID"] = "test-hubspot-id"
        os.environ["HUBSPOT_CLIENT_SECRET"] = "test-hubspot-secret"
        
        from app.infrastructure.connectors.crm.hubspot import HubSpotConnector
        
        connector = HubSpotConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert connector.provider_name == "hubspot"
        assert connector.connector_type == "crm"
        assert any("contacts" in scope for scope in connector.oauth_scopes)
