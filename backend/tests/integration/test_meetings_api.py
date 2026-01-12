"""
Integration Tests for Meetings API
Day 25: Meeting Booking Feature
"""
import pytest
import os
from datetime import datetime
from cryptography.fernet import Fernet

# Generate a valid Fernet key for testing
TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["CONNECTOR_ENCRYPTION_KEY"] = TEST_FERNET_KEY


class TestMeetingsAPIImports:
    """Test that meetings API components import correctly"""
    
    def test_router_import(self):
        """Meetings router can be imported"""
        from app.api.v1.endpoints.meetings import router
        assert router is not None
    
    def test_router_has_endpoints(self):
        """Router has expected number of endpoints"""
        from app.api.v1.endpoints.meetings import router
        assert len(router.routes) == 6
    
    def test_request_models_import(self):
        """Request models can be imported"""
        from app.api.v1.endpoints.meetings import (
            CreateMeetingRequest,
            UpdateMeetingRequest,
            CancelMeetingRequest
        )
        assert CreateMeetingRequest is not None
        assert UpdateMeetingRequest is not None
        assert CancelMeetingRequest is not None
    
    def test_response_models_import(self):
        """Response models can be imported"""
        from app.api.v1.endpoints.meetings import (
            MeetingResponse,
            CreateMeetingResponse,
            AvailabilitySlot
        )
        assert MeetingResponse is not None
        assert CreateMeetingResponse is not None
        assert AvailabilitySlot is not None


class TestMeetingsAPIRouterRegistration:
    """Test that meetings router is registered in main API"""
    
    def test_meetings_in_main_router(self):
        """Meetings router is included in main API router"""
        from app.api.v1.routes import api_router
        
        # Find meetings routes
        meetings_routes = [
            r for r in api_router.routes 
            if hasattr(r, 'path') and '/meetings' in r.path
        ]
        
        assert len(meetings_routes) > 0


class TestCreateMeetingRequest:
    """Test CreateMeetingRequest model validation"""
    
    def test_valid_request(self):
        """Valid request creates successfully"""
        from app.api.v1.endpoints.meetings import CreateMeetingRequest
        
        request = CreateMeetingRequest(
            title="Test Meeting",
            start_time="2026-01-08T10:00:00",
            duration_minutes=30,
            attendees=["test@example.com"],
            add_video_conference=True
        )
        
        assert request.title == "Test Meeting"
        assert request.duration_minutes == 30
        assert request.add_video_conference is True
    
    def test_default_values(self):
        """Default values are set correctly"""
        from app.api.v1.endpoints.meetings import CreateMeetingRequest
        
        request = CreateMeetingRequest(
            title="Test Meeting",
            start_time="2026-01-08T10:00:00"
        )
        
        assert request.duration_minutes == 30
        assert request.attendees == []
        assert request.add_video_conference is True
        assert request.timezone == "UTC"


class TestAvailabilitySlot:
    """Test AvailabilitySlot model"""
    
    def test_slot_creation(self):
        """Slot can be created with required fields"""
        from app.api.v1.endpoints.meetings import AvailabilitySlot
        
        slot = AvailabilitySlot(
            start="2026-01-08T09:00:00",
            end="2026-01-08T10:30:00",
            duration_minutes=30
        )
        
        assert slot.start == "2026-01-08T09:00:00"
        assert slot.end == "2026-01-08T10:30:00"
        assert slot.duration_minutes == 30


class TestMeetingResponse:
    """Test MeetingResponse model"""
    
    def test_response_with_all_fields(self):
        """Response can be created with all fields"""
        from app.api.v1.endpoints.meetings import MeetingResponse
        
        response = MeetingResponse(
            id="meeting-123",
            title="Test Meeting",
            description="Test description",
            start_time="2026-01-08T10:00:00",
            end_time="2026-01-08T10:30:00",
            timezone="UTC",
            join_link="https://meet.google.com/abc",
            status="scheduled",
            attendees=[{"email": "test@example.com"}],
            lead_id=None,
            created_at="2026-01-07T12:00:00"
        )
        
        assert response.id == "meeting-123"
        assert response.join_link == "https://meet.google.com/abc"


class TestCreateMeetingResponse:
    """Test CreateMeetingResponse model"""
    
    def test_success_response(self):
        """Success response has correct structure"""
        from app.api.v1.endpoints.meetings import CreateMeetingResponse
        
        response = CreateMeetingResponse(
            success=True,
            meeting_id="meeting-123",
            title="Test Meeting",
            join_link="https://meet.google.com/abc",
            provider="google_calendar"
        )
        
        assert response.success is True
        assert response.meeting_id == "meeting-123"
        assert response.calendar_required is False
    
    def test_error_response(self):
        """Error response has correct structure"""
        from app.api.v1.endpoints.meetings import CreateMeetingResponse
        
        response = CreateMeetingResponse(
            success=False,
            error="No calendar connected",
            calendar_required=True
        )
        
        assert response.success is False
        assert response.calendar_required is True
        assert response.meeting_id is None


class TestAssistantMeetingTools:
    """Test that meeting tools are registered in assistant"""
    
    def test_meeting_tools_in_registry(self):
        """Meeting tools are in ALL_TOOLS registry"""
        from app.infrastructure.assistant.tools import ALL_TOOLS
        
        assert "check_availability" in ALL_TOOLS
        assert "book_meeting" in ALL_TOOLS
        assert "update_meeting" in ALL_TOOLS
        assert "cancel_meeting" in ALL_TOOLS
    
    def test_total_tools_count(self):
        """Total tools count is correct"""
        from app.infrastructure.assistant.tools import ALL_TOOLS
        
        # Should have original 10 tools + 4 meeting tools = 14
        assert len(ALL_TOOLS) >= 14
    
    def test_book_meeting_has_input_schema(self):
        """book_meeting tool has input schema"""
        from app.infrastructure.assistant.tools import ACTION_TOOLS
        
        book_meeting_tool = ACTION_TOOLS.get("book_meeting")
        assert book_meeting_tool is not None
        assert book_meeting_tool.get("input_schema") is not None
    
    def test_check_availability_has_input_schema(self):
        """check_availability tool has input schema"""
        from app.infrastructure.assistant.tools import ACTION_TOOLS
        
        check_tool = ACTION_TOOLS.get("check_availability")
        assert check_tool is not None
        assert check_tool.get("input_schema") is not None
