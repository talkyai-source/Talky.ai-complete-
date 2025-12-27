"""
Unit tests for SIP Media Gateway
Tests audio conversion, session management, and MediaGateway interface.

Day 18: MicroSIP integration testing
"""
import pytest
import asyncio
import audioop
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.infrastructure.telephony.sip_media_gateway import (
    SIPMediaGateway,
    SIPSession
)


class TestSIPSession:
    """Test SIPSession dataclass"""
    
    def test_session_creation(self):
        """Test creating a SIP session"""
        session = SIPSession(
            call_id="test-call-123",
            remote_addr=("192.168.1.100", 5060),
            rtp_port=10000
        )
        
        assert session.call_id == "test-call-123"
        assert session.remote_addr == ("192.168.1.100", 5060)
        assert session.rtp_port == 10000
        assert session.codec == "PCMU"  # Default
        assert session.is_active is True
        assert session.chunks_received == 0
        assert session.chunks_sent == 0
    
    def test_session_with_custom_codec(self):
        """Test session with PCMA codec"""
        session = SIPSession(
            call_id="test-call-456",
            remote_addr=("127.0.0.1", 5060),
            rtp_port=10002,
            codec="PCMA"
        )
        
        assert session.codec == "PCMA"
    
    def test_session_has_audio_queue(self):
        """Test session has audio queue for STT"""
        session = SIPSession(
            call_id="test-call-789",
            remote_addr=("127.0.0.1", 5060),
            rtp_port=10004
        )
        
        assert session.audio_queue is not None
        assert hasattr(session.audio_queue, 'put_nowait')
        assert hasattr(session.audio_queue, 'get')


class TestSIPMediaGateway:
    """Test SIPMediaGateway class"""
    
    @pytest.fixture
    def gateway(self):
        """Create a SIP media gateway instance"""
        return SIPMediaGateway()
    
    @pytest.mark.asyncio
    async def test_initialize(self, gateway):
        """Test gateway initialization"""
        config = {
            "audio": {
                "input_sample_rate": 8000,
                "output_sample_rate": 16000
            }
        }
        
        await gateway.initialize(config)
        
        assert gateway._initialized is True
        assert gateway._config == config
    
    @pytest.mark.asyncio
    async def test_on_call_started(self, gateway):
        """Test starting a SIP call"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-call-001",
            metadata={
                "remote_addr": ("192.168.1.50", 5060),
                "rtp_port": 10000,
                "codec": "PCMU"
            }
        )
        
        session = gateway.get_session("test-call-001")
        
        assert session is not None
        assert session.call_id == "test-call-001"
        assert session.remote_addr == ("192.168.1.50", 5060)
        assert session.rtp_port == 10000
        assert session.codec == "PCMU"
        assert session.is_active is True
    
    @pytest.mark.asyncio
    async def test_on_call_ended(self, gateway):
        """Test ending a SIP call"""
        await gateway.initialize({})
        
        # Start a call
        await gateway.on_call_started(
            call_id="test-call-002",
            metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10002}
        )
        
        # End the call
        await gateway.on_call_ended("test-call-002", "user_hangup")
        
        # Session should be removed
        session = gateway.get_session("test-call-002")
        assert session is None
    
    @pytest.mark.asyncio
    async def test_audio_conversion_ulaw_to_pcm(self, gateway):
        """Test G.711 μ-law to PCM conversion"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-audio-001",
            metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10004}
        )
        
        # Create sample μ-law audio (160 bytes = 20ms at 8kHz)
        # μ-law silence is 0xFF
        ulaw_audio = bytes([0xFF] * 160)
        
        await gateway.on_audio_received("test-audio-001", ulaw_audio)
        
        session = gateway.get_session("test-audio-001")
        
        assert session.chunks_received == 1
        assert session.total_bytes_received == 160
        
        # Audio queue should have processed data
        assert not session.audio_queue.empty()
    
    @pytest.mark.asyncio
    async def test_audio_queue_retrieval(self, gateway):
        """Test getting audio queue for STT pipeline"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-queue-001",
            metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10006}
        )
        
        queue = gateway.get_audio_queue("test-queue-001")
        
        assert queue is not None
        
        # Non-existent call
        queue_none = gateway.get_audio_queue("non-existent")
        assert queue_none is None
    
    @pytest.mark.asyncio
    async def test_recording_buffer(self, gateway):
        """Test recording buffer accumulation"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-record-001",
            metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10008}
        )
        
        # Send multiple audio chunks
        ulaw_audio = bytes([0xFF] * 160)
        for _ in range(5):
            await gateway.on_audio_received("test-record-001", ulaw_audio)
        
        buffer = gateway.get_recording_buffer("test-record-001")
        
        assert buffer is not None
        assert len(buffer) == 5
        
        # Clear buffer
        gateway.clear_recording_buffer("test-record-001")
        buffer_after = gateway.get_recording_buffer("test-record-001")
        assert len(buffer_after) == 0
    
    @pytest.mark.asyncio
    async def test_session_metrics(self, gateway):
        """Test session metrics retrieval"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-metrics-001",
            metadata={
                "remote_addr": ("10.0.0.1", 5060),
                "rtp_port": 10010,
                "codec": "PCMA"
            }
        )
        
        # Send some audio
        ulaw_audio = bytes([0xFF] * 160)
        await gateway.on_audio_received("test-metrics-001", ulaw_audio)
        
        metrics = gateway.get_session_metrics("test-metrics-001")
        
        assert metrics is not None
        assert metrics["call_id"] == "test-metrics-001"
        assert metrics["remote_addr"] == "10.0.0.1:5060"
        assert metrics["codec"] == "PCMA"
        assert metrics["chunks_received"] == 1
        assert metrics["is_active"] is True
    
    @pytest.mark.asyncio
    async def test_is_session_active(self, gateway):
        """Test session active check"""
        await gateway.initialize({})
        
        await gateway.on_call_started(
            call_id="test-active-001",
            metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10012}
        )
        
        assert gateway.is_session_active("test-active-001") is True
        assert gateway.is_session_active("non-existent") is False
        
        await gateway.on_call_ended("test-active-001", "bye")
        assert gateway.is_session_active("test-active-001") is False
    
    @pytest.mark.asyncio
    async def test_gateway_name(self, gateway):
        """Test gateway name property"""
        assert gateway.name == "sip"
    
    @pytest.mark.asyncio
    async def test_cleanup(self, gateway):
        """Test cleanup of all sessions"""
        await gateway.initialize({})
        
        # Start multiple calls
        for i in range(3):
            await gateway.on_call_started(
                call_id=f"test-cleanup-{i}",
                metadata={"remote_addr": ("127.0.0.1", 5060), "rtp_port": 10020 + i * 2}
            )
        
        # Cleanup
        await gateway.cleanup()
        
        # All sessions should be gone
        for i in range(3):
            assert gateway.get_session(f"test-cleanup-{i}") is None


class TestAudioConversion:
    """Test audio format conversion"""
    
    def test_ulaw_decode(self):
        """Test μ-law to linear PCM conversion"""
        # μ-law encoded silence
        ulaw_data = bytes([0xFF] * 160)
        
        pcm_data = audioop.ulaw2lin(ulaw_data, 2)
        
        # Output should be 2x size (16-bit samples)
        assert len(pcm_data) == 320
    
    def test_resample_8k_to_16k(self):
        """Test resampling from 8kHz to 16kHz"""
        # 20ms of silence at 8kHz = 160 samples = 320 bytes
        pcm_8k = bytes([0x00] * 320)
        
        pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
        
        # Output should be ~2x size (16kHz vs 8kHz)
        assert len(pcm_16k) >= 600  # Approximately 640
    
    def test_resample_16k_to_8k(self):
        """Test resampling from 16kHz to 8kHz (for TTS output)"""
        # 20ms at 16kHz = 320 samples = 640 bytes
        pcm_16k = bytes([0x00] * 640)
        
        pcm_8k, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
        
        # Output should be ~0.5x size
        assert len(pcm_8k) >= 300  # Approximately 320
    
    def test_linear_to_ulaw(self):
        """Test PCM to μ-law encoding"""
        # Linear PCM silence
        pcm_data = bytes([0x00] * 320)
        
        ulaw_data = audioop.lin2ulaw(pcm_data, 2)
        
        # Output should be 0.5x size (8-bit samples)
        assert len(ulaw_data) == 160
    
    def test_round_trip_conversion(self):
        """Test μ-law → PCM → μ-law round trip"""
        # Original μ-law data
        original_ulaw = bytes([0x7F, 0x80, 0xFF, 0x00] * 40)  # 160 bytes
        
        # Decode
        pcm = audioop.ulaw2lin(original_ulaw, 2)
        
        # Encode back
        recovered_ulaw = audioop.lin2ulaw(pcm, 2)
        
        # Should be same size
        assert len(recovered_ulaw) == len(original_ulaw)
