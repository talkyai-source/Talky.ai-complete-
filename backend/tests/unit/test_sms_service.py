"""
Unit Tests for SMS Service
Tests for SMSService and SMS template rendering.

Day 27: Timed Communication System
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.sms_service import SMSService, SMSNotConfiguredError
from app.domain.services.sms_template_manager import (
    SMSTemplateManager,
    SMSTemplateType,
    get_sms_template_manager
)
from app.infrastructure.connectors.sms import SMSResult


class TestSMSTemplateManager:
    """Tests for SMSTemplateManager."""
    
    def test_get_template_returns_template(self):
        """Test getting a template by name."""
        manager = SMSTemplateManager()
        template = manager.get_template(SMSTemplateType.MEETING_REMINDER_24H.value)
        
        assert template is not None
        assert template.name == "Meeting Reminder (24h)"
        assert "name" in template.required_vars
    
    def test_get_template_raises_for_unknown(self):
        """Test that getting unknown template raises ValueError."""
        manager = SMSTemplateManager()
        
        with pytest.raises(ValueError, match="Unknown SMS template"):
            manager.get_template("nonexistent_template")
    
    def test_render_meeting_reminder_24h(self):
        """Test rendering 24h meeting reminder."""
        manager = SMSTemplateManager()
        result = manager.render_meeting_reminder(
            reminder_type="24h",
            name="John",
            title="Demo Call",
            time="2:00 PM"
        )
        
        assert "John" in result
        assert "Demo Call" in result
        assert "tomorrow" in result.lower()
    
    def test_render_meeting_reminder_1h(self):
        """Test rendering 1h meeting reminder."""
        manager = SMSTemplateManager()
        result = manager.render_meeting_reminder(
            reminder_type="1h",
            name="Jane",
            title="Strategy Meeting",
            time="3:00 PM"
        )
        
        assert "Jane" in result
        assert "Strategy Meeting" in result
        assert "1 hour" in result
    
    def test_render_meeting_reminder_10m(self):
        """Test rendering 10m meeting reminder with join link."""
        manager = SMSTemplateManager()
        result = manager.render_meeting_reminder(
            reminder_type="10m",
            name="Bob",
            title="Quick Sync",
            time="4:00 PM",
            join_link="https://meet.google.com/abc"
        )
        
        assert "Bob" in result
        assert "Quick Sync" in result
        assert "10 min" in result
        assert "https://meet.google.com/abc" in result
    
    def test_render_meeting_reminder_10m_without_link(self):
        """Test 10m reminder falls back when no join link."""
        manager = SMSTemplateManager()
        result = manager.render_meeting_reminder(
            reminder_type="10m",
            name="Bob",
            title="Quick Sync",
            time="4:00 PM",
            join_link=None
        )
        
        assert "See calendar" in result
    
    def test_list_templates(self):
        """Test listing available templates."""
        manager = SMSTemplateManager()
        templates = manager.list_templates()
        
        assert len(templates) >= 5
        assert SMSTemplateType.MEETING_REMINDER_24H.value in templates
        assert SMSTemplateType.MEETING_REMINDER_1H.value in templates
        assert SMSTemplateType.MEETING_REMINDER_10M.value in templates
    
    def test_get_template_info(self):
        """Test getting template metadata."""
        manager = SMSTemplateManager()
        info = manager.get_template_info(SMSTemplateType.MEETING_REMINDER_1H.value)
        
        assert "name" in info
        assert "type" in info
        assert "required_vars" in info
        assert "preview" in info
    
    def test_singleton_returns_same_instance(self):
        """Test singleton helper returns same instance."""
        manager1 = get_sms_template_manager()
        manager2 = get_sms_template_manager()
        
        assert manager1 is manager2


class TestSMSService:
    """Tests for SMSService."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        mock.table.return_value.insert.return_value.execute.return_value.data = [{"id": "action-123"}]
        mock.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = None
        return mock
    
    @pytest.fixture
    def mock_provider(self):
        """Create mock SMS provider."""
        with patch('app.services.sms_service.get_vonage_sms_provider') as mock_get:
            provider = MagicMock()
            provider.is_configured.return_value = True
            provider.send_sms = AsyncMock(return_value=SMSResult(
                success=True,
                message_id="msg-123",
                provider="vonage",
                to_number="+1234567890",
                sent_at=datetime.utcnow()
            ))
            mock_get.return_value = provider
            yield provider
    
    @pytest.mark.asyncio
    async def test_send_sms_success(self, mock_supabase, mock_provider):
        """Test successful SMS send."""
        service = SMSService(mock_supabase)
        service._provider = mock_provider
        
        result = await service.send_sms(
            tenant_id="tenant-123",
            to_number="+1234567890",
            message="Test message"
        )
        
        assert result["success"] is True
        assert result["message_id"] == "msg-123"
        assert result["provider"] == "vonage"
    
    @pytest.mark.asyncio
    async def test_send_sms_with_template(self, mock_supabase, mock_provider):
        """Test sending SMS with template."""
        service = SMSService(mock_supabase)
        service._provider = mock_provider
        
        result = await service.send_sms(
            tenant_id="tenant-123",
            to_number="+1234567890",
            message="",
            template_name=SMSTemplateType.MEETING_REMINDER_1H.value,
            template_context={
                "name": "John",
                "title": "Demo",
                "time": "2:00 PM"
            }
        )
        
        assert result["success"] is True
        # Verify provider was called with rendered message
        call_args = mock_provider.send_sms.call_args
        assert "John" in call_args.kwargs["message"]
    
    @pytest.mark.asyncio
    async def test_send_sms_not_configured(self, mock_supabase):
        """Test error when SMS provider not configured."""
        with patch('app.services.sms_service.get_vonage_sms_provider') as mock_get:
            provider = MagicMock()
            provider.is_configured.return_value = False
            mock_get.return_value = provider
            
            service = SMSService(mock_supabase)
            
            with pytest.raises(SMSNotConfiguredError):
                await service.send_sms(
                    tenant_id="tenant-123",
                    to_number="+1234567890",
                    message="Test"
                )
    
    @pytest.mark.asyncio
    async def test_send_meeting_reminder(self, mock_supabase, mock_provider):
        """Test convenience method for meeting reminders."""
        service = SMSService(mock_supabase)
        service._provider = mock_provider
        
        result = await service.send_meeting_reminder(
            tenant_id="tenant-123",
            to_number="+1234567890",
            reminder_type="1h",
            name="Jane",
            title="Strategy Call",
            time="3:00 PM"
        )
        
        assert result["success"] is True
        # Verify rendered message was sent
        call_args = mock_provider.send_sms.call_args
        assert "Jane" in call_args.kwargs["message"]
        assert "1 hour" in call_args.kwargs["message"]


class TestSMSResult:
    """Tests for SMSResult dataclass."""
    
    def test_to_dict(self):
        """Test SMSResult serialization."""
        result = SMSResult(
            success=True,
            message_id="msg-123",
            provider="vonage",
            to_number="+1234567890",
            sent_at=datetime(2026, 1, 9, 12, 0, 0),
            cost=0.0075
        )
        
        data = result.to_dict()
        
        assert data["success"] is True
        assert data["message_id"] == "msg-123"
        assert data["provider"] == "vonage"
        assert data["cost"] == 0.0075
        assert "2026-01-09" in data["sent_at"]
