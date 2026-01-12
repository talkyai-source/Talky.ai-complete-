"""
Tests for Outlook Calendar Connector
Day 25: Meeting Booking Feature
"""
import pytest
import os
from cryptography.fernet import Fernet

# Generate a valid Fernet key for testing
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["CONNECTOR_ENCRYPTION_KEY"] = TEST_FERNET_KEY
os.environ.setdefault("MICROSOFT_CLIENT_ID", "test-microsoft-client-id")
os.environ.setdefault("MICROSOFT_CLIENT_SECRET", "test-microsoft-secret")


class TestOutlookCalendarConnectorImport:
    """Test that Outlook connector imports correctly"""
    
    def test_import_from_module(self):
        """OutlookCalendarConnector can be imported from module"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        assert OutlookCalendarConnector is not None
    
    def test_import_from_package(self):
        """OutlookCalendarConnector can be imported from package"""
        from app.infrastructure.connectors.calendar import OutlookCalendarConnector
        assert OutlookCalendarConnector is not None
    
    def test_registered_in_factory(self):
        """OutlookCalendarConnector is registered in ConnectorFactory"""
        from app.infrastructure.connectors.base import ConnectorFactory
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        assert ConnectorFactory.is_registered("outlook_calendar") is True


class TestOutlookCalendarConnectorProperties:
    """Test Outlook connector properties"""
    
    def test_provider_name(self):
        """Provider name is outlook_calendar"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert connector.provider_name == "outlook_calendar"
    
    def test_connector_type(self):
        """Connector type is calendar"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        assert connector.connector_type == "calendar"
    
    def test_oauth_scopes(self):
        """OAuth scopes include required permissions"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        scopes = connector.oauth_scopes
        assert "Calendars.ReadWrite" in scopes
        assert "offline_access" in scopes


class TestOutlookOAuthUrl:
    """Test OAuth URL generation"""
    
    def test_oauth_url_contains_microsoft_domain(self):
        """OAuth URL points to Microsoft login"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state-123"
        )
        
        assert "login.microsoftonline.com" in url
    
    def test_oauth_url_contains_client_id(self):
        """OAuth URL contains client ID"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state-123"
        )
        
        assert "test-microsoft-client-id" in url
    
    def test_oauth_url_contains_state(self):
        """OAuth URL contains state parameter"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="my-unique-state-789"
        )
        
        assert "state=my-unique-state-789" in url
    
    def test_oauth_url_contains_code_challenge_when_provided(self):
        """OAuth URL contains PKCE code challenge when provided"""
        from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
        
        connector = OutlookCalendarConnector(
            tenant_id="tenant-123",
            connector_id="connector-456"
        )
        
        url = connector.get_oauth_url(
            redirect_uri="https://example.com/callback",
            state="test-state",
            code_challenge="test-code-challenge-abc123"
        )
        
        assert "code_challenge=test-code-challenge-abc123" in url


class TestOutlookMissingCredentials:
    """Test behavior when credentials are missing"""
    
    def test_missing_client_id_raises(self):
        """Raises error when MICROSOFT_CLIENT_ID is missing"""
        # Temporarily remove env var
        original = os.environ.get("MICROSOFT_CLIENT_ID")
        del os.environ["MICROSOFT_CLIENT_ID"]
        
        try:
            from app.infrastructure.connectors.calendar.outlook_calendar import OutlookCalendarConnector
            
            connector = OutlookCalendarConnector(
                tenant_id="tenant-123",
                connector_id="connector-456"
            )
            
            with pytest.raises(ValueError) as exc_info:
                connector.get_oauth_url(
                    redirect_uri="https://example.com/callback",
                    state="test-state"
                )
            
            assert "credentials not configured" in str(exc_info.value)
        finally:
            # Restore
            if original:
                os.environ["MICROSOFT_CLIENT_ID"] = original
