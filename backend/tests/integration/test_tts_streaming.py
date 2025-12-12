"""
Integration Test for TTS Streaming Pipeline
Tests TTS -> audio format conversion -> RTP packetization
"""
import asyncio
import os
import pytest
from dotenv import load_dotenv

load_dotenv()


class TestTTSStreamingPipeline:
    """Integration tests for TTS streaming with RTP output."""
    
    @pytest.mark.asyncio
    async def test_tts_to_g711_conversion(self):
        """Test TTS output can be converted to G.711 format."""
        from app.infrastructure.tts.cartesia import CartesiaTTSProvider
        from app.utils.audio_utils import convert_for_rtp
        
        api_key = os.getenv("CARTESIA_API_KEY")
        if not api_key:
            pytest.skip("CARTESIA_API_KEY not set")
        
        # Initialize TTS
        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": api_key,
            "model_id": "sonic-3",
            "sample_rate": 22050
        })
        
        # Generate short audio
        text = "Hello"
        audio_chunks = []
        
        async for chunk in tts.stream_synthesize(
            text=text,
            voice_id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            sample_rate=22050
        ):
            audio_chunks.append(chunk.data)
        
        assert len(audio_chunks) > 0
        
        # Convert first chunk to G.711
        g711_audio = convert_for_rtp(
            audio_chunks[0],
            source_rate=22050,
            source_format="pcm_f32le",
            codec="ulaw"
        )
        
        assert len(g711_audio) > 0
        
        await tts.cleanup()
    
    @pytest.mark.asyncio
    async def test_tts_to_rtp_packets(self):
        """Test TTS output can be packetized into RTP."""
        from app.infrastructure.tts.cartesia import CartesiaTTSProvider
        from app.utils.audio_utils import convert_for_rtp
        from app.utils.rtp_builder import RTPPacketBuilder, PayloadType, RTPPacket
        
        api_key = os.getenv("CARTESIA_API_KEY")
        if not api_key:
            pytest.skip("CARTESIA_API_KEY not set")
        
        # Initialize TTS
        tts = CartesiaTTSProvider()
        await tts.initialize({
            "api_key": api_key,
            "model_id": "sonic-3",
            "sample_rate": 22050
        })
        
        # Initialize RTP builder
        rtp_builder = RTPPacketBuilder(payload_type=PayloadType.PCMU)
        
        # Generate audio and build RTP packets
        text = "Test message"
        all_packets = []
        
        async for chunk in tts.stream_synthesize(
            text=text,
            voice_id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            sample_rate=22050
        ):
            # Convert to G.711
            g711_audio = convert_for_rtp(
                chunk.data,
                source_rate=22050,
                source_format="pcm_f32le",
                codec="ulaw"
            )
            
            # Build RTP packets
            packets = rtp_builder.build_packets_from_audio(g711_audio)
            all_packets.extend(packets)
        
        assert len(all_packets) > 0
        
        # Verify packets are valid
        for pkt_data in all_packets[:3]:
            pkt = RTPPacket.from_bytes(pkt_data)
            assert pkt.version == 2
            assert pkt.payload_type == PayloadType.PCMU
            assert len(pkt.payload) == 160  # 20ms of G.711
        
        await tts.cleanup()


class TestRTPMediaGatewayIntegration:
    """Integration tests for RTP media gateway."""
    
    @pytest.mark.asyncio
    async def test_gateway_session_lifecycle(self):
        """Test RTP session creation and cleanup."""
        from app.infrastructure.telephony.rtp_media_gateway import RTPMediaGateway
        
        gateway = RTPMediaGateway()
        await gateway.initialize({
            "remote_ip": "127.0.0.1",
            "remote_port": 5004,
            "codec": "ulaw"
        })
        
        # Start call
        await gateway.on_call_started("test-call-1", {})
        
        session = gateway.get_session("test-call-1")
        assert session is not None
        assert session.codec == "ulaw"
        assert session.udp_socket is not None
        
        # Verify queues exist
        assert gateway.get_audio_queue("test-call-1") is not None
        assert gateway.get_output_queue("test-call-1") is not None
        
        # End call - this deletes the session
        await gateway.on_call_ended("test-call-1", "test_complete")
        
        # Verify session is cleaned up
        assert gateway.get_session("test-call-1") is None
        assert gateway.get_audio_queue("test-call-1") is None
        
        await gateway.cleanup()
    
    @pytest.mark.asyncio
    async def test_gateway_with_alaw_codec(self):
        """Test RTP gateway with A-law codec."""
        from app.infrastructure.telephony.rtp_media_gateway import RTPMediaGateway
        
        gateway = RTPMediaGateway()
        await gateway.initialize({
            "codec": "alaw"
        })
        
        await gateway.on_call_started("test-call-2", {"codec": "alaw"})
        
        session = gateway.get_session("test-call-2")
        assert session.codec == "alaw"
        
        await gateway.on_call_ended("test-call-2", "test_complete")
        await gateway.cleanup()


class TestMediaGatewayFactory:
    """Integration tests for media gateway factory."""
    
    @pytest.mark.asyncio
    async def test_factory_creates_vonage_gateway(self):
        """Test factory creates Vonage gateway."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        
        gateway = MediaGatewayFactory.create("vonage")
        assert gateway.name == "vonage"
    
    @pytest.mark.asyncio
    async def test_factory_creates_rtp_gateway(self):
        """Test factory creates RTP gateway."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        
        gateway = MediaGatewayFactory.create("rtp")
        assert gateway.name == "rtp"
    
    def test_factory_lists_gateways(self):
        """Test factory lists available gateways."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        
        gateways = MediaGatewayFactory.list_gateways()
        assert "vonage" in gateways
        assert "rtp" in gateways


class TestLatencyTrackerIntegration:
    """Integration tests for latency tracking."""
    
    @pytest.mark.asyncio
    async def test_full_turn_tracking(self):
        """Test tracking a complete voice pipeline turn."""
        from app.domain.services.latency_tracker import LatencyTracker
        import time
        
        tracker = LatencyTracker()
        
        # Simulate a voice pipeline turn
        tracker.start_turn("call-1", turn_id=1)
        
        await asyncio.sleep(0.05)  # Simulate processing
        tracker.mark_llm_start("call-1")
        
        await asyncio.sleep(0.1)  # Simulate LLM
        tracker.mark_llm_end("call-1")
        
        tracker.mark_tts_start("call-1")
        await asyncio.sleep(0.05)  # Simulate TTS
        tracker.mark_audio_start("call-1")
        
        # Get metrics
        metrics = tracker.get_metrics("call-1")
        
        assert metrics.total_latency_ms > 150  # At least 150ms (our delays)
        assert metrics.llm_latency_ms >= 100  # At least 100ms
        assert metrics.time_to_first_audio_ms >= 50  # At least 50ms
        
        # Log and archive
        tracker.log_metrics("call-1")
        
        history = tracker.get_history("call-1")
        assert len(history) == 1
