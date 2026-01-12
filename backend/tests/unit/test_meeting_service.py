"""
Tests for Meeting Service
Day 25: Meeting Booking Feature
"""
import pytest
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet

# Generate a valid Fernet key for testing
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["CONNECTOR_ENCRYPTION_KEY"] = TEST_FERNET_KEY


class TestMeetingServiceImports:
    """Test that all meeting service components import correctly"""
    
    def test_meeting_service_import(self):
        """MeetingService can be imported"""
        from app.services.meeting_service import MeetingService
        assert MeetingService is not None
    
    def test_calendar_not_connected_error_import(self):
        """CalendarNotConnectedError can be imported"""
        from app.services.meeting_service import CalendarNotConnectedError
        assert CalendarNotConnectedError is not None
    
    def test_get_meeting_service_import(self):
        """get_meeting_service helper can be imported"""
        from app.services.meeting_service import get_meeting_service
        assert get_meeting_service is not None


class TestCalendarNotConnectedError:
    """Tests for CalendarNotConnectedError exception"""
    
    def test_default_message(self):
        """Exception has user-friendly default message"""
        from app.services.meeting_service import CalendarNotConnectedError
        
        error = CalendarNotConnectedError()
        assert "No calendar connected" in error.message
        assert "Google Calendar" in error.message or "Outlook" in error.message
    
    def test_custom_message(self):
        """Exception accepts custom message"""
        from app.services.meeting_service import CalendarNotConnectedError
        
        error = CalendarNotConnectedError("Custom error message")
        assert error.message == "Custom error message"


class TestMeetingServiceInit:
    """Tests for MeetingService initialization"""
    
    def test_init_with_supabase(self):
        """MeetingService initializes with supabase client"""
        from app.services.meeting_service import MeetingService
        
        mock_supabase = MagicMock()
        service = MeetingService(mock_supabase)
        
        assert service.supabase == mock_supabase
    
    def test_encryption_service_initialized(self):
        """Encryption service is initialized"""
        from app.services.meeting_service import MeetingService
        
        mock_supabase = MagicMock()
        service = MeetingService(mock_supabase)
        
        assert service._encryption is not None


class TestGetActiveCalendarConnector:
    """Tests for _get_active_calendar_connector"""
    
    @pytest.mark.asyncio
    async def test_no_calendar_connected_raises(self):
        """Raises CalendarNotConnectedError when no calendar exists"""
        from app.services.meeting_service import MeetingService, CalendarNotConnectedError
        
        mock_supabase = MagicMock()
        # Mock the chained calls for finding connectors
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_execute = MagicMock()
        mock_eq3.execute.return_value = mock_execute
        mock_execute.data = []
        
        service = MeetingService(mock_supabase)
        
        with pytest.raises(CalendarNotConnectedError) as exc_info:
            await service._get_active_calendar_connector("tenant-123")
        
        assert "No calendar connected" in str(exc_info.value)


class TestCreateMeeting:
    """Tests for create_meeting method"""
    
    @pytest.mark.asyncio
    async def test_create_meeting_no_connector_raises(self):
        """Raises error when no calendar is connected"""
        from app.services.meeting_service import MeetingService, CalendarNotConnectedError
        
        mock_supabase = MagicMock()
        # Mock the chained calls for finding connectors - return empty
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_eq3 = MagicMock()
        mock_eq2.eq.return_value = mock_eq3
        mock_execute = MagicMock()
        mock_eq3.execute.return_value = mock_execute
        mock_execute.data = []
        
        service = MeetingService(mock_supabase)
        
        with pytest.raises(CalendarNotConnectedError):
            await service.create_meeting(
                tenant_id="tenant-123",
                title="Test Meeting",
                start_time=datetime.utcnow() + timedelta(days=1),
                duration_minutes=30,
                attendees=["test@example.com"]
            )


class TestGetMeeting:
    """Tests for get_meeting method"""
    
    @pytest.mark.asyncio
    async def test_get_meeting_returns_data(self):
        """get_meeting returns meeting data"""
        from app.services.meeting_service import MeetingService
        
        mock_meeting = {
            "id": "meeting-123",
            "title": "Test Meeting",
            "start_time": "2026-01-08T10:00:00",
            "status": "scheduled"
        }
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_single = MagicMock()
        mock_eq2.single.return_value = mock_single
        mock_execute = MagicMock()
        mock_single.execute.return_value = mock_execute
        mock_execute.data = mock_meeting
        
        service = MeetingService(mock_supabase)
        result = await service.get_meeting("tenant-123", "meeting-123")
        
        assert result["id"] == "meeting-123"
        assert result["title"] == "Test Meeting"
    
    @pytest.mark.asyncio
    async def test_get_meeting_not_found(self):
        """get_meeting returns None for non-existent meeting"""
        from app.services.meeting_service import MeetingService
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_single = MagicMock()
        mock_eq2.single.return_value = mock_single
        mock_execute = MagicMock()
        mock_single.execute.return_value = mock_execute
        mock_execute.data = None
        
        service = MeetingService(mock_supabase)
        result = await service.get_meeting("tenant-123", "nonexistent")
        
        assert result is None


class TestListMeetings:
    """Tests for list_meetings method"""
    
    @pytest.mark.asyncio
    async def test_list_meetings_returns_array(self):
        """list_meetings returns list of meetings"""
        from app.services.meeting_service import MeetingService
        
        mock_meetings = [
            {"id": "meeting-1", "title": "Meeting 1"},
            {"id": "meeting-2", "title": "Meeting 2"}
        ]
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_order = MagicMock()
        mock_eq.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_execute = MagicMock()
        mock_limit.execute.return_value = mock_execute
        mock_execute.data = mock_meetings
        
        service = MeetingService(mock_supabase)
        result = await service.list_meetings("tenant-123")
        
        assert len(result) == 2
        assert result[0]["id"] == "meeting-1"
    
    @pytest.mark.asyncio
    async def test_list_meetings_empty(self):
        """list_meetings returns empty list when no meetings"""
        from app.services.meeting_service import MeetingService
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq = MagicMock()
        mock_select.eq.return_value = mock_eq
        mock_order = MagicMock()
        mock_eq.order.return_value = mock_order
        mock_limit = MagicMock()
        mock_order.limit.return_value = mock_limit
        mock_execute = MagicMock()
        mock_limit.execute.return_value = mock_execute
        mock_execute.data = []
        
        service = MeetingService(mock_supabase)
        result = await service.list_meetings("tenant-123")
        
        assert result == []


class TestCancelMeeting:
    """Tests for cancel_meeting method"""
    
    @pytest.mark.asyncio
    async def test_cancel_meeting_not_found(self):
        """cancel_meeting returns error for non-existent meeting"""
        from app.services.meeting_service import MeetingService
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_supabase.table.return_value = mock_table
        mock_select = MagicMock()
        mock_table.select.return_value = mock_select
        mock_eq1 = MagicMock()
        mock_select.eq.return_value = mock_eq1
        mock_eq2 = MagicMock()
        mock_eq1.eq.return_value = mock_eq2
        mock_single = MagicMock()
        mock_eq2.single.return_value = mock_single
        mock_execute = MagicMock()
        mock_single.execute.return_value = mock_execute
        mock_execute.data = None
        
        service = MeetingService(mock_supabase)
        result = await service.cancel_meeting("tenant-123", "nonexistent")
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()
