"""
Tests for Email Service
Day 26: AI Email System
"""
import pytest
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet

# Generate a valid Fernet key for testing
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["CONNECTOR_ENCRYPTION_KEY"] = TEST_FERNET_KEY


class TestEmailServiceImports:
    """Test that all email service components import correctly"""
    
    def test_email_service_import(self):
        """EmailService can be imported"""
        from app.services.email_service import EmailService
        assert EmailService is not None
    
    def test_email_not_connected_error_import(self):
        """EmailNotConnectedError can be imported"""
        from app.services.email_service import EmailNotConnectedError
        assert EmailNotConnectedError is not None
    
    def test_get_email_service_import(self):
        """get_email_service helper can be imported"""
        from app.services.email_service import get_email_service
        assert get_email_service is not None


class TestEmailNotConnectedError:
    """Tests for EmailNotConnectedError exception"""
    
    def test_default_message(self):
        """Exception has user-friendly default message"""
        from app.services.email_service import EmailNotConnectedError
        error = EmailNotConnectedError()
        
        assert "No email provider connected" in error.message
        assert "Gmail" in error.message
    
    def test_custom_message(self):
        """Exception accepts custom message"""
        from app.services.email_service import EmailNotConnectedError
        error = EmailNotConnectedError("Custom error message")
        
        assert error.message == "Custom error message"


class TestEmailServiceInit:
    """Tests for EmailService initialization"""
    
    def test_init_with_supabase(self):
        """EmailService initializes with supabase client"""
        from app.services.email_service import EmailService
        
        mock_supabase = MagicMock()
        service = EmailService(mock_supabase)
        
        assert service.supabase == mock_supabase
        assert service.template_manager is not None
    
    def test_encryption_service_initialized(self):
        """Encryption service is initialized"""
        from app.services.email_service import EmailService
        
        mock_supabase = MagicMock()
        service = EmailService(mock_supabase)
        
        assert service.encryption is not None


class TestGetActiveEmailConnector:
    """Tests for _get_active_email_connector"""
    
    @pytest.mark.asyncio
    async def test_no_email_connected_raises(self):
        """Raises EmailNotConnectedError when no email connector exists"""
        from app.services.email_service import EmailService, EmailNotConnectedError
        
        # Mock supabase to return no connectors
        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.data = []
        
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.execute.return_value = mock_response
        mock_supabase.table.return_value = mock_table
        
        service = EmailService(mock_supabase)
        
        with pytest.raises(EmailNotConnectedError):
            await service._get_active_email_connector("test-tenant-id")


class TestSendEmail:
    """Tests for send_email method"""
    
    @pytest.mark.asyncio
    async def test_send_email_no_connector_raises(self):
        """Raises error when no email connector is connected"""
        from app.services.email_service import EmailService, EmailNotConnectedError
        
        # Mock supabase table calls
        mock_supabase = MagicMock()
        
        # Create separate mock for each table call
        def table_side_effect(table_name):
            mock_table = MagicMock()
            mock_table.select.return_value = mock_table
            mock_table.eq.return_value = mock_table
            mock_table.single.return_value = mock_table
            
            if table_name == "connectors":
                # No connectors found
                mock_table.execute.return_value = MagicMock(data=[])
            elif table_name == "assistant_actions":
                # Action insert succeeds
                mock_table.insert.return_value = mock_table
                mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "test-action"}])
                mock_table.update.return_value = mock_table
                mock_table.update.return_value.eq.return_value = mock_table
                mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            
            return mock_table
        
        mock_supabase.table.side_effect = table_side_effect
        
        service = EmailService(mock_supabase)
        
        with pytest.raises(EmailNotConnectedError):
            await service.send_email(
                tenant_id="test-tenant",
                to=["test@example.com"],
                subject="Test Subject",
                body="Test body"
            )
    

    @pytest.mark.asyncio
    async def test_send_email_with_template(self):
        """Email with template renders correctly before validation"""
        from app.services.email_service import EmailService
        from app.domain.services.email_template_manager import EmailTemplateManager
        
        mock_supabase = MagicMock()
        template_manager = EmailTemplateManager()
        service = EmailService(mock_supabase, template_manager)
        
        # Verify template rendering works
        rendered = template_manager.render_email(
            "meeting_confirmation",
            title="Test Meeting",
            date="2026-01-10",
            time="3:00 PM",
            attendee_name="Jane"
        )
        
        assert "Test Meeting" in rendered.subject
        assert "Jane" in rendered.body


class TestListTemplates:
    """Tests for list_templates method"""
    
    def test_list_templates_returns_info(self):
        """list_templates returns template information"""
        from app.services.email_service import EmailService
        
        mock_supabase = MagicMock()
        service = EmailService(mock_supabase)
        
        templates = service.list_templates()
        
        assert isinstance(templates, list)
        assert len(templates) >= 3
        assert all(isinstance(t, dict) for t in templates)
        assert all("name" in t for t in templates)


class TestContentValidation:
    """Tests for email content validation in service"""
    
    @pytest.mark.asyncio
    async def test_validation_rejects_empty_subject(self):
        """Email with empty subject is rejected"""
        from app.services.email_service import EmailService
        from app.domain.services.email_template_manager import EmailContentValidationError
        
        mock_supabase = MagicMock()
        
        # Mock for action record creation
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "test"}])
        mock_table.update.return_value = mock_table
        mock_supabase.table.return_value = mock_table
        
        service = EmailService(mock_supabase)
        
        with pytest.raises(EmailContentValidationError):
            await service.send_email(
                tenant_id="test-tenant",
                to=["test@example.com"],
                subject="",  # Empty subject
                body="Valid body"
            )


class TestSingletonHelper:
    """Tests for singleton helper"""
    
    def test_get_email_service_returns_service(self):
        """get_email_service returns EmailService instance"""
        from app.services.email_service import get_email_service, EmailService
        
        mock_supabase = MagicMock()
        
        # Reset singleton for testing
        import app.services.email_service as email_module
        email_module._email_service = None
        
        service = get_email_service(mock_supabase)
        
        assert isinstance(service, EmailService)
