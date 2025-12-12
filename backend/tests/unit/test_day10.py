"""
Day 10 Unit Tests
Tests for RecordingBuffer, RecordingService, TranscriptService, and integration.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Import the classes to test
from app.domain.services.recording_service import RecordingBuffer, RecordingService
from app.domain.services.transcript_service import TranscriptService, TranscriptTurn


class TestRecordingBuffer:
    """Test RecordingBuffer class functionality."""
    
    def test_init_default_sample_rate(self):
        """Test buffer initialization with default 16kHz sample rate."""
        buffer = RecordingBuffer(call_id="test-call-1")
        
        assert buffer.call_id == "test-call-1"
        assert buffer.sample_rate == 16000
        assert buffer.channels == 1
        assert buffer.bit_depth == 16
        assert buffer.total_bytes == 0
        assert len(buffer.chunks) == 0
    
    def test_init_rtp_sample_rate(self):
        """Test buffer initialization with 8kHz sample rate for RTP/G.711."""
        buffer = RecordingBuffer(
            call_id="rtp-call-1",
            sample_rate=8000
        )
        
        assert buffer.sample_rate == 8000
    
    def test_add_chunk(self):
        """Test adding audio chunks to buffer."""
        buffer = RecordingBuffer(call_id="test-call")
        
        chunk1 = b'\x00' * 1000
        chunk2 = b'\x01' * 500
        
        buffer.add_chunk(chunk1)
        assert buffer.total_bytes == 1000
        assert len(buffer.chunks) == 1
        
        buffer.add_chunk(chunk2)
        assert buffer.total_bytes == 1500
        assert len(buffer.chunks) == 2
    
    def test_get_complete_audio(self):
        """Test getting complete audio from buffer."""
        buffer = RecordingBuffer(call_id="test-call")
        
        buffer.add_chunk(b'hello')
        buffer.add_chunk(b'world')
        
        complete = buffer.get_complete_audio()
        assert complete == b'helloworld'
    
    def test_get_duration_seconds_16khz(self):
        """Test duration calculation for 16kHz audio."""
        buffer = RecordingBuffer(call_id="test-call", sample_rate=16000)
        
        # 16000 Hz * 1 channel * 2 bytes = 32000 bytes per second
        buffer.add_chunk(b'\x00' * 32000)
        
        assert buffer.get_duration_seconds() == 1.0
    
    def test_get_duration_seconds_8khz(self):
        """Test duration calculation for 8kHz audio."""
        buffer = RecordingBuffer(call_id="test-call", sample_rate=8000)
        
        # 8000 Hz * 1 channel * 2 bytes = 16000 bytes per second
        buffer.add_chunk(b'\x00' * 16000)
        
        assert buffer.get_duration_seconds() == 1.0
    
    def test_get_wav_bytes(self):
        """Test WAV file generation."""
        buffer = RecordingBuffer(call_id="test-call")
        
        # Add some audio data
        buffer.add_chunk(b'\x00' * 1000)
        
        wav_data = buffer.get_wav_bytes()
        
        # WAV file should start with RIFF header
        assert wav_data[:4] == b'RIFF'
        # Should contain WAVE format
        assert b'WAVE' in wav_data[:12]
    
    def test_clear(self):
        """Test clearing buffer."""
        buffer = RecordingBuffer(call_id="test-call")
        buffer.add_chunk(b'\x00' * 1000)
        
        buffer.clear()
        
        assert buffer.total_bytes == 0
        assert len(buffer.chunks) == 0


class TestTranscriptService:
    """Test TranscriptService functionality."""
    
    def setup_method(self):
        """Clear buffers before each test."""
        TranscriptService.clear_all_buffers()
    
    def test_accumulate_turn(self):
        """Test accumulating conversation turns."""
        service = TranscriptService()
        call_id = "test-call-1"
        
        service.accumulate_turn(call_id, "user", "Hello, I'm calling about my appointment")
        service.accumulate_turn(call_id, "assistant", "Hi! How can I help you today?")
        
        turns = service.get_turns(call_id)
        
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Hello, I'm calling about my appointment"
        assert turns[1].role == "assistant"
    
    def test_skip_empty_content(self):
        """Test that empty content is skipped."""
        service = TranscriptService()
        call_id = "test-call"
        
        service.accumulate_turn(call_id, "user", "")
        service.accumulate_turn(call_id, "user", "   ")
        
        turns = service.get_turns(call_id)
        assert len(turns) == 0
    
    def test_get_transcript_text(self):
        """Test plain text transcript generation."""
        service = TranscriptService()
        call_id = "test-call"
        
        service.accumulate_turn(call_id, "user", "Hello")
        service.accumulate_turn(call_id, "assistant", "Hi there")
        
        text = service.get_transcript_text(call_id)
        
        assert "User: Hello" in text
        assert "Assistant: Hi there" in text
    
    def test_get_transcript_json(self):
        """Test JSON transcript generation."""
        service = TranscriptService()
        call_id = "test-call"
        
        service.accumulate_turn(call_id, "user", "Hello")
        
        json_data = service.get_transcript_json(call_id)
        
        assert len(json_data) == 1
        assert json_data[0]["role"] == "user"
        assert json_data[0]["content"] == "Hello"
        assert "timestamp" in json_data[0]
    
    def test_get_metrics(self):
        """Test transcript metrics calculation."""
        service = TranscriptService()
        call_id = "test-call"
        
        service.accumulate_turn(call_id, "user", "one two three")
        service.accumulate_turn(call_id, "assistant", "four five six seven")
        
        metrics = service.get_metrics(call_id)
        
        assert metrics["turn_count"] == 2
        assert metrics["user_word_count"] == 3
        assert metrics["assistant_word_count"] == 4
        assert metrics["word_count"] == 7
    
    def test_clear_buffer(self):
        """Test clearing transcript buffer."""
        service = TranscriptService()
        call_id = "test-call"
        
        service.accumulate_turn(call_id, "user", "Hello")
        assert len(service.get_turns(call_id)) == 1
        
        service.clear_buffer(call_id)
        assert len(service.get_turns(call_id)) == 0


class TestRecordingService:
    """Test RecordingService functionality."""
    
    @pytest.mark.asyncio
    async def test_generate_storage_path(self):
        """Test storage path generation."""
        mock_supabase = MagicMock()
        service = RecordingService(mock_supabase)
        
        path = service._generate_storage_path(
            call_id="call-123",
            tenant_id="tenant-abc",
            campaign_id="campaign-xyz"
        )
        
        assert path == "tenant-abc/campaign-xyz/call-123.wav"
    
    @pytest.mark.asyncio
    async def test_save_recording_empty_buffer(self):
        """Test that empty buffer returns None."""
        mock_supabase = MagicMock()
        service = RecordingService(mock_supabase)
        
        buffer = RecordingBuffer(call_id="test")
        # Buffer is empty
        
        result = await service.save_recording(
            call_id="test",
            buffer=buffer,
            tenant_id="tenant",
            campaign_id="campaign"
        )
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_save_recording_uploads_to_storage(self):
        """Test recording upload to storage."""
        mock_supabase = MagicMock()
        mock_storage = MagicMock()
        mock_bucket = MagicMock()
        mock_supabase.storage.from_.return_value = mock_bucket
        
        service = RecordingService(mock_supabase)
        
        buffer = RecordingBuffer(call_id="test")
        buffer.add_chunk(b'\x00' * 1000)
        
        result = await service.save_recording(
            call_id="test",
            buffer=buffer,
            tenant_id="tenant",
            campaign_id="campaign"
        )
        
        # Verify upload was called
        mock_bucket.upload.assert_called_once()
        assert result is not None


class TestTranscriptTurn:
    """Test TranscriptTurn dataclass."""
    
    def test_to_dict(self):
        """Test dictionary conversion."""
        turn = TranscriptTurn(
            role="user",
            content="Hello",
            timestamp="2024-01-01T00:00:00",
            confidence=0.95
        )
        
        result = turn.to_dict()
        
        assert result["role"] == "user"
        assert result["content"] == "Hello"
        assert result["timestamp"] == "2024-01-01T00:00:00"
        assert result["confidence"] == 0.95


class TestMediaGatewayIntegration:
    """Test that both gateways implement recording buffer methods."""
    
    def test_vonage_gateway_has_recording_buffer_methods(self):
        """Verify VonageMediaGateway has recording buffer methods."""
        from app.infrastructure.telephony.vonage_media_gateway import VonageMediaGateway
        
        gateway = VonageMediaGateway()
        
        assert hasattr(gateway, 'get_recording_buffer')
        assert hasattr(gateway, 'clear_recording_buffer')
        assert hasattr(gateway, '_recording_buffers')
    
    def test_rtp_gateway_has_recording_buffer_methods(self):
        """Verify RTPMediaGateway has recording buffer methods."""
        from app.infrastructure.telephony.rtp_media_gateway import RTPMediaGateway
        
        gateway = RTPMediaGateway()
        
        assert hasattr(gateway, 'get_recording_buffer')
        assert hasattr(gateway, 'clear_recording_buffer')
        assert hasattr(gateway, '_recording_buffers')


# Run with: python -m pytest tests/unit/test_day10.py -v
