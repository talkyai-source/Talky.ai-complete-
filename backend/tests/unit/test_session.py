"""
Unit tests for session models (CallSession, CallState, LatencyMetric)
"""
import pytest
from datetime import datetime, timedelta
from app.domain.models.session import CallSession, CallState, LatencyMetric
from app.domain.models.conversation import Message


class TestCallState:
    """Tests for CallState enum"""
    
    def test_call_state_values(self):
        """Test all CallState enum values"""
        assert CallState.CONNECTING == "connecting"
        assert CallState.ACTIVE == "active"
        assert CallState.LISTENING == "listening"
        assert CallState.PROCESSING == "processing"
        assert CallState.SPEAKING == "speaking"
        assert CallState.ENDING == "ending"
        assert CallState.ENDED == "ended"
        assert CallState.ERROR == "error"
    
    def test_call_state_count(self):
        """Test we have all 8 states"""
        assert len(CallState) == 8


class TestLatencyMetric:
    """Tests for LatencyMetric"""
    
    def test_create_latency_metric(self):
        """Test creating a latency metric"""
        metric = LatencyMetric(
            component="stt",
            latency_ms=250.5,
            turn_id=1
        )
        
        assert metric.component == "stt"
        assert metric.latency_ms == 250.5
        assert metric.turn_id == 1
        assert metric.success is True
        assert metric.error_message is None
    
    def test_latency_metric_with_error(self):
        """Test latency metric with error"""
        metric = LatencyMetric(
            component="llm",
            latency_ms=1000.0,
            turn_id=2,
            success=False,
            error_message="Timeout"
        )
        
        assert metric.success is False
        assert metric.error_message == "Timeout"
    
    def test_latency_validation(self):
        """Test latency must be non-negative"""
        with pytest.raises(ValueError):
            LatencyMetric(
                component="tts",
                latency_ms=-10.0,  # Invalid
                turn_id=1
            )
    
    def test_turn_id_validation(self):
        """Test turn_id must be non-negative"""
        with pytest.raises(ValueError):
            LatencyMetric(
                component="stt",
                latency_ms=100.0,
                turn_id=-1  # Invalid
            )


class TestCallSession:
    """Tests for CallSession"""
    
    def test_create_call_session(self):
        """Test creating a call session"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid-123",
            system_prompt="You are a helpful assistant",
            voice_id="voice-1"
        )
        
        assert session.call_id == "test-123"
        assert session.campaign_id == "campaign-1"
        assert session.lead_id == "lead-1"
        assert session.vonage_call_uuid == "vonage-uuid-123"
        assert session.state == CallState.CONNECTING
        assert session.turn_id == 0
        assert session.language == "en"
        assert len(session.conversation_history) == 0
        assert len(session.latency_measurements) == 0
    
    def test_session_with_custom_language(self):
        """Test session with custom language"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1",
            language="es"
        )
        
        assert session.language == "es"
    
    def test_session_with_tenant_id(self):
        """Test session with tenant ID"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1",
            tenant_id="tenant-1"
        )
        
        assert session.tenant_id == "tenant-1"
    
    def test_update_activity(self):
        """Test updating activity timestamp"""
        import time
        
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        original_time = session.last_activity_at
        time.sleep(0.01)  # Small delay to ensure timestamp difference
        session.update_activity()
        
        assert session.last_activity_at >= original_time
    
    def test_add_latency_measurement(self):
        """Test adding latency measurements"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        session.add_latency_measurement("stt", 250.0)
        session.add_latency_measurement("llm", 450.0)
        session.add_latency_measurement("tts", 120.0)
        
        assert len(session.latency_measurements) == 3
        assert session.latency_measurements[0].component == "stt"
        assert session.latency_measurements[1].component == "llm"
        assert session.latency_measurements[2].component == "tts"
    
    def test_is_stale(self):
        """Test stale session detection"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        # Fresh session should not be stale
        assert session.is_stale(timeout_seconds=300) is False
        
        # Manually set old timestamp
        session.last_activity_at = datetime.utcnow() - timedelta(minutes=10)
        
        # Should be stale with 5 minute timeout
        assert session.is_stale(timeout_seconds=300) is True
    
    def test_get_duration_seconds(self):
        """Test getting call duration"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        # Manually set start time to 1 minute ago
        session.started_at = datetime.utcnow() - timedelta(minutes=1)
        
        duration = session.get_duration_seconds()
        assert duration >= 60  # At least 60 seconds
        assert duration < 65   # But not much more
    
    def test_increment_turn(self):
        """Test incrementing turn counter"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        session.current_user_input = "Hello"
        session.current_ai_response = "Hi there"
        
        session.increment_turn()
        
        assert session.turn_id == 1
        assert session.current_user_input == ""
        assert session.current_ai_response == ""
    
    def test_get_average_latency(self):
        """Test calculating average latency"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        # Add measurements
        session.add_latency_measurement("stt", 200.0)
        session.add_latency_measurement("stt", 300.0)
        session.add_latency_measurement("llm", 400.0)
        session.add_latency_measurement("llm", 600.0)
        
        # Average STT latency
        avg_stt = session.get_average_latency("stt")
        assert avg_stt == 250.0  # (200 + 300) / 2
        
        # Average LLM latency
        avg_llm = session.get_average_latency("llm")
        assert avg_llm == 500.0  # (400 + 600) / 2
        
        # Overall average
        avg_all = session.get_average_latency()
        assert avg_all == 375.0  # (200 + 300 + 400 + 600) / 4
    
    def test_get_average_latency_no_measurements(self):
        """Test average latency with no measurements"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        assert session.get_average_latency("stt") == 0.0
        assert session.get_average_latency() == 0.0
    
    def test_session_serialization_to_redis(self):
        """Test serializing session to Redis dict"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test prompt",
            voice_id="voice-1"
        )
        
        # Add some data
        session.conversation_history.append(
            Message(role="user", content="Hello")
        )
        session.add_latency_measurement("stt", 250.0)
        
        redis_dict = session.model_dump_redis()
        
        # Should include serializable fields
        assert "call_id" in redis_dict
        assert "campaign_id" in redis_dict
        assert "conversation_history" in redis_dict
        assert "latency_measurements" in redis_dict
        
        # Should NOT include runtime fields
        assert "websocket" not in redis_dict
        assert "audio_input_buffer" not in redis_dict
        assert "audio_output_buffer" not in redis_dict
        assert "transcript_buffer" not in redis_dict
    
    def test_session_deserialization_from_redis(self):
        """Test deserializing session from Redis dict"""
        # Create original session
        original = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test prompt",
            voice_id="voice-1"
        )
        
        original.turn_id = 5
        original.current_user_input = "Test input"
        
        # Serialize
        redis_dict = original.model_dump_redis()
        
        # Deserialize
        restored = CallSession.from_redis_dict(redis_dict)
        
        # Check fields match
        assert restored.call_id == original.call_id
        assert restored.campaign_id == original.campaign_id
        assert restored.turn_id == original.turn_id
        assert restored.current_user_input == original.current_user_input
        
        # Check runtime fields are recreated
        assert restored.audio_input_buffer is not None
        assert restored.audio_output_buffer is not None
        assert restored.transcript_buffer is not None
    
    def test_streaming_state_flags(self):
        """Test streaming state boolean flags"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        # All should be False initially
        assert session.stt_active is False
        assert session.llm_active is False
        assert session.tts_active is False
        assert session.user_speaking is False
        assert session.ai_speaking is False
        
        # Set flags
        session.stt_active = True
        session.user_speaking = True
        
        assert session.stt_active is True
        assert session.user_speaking is True
    
    def test_conversation_history(self):
        """Test managing conversation history"""
        session = CallSession(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            vonage_call_uuid="vonage-uuid",
            system_prompt="Test",
            voice_id="voice-1"
        )
        
        # Add messages
        session.conversation_history.append(
            Message(role="user", content="Hello")
        )
        session.conversation_history.append(
            Message(role="assistant", content="Hi there!")
        )
        
        assert len(session.conversation_history) == 2
        assert session.conversation_history[0].role == "user"
        assert session.conversation_history[1].role == "assistant"
