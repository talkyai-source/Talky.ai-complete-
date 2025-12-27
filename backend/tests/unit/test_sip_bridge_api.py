"""
Unit tests for SIP Bridge API Endpoints
Tests REST API and integration points.

Day 18: MicroSIP integration testing
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from datetime import datetime


class TestSIPBridgeEndpoints:
    """Test SIP bridge API endpoints"""
    
    @pytest.fixture
    def mock_sip_server(self):
        """Create mock SIP server"""
        mock = MagicMock()
        mock._running = False
        mock._calls = {}
        mock._rtp_sockets = {}
        mock.sip_port = 5060
        mock.host = "0.0.0.0"
        return mock
    
    def test_status_when_stopped(self):
        """Test status endpoint when server is stopped"""
        with patch('app.api.v1.endpoints.sip_bridge._sip_server', None):
            from app.api.v1.endpoints.sip_bridge import get_sip_status
            import asyncio
            
            result = asyncio.get_event_loop().run_until_complete(get_sip_status())
            
            assert result.status_code == 200
            body = result.body.decode()
            assert "stopped" in body
    
    def test_status_when_running(self, mock_sip_server):
        """Test status endpoint when server is running"""
        mock_sip_server._running = True
        
        with patch('app.api.v1.endpoints.sip_bridge._sip_server', mock_sip_server):
            from app.api.v1.endpoints.sip_bridge import get_sip_status
            import asyncio
            
            result = asyncio.get_event_loop().run_until_complete(get_sip_status())
            
            assert result.status_code == 200
            body = result.body.decode()
            assert "running" in body


class TestSIPGatewayFactory:
    """Test MediaGatewayFactory with SIP gateway"""
    
    def test_factory_creates_sip_gateway(self):
        """Test factory can create SIP gateway"""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        
        gateway = MediaGatewayFactory.create("sip")
        
        assert gateway is not None
        assert gateway.name == "sip"
    
    def test_factory_lists_sip_gateway(self):
        """Test factory lists SIP gateway type"""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        
        gateways = MediaGatewayFactory.list_gateways()
        
        assert "sip" in gateways
        assert "vonage" in gateways
        assert "browser" in gateways


class TestSIPIntegration:
    """Integration-style tests for SIP components"""
    
    @pytest.mark.asyncio
    async def test_full_audio_pipeline(self):
        """Test audio flow through SIP gateway"""
        from app.infrastructure.telephony.sip_media_gateway import SIPMediaGateway
        import audioop
        
        gateway = SIPMediaGateway()
        await gateway.initialize({})
        
        # Start a call
        await gateway.on_call_started(
            call_id="integration-001",
            metadata={
                "remote_addr": ("127.0.0.1", 5060),
                "rtp_port": 10000,
                "codec": "PCMU"
            }
        )
        
        # Simulate receiving μ-law audio (20ms at 8kHz = 160 samples)
        # Create valid μ-law data
        ulaw_audio = bytes([0xFF] * 160)  # Silence in μ-law
        
        await gateway.on_audio_received("integration-001", ulaw_audio)
        
        # Check audio was queued
        session = gateway.get_session("integration-001")
        assert session is not None
        assert not session.audio_queue.empty()
        
        # Get the processed audio
        pcm_audio = await session.audio_queue.get()
        
        # Should be upsampled to 16kHz (roughly 2x samples)
        # 160 samples at 8kHz → ~320 samples at 16kHz → ~640 bytes
        assert len(pcm_audio) >= 600
        
        # End call
        await gateway.on_call_ended("integration-001", "test_complete")
        assert gateway.get_session("integration-001") is None
    
    @pytest.mark.asyncio
    async def test_multiple_concurrent_calls(self):
        """Test handling multiple simultaneous calls"""
        from app.infrastructure.telephony.sip_media_gateway import SIPMediaGateway
        
        gateway = SIPMediaGateway()
        await gateway.initialize({})
        
        # Start 3 calls
        for i in range(3):
            await gateway.on_call_started(
                call_id=f"concurrent-{i}",
                metadata={
                    "remote_addr": ("127.0.0.1", 5060 + i),
                    "rtp_port": 10000 + i * 2
                }
            )
        
        # Verify all calls active
        for i in range(3):
            assert gateway.is_session_active(f"concurrent-{i}") is True
        
        # Send audio to each
        ulaw_audio = bytes([0xFF] * 160)
        for i in range(3):
            await gateway.on_audio_received(f"concurrent-{i}", ulaw_audio)
        
        # Verify each received audio
        for i in range(3):
            session = gateway.get_session(f"concurrent-{i}")
            assert session.chunks_received == 1
        
        # Cleanup
        await gateway.cleanup()
        
        # All should be gone
        for i in range(3):
            assert gateway.get_session(f"concurrent-{i}") is None
