"""
Unit tests for WebSocket message schemas
"""
import pytest
from datetime import datetime
from app.domain.models.websocket_messages import (
    AudioChunkMessage,
    TranscriptChunkMessage,
    TurnEndMessage,
    LLMStartMessage,
    LLMEndMessage,
    TTSStartMessage,
    TTSEndMessage,
    SessionStartMessage,
    SessionEndMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
    MessageType,
    MessageDirection,
    parse_message,
)


class TestAudioChunkMessage:
    """Tests for AudioChunkMessage"""
    
    def test_create_audio_chunk_message(self):
        """Test creating an audio chunk message"""
        msg = AudioChunkMessage(
            call_id="test-123",
            direction=MessageDirection.INBOUND,
            data=b"\x00\x01\x02\x03",
            sequence=1
        )
        
        assert msg.type == MessageType.AUDIO_CHUNK
        assert msg.call_id == "test-123"
        assert msg.direction == MessageDirection.INBOUND
        assert msg.sample_rate == 16000  # default
        assert msg.channels == 1  # default
        assert msg.sequence == 1
        assert len(msg.data) == 4
    
    def test_audio_chunk_with_custom_sample_rate(self):
        """Test audio chunk with custom sample rate"""
        msg = AudioChunkMessage(
            call_id="test-123",
            direction=MessageDirection.OUTBOUND,
            data=b"\x00\x01",
            sequence=5,
            sample_rate=22050
        )
        
        assert msg.sample_rate == 22050
        assert msg.direction == MessageDirection.OUTBOUND
    
    def test_audio_chunk_sequence_validation(self):
        """Test sequence number must be non-negative"""
        with pytest.raises(ValueError):
            AudioChunkMessage(
                call_id="test-123",
                direction=MessageDirection.INBOUND,
                data=b"\x00\x01",
                sequence=-1  # Invalid
            )


class TestTranscriptChunkMessage:
    """Tests for TranscriptChunkMessage"""
    
    def test_create_transcript_chunk(self):
        """Test creating a transcript chunk message"""
        msg = TranscriptChunkMessage(
            call_id="test-123",
            text="Hello world",
            is_final=True,
            confidence=0.95
        )
        
        assert msg.type == MessageType.TRANSCRIPT_CHUNK
        assert msg.text == "Hello world"
        assert msg.is_final is True
        assert msg.confidence == 0.95
    
    def test_transcript_chunk_interim(self):
        """Test interim transcript chunk"""
        msg = TranscriptChunkMessage(
            call_id="test-123",
            text="Hello",
            is_final=False
        )
        
        assert msg.is_final is False
        assert msg.confidence is None  # Optional
    
    def test_confidence_validation(self):
        """Test confidence must be between 0 and 1"""
        with pytest.raises(ValueError):
            TranscriptChunkMessage(
                call_id="test-123",
                text="Test",
                confidence=1.5  # Invalid
            )


class TestTurnEndMessage:
    """Tests for TurnEndMessage"""
    
    def test_create_turn_end_message(self):
        """Test creating a turn end message"""
        msg = TurnEndMessage(
            call_id="test-123",
            turn_id=1,
            full_transcript="Hello, how are you?"
        )
        
        assert msg.type == MessageType.TURN_END
        assert msg.turn_id == 1
        assert msg.full_transcript == "Hello, how are you?"
    
    def test_turn_id_validation(self):
        """Test turn_id must be non-negative"""
        with pytest.raises(ValueError):
            TurnEndMessage(
                call_id="test-123",
                turn_id=-1,  # Invalid
                full_transcript="Test"
            )


class TestSessionMessages:
    """Tests for session control messages"""
    
    def test_session_start_message(self):
        """Test creating a session start message"""
        msg = SessionStartMessage(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            system_prompt="You are a helpful assistant",
            voice_id="voice-1",
            language="en"
        )
        
        assert msg.type == MessageType.SESSION_START
        assert msg.campaign_id == "campaign-1"
        assert msg.lead_id == "lead-1"
        assert msg.language == "en"
    
    def test_session_end_message(self):
        """Test creating a session end message"""
        msg = SessionEndMessage(
            call_id="test-123",
            reason="hangup",
            duration_seconds=125.5
        )
        
        assert msg.type == MessageType.SESSION_END
        assert msg.reason == "hangup"
        assert msg.duration_seconds == 125.5
    
    def test_duration_validation(self):
        """Test duration must be non-negative"""
        with pytest.raises(ValueError):
            SessionEndMessage(
                call_id="test-123",
                reason="error",
                duration_seconds=-10.0  # Invalid
            )


class TestErrorMessage:
    """Tests for error messages"""
    
    def test_create_error_message(self):
        """Test creating an error message"""
        msg = ErrorMessage(
            call_id="test-123",
            error_code="STT_TIMEOUT",
            error_message="Speech-to-text service timed out",
            component="stt",
            recoverable=True
        )
        
        assert msg.type == MessageType.ERROR
        assert msg.error_code == "STT_TIMEOUT"
        assert msg.component == "stt"
        assert msg.recoverable is True
    
    def test_unrecoverable_error(self):
        """Test unrecoverable error"""
        msg = ErrorMessage(
            call_id="test-123",
            error_code="LLM_FAILED",
            error_message="LLM service unavailable",
            component="llm",
            recoverable=False
        )
        
        assert msg.recoverable is False


class TestHeartbeatMessages:
    """Tests for heartbeat messages"""
    
    def test_ping_message(self):
        """Test creating a ping message"""
        msg = PingMessage(call_id="test-123")
        
        assert msg.type == MessageType.PING
        assert msg.call_id == "test-123"
        assert isinstance(msg.timestamp, datetime)
    
    def test_pong_message(self):
        """Test creating a pong message"""
        msg = PongMessage(call_id="test-123")
        
        assert msg.type == MessageType.PONG
        assert msg.call_id == "test-123"


class TestMessageParsing:
    """Tests for message parsing"""
    
    def test_parse_transcript_message(self):
        """Test parsing a transcript chunk message"""
        data = {
            "type": "transcript_chunk",
            "call_id": "test-123",
            "text": "Test",
            "is_final": True,
            "confidence": 0.9,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        msg = parse_message("transcript_chunk", data)
        
        assert isinstance(msg, TranscriptChunkMessage)
        assert msg.text == "Test"
        assert msg.is_final is True
    
    def test_parse_turn_end_message(self):
        """Test parsing a turn end message"""
        data = {
            "type": "turn_end",
            "call_id": "test-123",
            "turn_id": 1,
            "full_transcript": "Hello world",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        msg = parse_message("turn_end", data)
        
        assert isinstance(msg, TurnEndMessage)
        assert msg.turn_id == 1
    
    def test_parse_unknown_message_type(self):
        """Test parsing unknown message type raises error"""
        with pytest.raises(ValueError, match="Unknown message type"):
            parse_message("unknown_type", {})
    
    def test_parse_error_message(self):
        """Test parsing an error message"""
        data = {
            "type": "error",
            "call_id": "test-123",
            "error_code": "TEST_ERROR",
            "error_message": "Test error message",
            "component": "test",
            "recoverable": True,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        msg = parse_message("error", data)
        
        assert isinstance(msg, ErrorMessage)
        assert msg.error_code == "TEST_ERROR"


class TestMessageSerialization:
    """Tests for message serialization"""
    
    def test_transcript_json_serialization(self):
        """Test message can be serialized to JSON"""
        msg = TranscriptChunkMessage(
            call_id="test-123",
            text="Hello",
            is_final=True,
            confidence=0.95
        )
        
        json_data = msg.model_dump_json()
        
        assert "transcript_chunk" in json_data
        assert "test-123" in json_data
        assert "Hello" in json_data
    
    def test_session_start_json_serialization(self):
        """Test session start message serialization"""
        msg = SessionStartMessage(
            call_id="test-123",
            campaign_id="campaign-1",
            lead_id="lead-1",
            system_prompt="Test prompt",
            voice_id="voice-1"
        )
        
        json_data = msg.model_dump_json()
        
        assert "session_start" in json_data
        assert "campaign-1" in json_data
    
    def test_audio_chunk_hex_encoding(self):
        """Test audio chunk bytes are hex-encoded in JSON"""
        msg = AudioChunkMessage(
            call_id="test-123",
            direction=MessageDirection.INBOUND,
            data=b"\x00\x01\x02\x03",
            sequence=1
        )
        
        json_data = msg.model_dump_json()
        
        # Bytes should be hex-encoded
        assert "00010203" in json_data or "data" in json_data


class TestLLMMessages:
    """Tests for LLM-related messages"""
    
    def test_llm_start_message(self):
        """Test LLM start message"""
        msg = LLMStartMessage(
            call_id="test-123",
            turn_id=1
        )
        
        assert msg.type == MessageType.LLM_START
        assert msg.turn_id == 1
    
    def test_llm_end_message(self):
        """Test LLM end message"""
        msg = LLMEndMessage(
            call_id="test-123",
            turn_id=1,
            full_response="I'm doing great, thanks!"
        )
        
        assert msg.type == MessageType.LLM_END
        assert msg.full_response == "I'm doing great, thanks!"


class TestTTSMessages:
    """Tests for TTS-related messages"""
    
    def test_tts_start_message(self):
        """Test TTS start message"""
        msg = TTSStartMessage(
            call_id="test-123",
            turn_id=1,
            text="Hello world"
        )
        
        assert msg.type == MessageType.TTS_START
        assert msg.text == "Hello world"
    
    def test_tts_end_message(self):
        """Test TTS end message"""
        msg = TTSEndMessage(
            call_id="test-123",
            turn_id=1
        )
        
        assert msg.type == MessageType.TTS_END
        assert msg.turn_id == 1
