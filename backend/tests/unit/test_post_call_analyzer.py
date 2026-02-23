"""
Unit tests for PostCallAnalyzer service.

Day 29: Voice AI Intent Detection & Actions
Tests pattern-based intent detection, API availability checks,
permission verification, and action execution.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from app.domain.services.post_call_analyzer import PostCallAnalyzer
from app.domain.models.voice_intent import (
    VoiceActionableIntent,
    ActionReadiness,
    DetectedIntent
)


class TestIntentDetection:
    """Tests for pattern-based intent detection."""
    
    @pytest.fixture
    def analyzer(self):
        """Create analyzer with mocked supabase."""
        mock_supabase = MagicMock()
        return PostCallAnalyzer(mock_supabase)
    
    # === Booking Request Tests ===
    
    def test_detect_booking_explicit(self, analyzer):
        """Explicit booking request detected with high confidence."""
        transcript = "User: Can we schedule a meeting for tomorrow?\nAssistant: Sure!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.BOOKING_REQUEST
        assert confidence >= 0.7
    
    def test_detect_booking_with_time(self, analyzer):
        """Booking with time reference extracts time data."""
        transcript = "User: Let's book a call for 2pm tomorrow\nAssistant: Great!"
        intent, confidence, extracted = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.BOOKING_REQUEST
        assert confidence >= 0.8
        assert "time_reference" in extracted
    
    def test_detect_booking_confirmation(self, analyzer):
        """Confirmation language detected as booking."""
        transcript = "User: Yes, that time works for me\nAssistant: Perfect!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.BOOKING_REQUEST
        assert confidence >= 0.7
    
    def test_detect_booking_demo_request(self, analyzer):
        """Demo scheduling detected as booking."""
        transcript = "User: I'd like to set up a demo\nAssistant: Absolutely!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.BOOKING_REQUEST
    
    def test_detect_booking_appointment(self, analyzer):
        """Appointment request detected."""
        transcript = "User: Can we arrange an appointment?\nAssistant: Of course!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.BOOKING_REQUEST
    
    # === Follow-up Request Tests ===
    
    def test_detect_followup_explicit(self, analyzer):
        """Explicit follow-up request detected."""
        transcript = "User: Can you send me more information?\nAssistant: Sure!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.FOLLOW_UP_REQUEST
        assert confidence >= 0.7
    
    def test_detect_followup_email(self, analyzer):
        """Email request detected as follow-up."""
        transcript = "User: Could you email me the details?\nAssistant: Of course!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.FOLLOW_UP_REQUEST
    
    def test_detect_followup_pricing(self, analyzer):
        """Pricing request detected as follow-up."""
        transcript = "User: I need to see the pricing documentation\nAssistant: Sure thing!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.FOLLOW_UP_REQUEST
    
    # === Reminder Request Tests ===
    
    def test_detect_reminder_explicit(self, analyzer):
        """Explicit reminder request detected."""
        transcript = "User: Please remind me about this\nAssistant: I'll set a reminder!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.REMINDER_REQUEST
    
    def test_detect_reminder_implicit(self, analyzer):
        """Implicit reminder request detected."""
        transcript = "User: Don't let me forget to follow up\nAssistant: Noted!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.REMINDER_REQUEST
    
    # === Callback Request Tests ===
    
    def test_detect_callback_explicit(self, analyzer):
        """Explicit callback request detected."""
        transcript = "User: Can you call me back later?\nAssistant: I'll do that!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.CALLBACK_LATER
    
    def test_detect_callback_busy(self, analyzer):
        """Busy indicator detected as callback."""
        transcript = "User: I'm busy now, try again tomorrow\nAssistant: No problem!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.CALLBACK_LATER
    
    def test_detect_callback_not_good_time(self, analyzer):
        """Not good time detected as callback."""
        transcript = "User: Not a good time right now\nAssistant: I understand!"
        intent, confidence, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.CALLBACK_LATER
    
    # === No Intent Tests ===
    
    def test_detect_none_generic(self, analyzer):
        """Generic conversation has no actionable intent."""
        transcript = "User: That sounds interesting\nAssistant: Great!"
        intent, _, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.NONE
    
    def test_detect_none_question(self, analyzer):
        """Question without action has no intent."""
        transcript = "User: How long have you been in business?\nAssistant: 10 years!"
        intent, _, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.NONE
    
    def test_detect_none_empty(self, analyzer):
        """Empty input has no intent."""
        intent, confidence, _ = analyzer._detect_intent("")
        
        assert intent == VoiceActionableIntent.NONE
        assert confidence == 0.0
    
    def test_detect_none_objection(self, analyzer):
        """Objection without callback has no actionable intent."""
        transcript = "User: I'm not interested\nAssistant: I understand."
        intent, _, _ = analyzer._detect_intent(transcript)
        
        assert intent == VoiceActionableIntent.NONE


class TestDataExtraction:
    """Tests for data extraction from transcripts."""
    
    @pytest.fixture
    def analyzer(self):
        mock_supabase = MagicMock()
        return PostCallAnalyzer(mock_supabase)
    
    def test_extract_time_specific(self, analyzer):
        """Specific time extracted from booking request."""
        data = analyzer._extract_booking_data("Let's meet at 3pm tomorrow")
        
        assert "time_reference" in data
        assert "3pm" in data["time_reference"].lower() or "tomorrow" in data["time_reference"].lower()
    
    def test_extract_time_relative(self, analyzer):
        """Relative time extracted from booking request."""
        data = analyzer._extract_booking_data("How about tomorrow morning?")
        
        assert "time_reference" in data
    
    def test_extract_callback_time(self, analyzer):
        """Callback time extracted."""
        data = analyzer._extract_callback_data("Call me back later today")
        
        assert "callback_time" in data


class TestRecommendationMessages:
    """Tests for recommendation message generation."""
    
    @pytest.fixture
    def analyzer(self):
        mock_supabase = MagicMock()
        return PostCallAnalyzer(mock_supabase)
    
    def test_recommendation_missing_calendar(self, analyzer):
        """Recommendation for missing calendar connector."""
        message = analyzer._build_recommendation_message(
            VoiceActionableIntent.BOOKING_REQUEST,
            api_missing="calendar"
        )
        
        assert "calendar" in message.lower()
        assert "connect" in message.lower()
    
    def test_recommendation_missing_email(self, analyzer):
        """Recommendation for missing email connector."""
        message = analyzer._build_recommendation_message(
            VoiceActionableIntent.FOLLOW_UP_REQUEST,
            api_missing="email"
        )
        
        assert "email" in message.lower()
        assert "connect" in message.lower()
    
    def test_recommendation_needs_permission(self, analyzer):
        """Recommendation for needing permission."""
        message = analyzer._build_recommendation_message(
            VoiceActionableIntent.BOOKING_REQUEST,
            needs_permission=True
        )
        
        assert "auto-actions" in message.lower() or "enable" in message.lower()


class TestActionPlanBuilding:
    """Tests for action plan generation."""
    
    @pytest.fixture
    def analyzer(self):
        mock_supabase = MagicMock()
        return PostCallAnalyzer(mock_supabase)
    
    def test_booking_plan_has_three_steps(self, analyzer):
        """Booking plan includes meeting, email, and reminder."""
        plan = analyzer._build_action_plan(
            VoiceActionableIntent.BOOKING_REQUEST,
            {"time_reference": "2pm"},
            "lead-123"
        )
        
        assert len(plan) == 3
        assert plan[0]["type"] == "book_meeting"
        assert plan[1]["type"] == "send_email"
        assert plan[2]["type"] == "schedule_reminder"
    
    def test_followup_plan_has_email(self, analyzer):
        """Follow-up plan includes email only."""
        plan = analyzer._build_action_plan(
            VoiceActionableIntent.FOLLOW_UP_REQUEST,
            {},
            "lead-123"
        )
        
        assert len(plan) == 1
        assert plan[0]["type"] == "send_email"
    
    def test_callback_plan_has_reminder(self, analyzer):
        """Callback plan includes reminder."""
        plan = analyzer._build_action_plan(
            VoiceActionableIntent.CALLBACK_LATER,
            {"callback_time": "tomorrow"},
            "lead-123"
        )
        
        assert len(plan) == 1
        assert plan[0]["type"] == "schedule_reminder"


class TestAPIAvailability:
    """Tests for API availability checking."""
    
    @pytest.mark.asyncio
    async def test_check_api_no_requirement(self):
        """No connector required returns True."""
        mock_supabase = MagicMock()
        analyzer = PostCallAnalyzer(mock_supabase)
        
        result = await analyzer._check_api_available("tenant-123", None)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_api_connector_active(self):
        """Active connector returns True."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"id": "conn-123", "status": "active"}
        ]
        
        analyzer = PostCallAnalyzer(mock_supabase)
        result = await analyzer._check_api_available("tenant-123", "calendar")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_api_connector_missing(self):
        """Missing connector returns False."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        analyzer = PostCallAnalyzer(mock_supabase)
        result = await analyzer._check_api_available("tenant-123", "calendar")
        
        assert result is False


class TestPermissionChecking:
    """Tests for auto-action permission checking."""
    
    @pytest.mark.asyncio
    async def test_permission_enabled(self):
        """Permission enabled returns True."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"auto_actions_enabled": True}
        ]
        
        analyzer = PostCallAnalyzer(mock_supabase)
        result = await analyzer._check_auto_action_permission("tenant-123")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_permission_disabled(self):
        """Permission disabled returns False."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"auto_actions_enabled": False}
        ]
        
        analyzer = PostCallAnalyzer(mock_supabase)
        result = await analyzer._check_auto_action_permission("tenant-123")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_permission_no_settings(self):
        """No settings defaults to False."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        analyzer = PostCallAnalyzer(mock_supabase)
        result = await analyzer._check_auto_action_permission("tenant-123")
        
        assert result is False


class TestFullAnalysisFlow:
    """Integration tests for full analysis flow."""
    
    @pytest.mark.asyncio
    async def test_analyze_booking_ready(self):
        """Full flow: booking detected, APIs ready, permission granted."""
        mock_supabase = MagicMock()
        
        # Mock connector check - calendar active
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"id": "conn-123", "status": "active"}
        ]
        # Mock permission check - enabled
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"auto_actions_enabled": True}
        ]
        # Mock update
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        
        analyzer = PostCallAnalyzer(mock_supabase)
        
        with patch.object(analyzer, '_execute_action', new_callable=AsyncMock) as mock_exec:
            result = await analyzer.analyze_call(
                call_id="call-123",
                tenant_id="tenant-456",
                transcript_text="User: Let's schedule a meeting for tomorrow at 2pm\nAssistant: Great!",
                lead_id="lead-789"
            )
        
        assert result is not None
        assert result.intent == VoiceActionableIntent.BOOKING_REQUEST
        assert result.readiness == ActionReadiness.READY
        mock_exec.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_analyze_booking_missing_api(self):
        """Full flow: booking detected but calendar not connected."""
        mock_supabase = MagicMock()
        
        # Mock connector check - not found
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        # Mock permission check
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"auto_actions_enabled": True}
        ]
        # Mock update
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        
        analyzer = PostCallAnalyzer(mock_supabase)
        
        result = await analyzer.analyze_call(
            call_id="call-123",
            tenant_id="tenant-456",
            transcript_text="User: Let's book a demo\nAssistant: Sure!",
            lead_id="lead-789"
        )
        
        assert result is not None
        assert result.intent == VoiceActionableIntent.BOOKING_REQUEST
        assert result.readiness == ActionReadiness.MISSING_API
        assert "calendar" in result.recommendation_message.lower()
    
    @pytest.mark.asyncio
    async def test_analyze_no_intent(self):
        """Full flow: no actionable intent detected."""
        mock_supabase = MagicMock()
        analyzer = PostCallAnalyzer(mock_supabase)
        
        result = await analyzer.analyze_call(
            call_id="call-123",
            tenant_id="tenant-456",
            transcript_text="User: That sounds interesting!\nAssistant: Great!",
            lead_id="lead-789"
        )
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_analyze_empty_transcript(self):
        """Full flow: empty transcript returns None."""
        mock_supabase = MagicMock()
        analyzer = PostCallAnalyzer(mock_supabase)
        
        result = await analyzer.analyze_call(
            call_id="call-123",
            tenant_id="tenant-456",
            transcript_text="",
            lead_id="lead-789"
        )
        
        assert result is None
