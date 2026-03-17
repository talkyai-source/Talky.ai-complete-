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
    
class TestMediaGatewayFactory:
    """Integration tests for media gateway factory."""

    @pytest.mark.asyncio
    async def test_factory_creates_browser_gateway(self):
        """Test factory creates Browser gateway."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory

        gateway = MediaGatewayFactory.create("browser")
        assert gateway.name == "browser"

    def test_factory_lists_gateways(self):
        """Test factory lists available gateways."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory

        gateways = MediaGatewayFactory.list_gateways()
        assert "browser" in gateways


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
