"""
Unit Tests for Media Gateway
Tests VonageMediaGateway implementation
"""
import pytest
import asyncio
from app.infrastructure.telephony.vonage_media_gateway import VonageMediaGateway
from app.utils.audio_utils import generate_silence, generate_sine_wave


@pytest.mark.asyncio
class TestVonageMediaGateway:
    """Tests for Vonage Media Gateway"""
    
    async def test_initialization(self):
        """Test gateway initialization"""
        gateway = VonageMediaGateway()
        
        await gateway.initialize({
            "sample_rate": 16000,
            "channels": 1,
            "max_queue_size": 100
        })
        
        assert gateway.name == "vonage"
        assert gateway._sample_rate == 16000
        assert gateway._channels == 1
        assert gateway._max_queue_size == 100
    
    async def test_call_started(self):
        """Test call start event"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        metadata = {
            "campaign_id": "campaign-1",
            "lead_id": "lead-1",
            "phone_number": "+1234567890"
        }
        
        await gateway.on_call_started(call_id, metadata)
        
        # Verify queues created
        assert call_id in gateway._audio_queues
        assert call_id in gateway._output_queues
        
        # Verify metadata stored
        assert call_id in gateway._session_metadata
        assert gateway._session_metadata[call_id]["campaign_id"] == "campaign-1"
        assert gateway._session_metadata[call_id]["status"] == "active"
        
        # Verify metrics initialized
        assert call_id in gateway._audio_metrics
        assert gateway._audio_metrics[call_id]["total_chunks"] == 0
    
    async def test_audio_received_valid(self):
        """Test receiving valid audio chunk"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Generate valid 80ms audio chunk
        audio_chunk = generate_silence(80, 16000, 1, 16)
        
        await gateway.on_audio_received(call_id, audio_chunk)
        
        # Verify metrics updated
        metrics = gateway._audio_metrics[call_id]
        assert metrics["total_chunks"] == 1
        assert metrics["total_bytes"] == len(audio_chunk)
        assert metrics["total_duration_ms"] > 0
        assert metrics["validation_errors"] == 0
        
        # Verify audio queued
        queue = gateway.get_audio_queue(call_id)
        assert queue.qsize() == 1
        
        # Verify we can retrieve the audio
        retrieved_audio = await queue.get()
        assert retrieved_audio == audio_chunk
    
    async def test_audio_received_invalid_format(self):
        """Test receiving invalid audio chunk"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Invalid chunk (not divisible by frame size)
        invalid_audio = bytes(641)  # Not divisible by 2
        
        await gateway.on_audio_received(call_id, invalid_audio)
        
        # Verify validation error recorded
        metrics = gateway._audio_metrics[call_id]
        assert metrics["validation_errors"] == 1
        assert metrics["total_chunks"] == 0  # Not counted
        
        # Verify audio NOT queued
        queue = gateway.get_audio_queue(call_id)
        assert queue.qsize() == 0
    
    async def test_buffer_overflow(self):
        """Test buffer overflow handling"""
        gateway = VonageMediaGateway()
        await gateway.initialize({"max_queue_size": 5})  # Small buffer
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Fill buffer beyond capacity
        for i in range(10):
            audio_chunk = generate_silence(80, 16000, 1, 16)
            await gateway.on_audio_received(call_id, audio_chunk)
        
        # Verify overflow recorded
        metrics = gateway._audio_metrics[call_id]
        assert metrics["buffer_overflows"] > 0
        
        # Verify queue size doesn't exceed max
        queue = gateway.get_audio_queue(call_id)
        assert queue.qsize() <= 5
    
    async def test_multiple_audio_chunks(self):
        """Test receiving multiple audio chunks"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Send 10 chunks
        for i in range(10):
            audio_chunk = generate_sine_wave(440, 80, 16000, 1, 0.5)
            await gateway.on_audio_received(call_id, audio_chunk)
        
        # Verify metrics
        metrics = gateway._audio_metrics[call_id]
        assert metrics["total_chunks"] == 10
        assert metrics["total_duration_ms"] > 0
        
        # Verify all chunks queued
        queue = gateway.get_audio_queue(call_id)
        assert queue.qsize() == 10
    
    async def test_send_audio(self):
        """Test sending audio (outbound)"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Send audio chunk
        audio_chunk = generate_silence(80, 16000, 1, 16)
        await gateway.send_audio(call_id, audio_chunk)
        
        # Verify audio queued in output queue
        output_queue = gateway.get_output_queue(call_id)
        assert output_queue.qsize() == 1
        
        # Verify we can retrieve it
        retrieved_audio = await output_queue.get()
        assert retrieved_audio == audio_chunk
    
    async def test_call_ended(self):
        """Test call end event"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Send some audio
        audio_chunk = generate_silence(80, 16000, 1, 16)
        await gateway.on_audio_received(call_id, audio_chunk)
        
        # End call
        await gateway.on_call_ended(call_id, "hangup")
        
        # Verify metadata updated
        assert gateway._session_metadata[call_id]["status"] == "ended"
        assert gateway._session_metadata[call_id]["end_reason"] == "hangup"
        
        # Queues should still exist (for pipeline to finish)
        assert call_id in gateway._audio_queues
    
    async def test_get_metrics(self):
        """Test getting call metrics"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        call_id = "test-call-123"
        await gateway.on_call_started(call_id, {})
        
        # Send audio
        for i in range(5):
            audio_chunk = generate_silence(80, 16000, 1, 16)
            await gateway.on_audio_received(call_id, audio_chunk)
        
        # Get metrics
        metrics = gateway.get_metrics(call_id)
        assert metrics is not None
        assert metrics["total_chunks"] == 5
        assert metrics["total_bytes"] > 0
        assert metrics["validation_errors"] == 0
    
    async def test_cleanup(self):
        """Test gateway cleanup"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        # Create multiple calls
        for i in range(3):
            call_id = f"test-call-{i}"
            await gateway.on_call_started(call_id, {})
            
            # Send some audio
            audio_chunk = generate_silence(80, 16000, 1, 16)
            await gateway.on_audio_received(call_id, audio_chunk)
        
        # Cleanup
        await gateway.cleanup()
        
        # Verify all data cleared
        assert len(gateway._audio_queues) == 0
        assert len(gateway._output_queues) == 0
        assert len(gateway._session_metadata) == 0
        assert len(gateway._audio_metrics) == 0
    
    async def test_unknown_call_audio(self):
        """Test receiving audio for unknown call"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        # Send audio for non-existent call
        audio_chunk = generate_silence(80, 16000, 1, 16)
        await gateway.on_audio_received("unknown-call", audio_chunk)
        
        # Should not crash, just log warning
        # No metrics should be created
        assert "unknown-call" not in gateway._audio_metrics
    
    async def test_unknown_call_send_audio(self):
        """Test sending audio for unknown call"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        # Send audio for non-existent call
        audio_chunk = generate_silence(80, 16000, 1, 16)
        await gateway.send_audio("unknown-call", audio_chunk)
        
        # Should not crash, just log warning
        assert "unknown-call" not in gateway._output_queues
    
    async def test_concurrent_calls(self):
        """Test handling multiple concurrent calls"""
        gateway = VonageMediaGateway()
        await gateway.initialize({})
        
        # Start multiple calls
        call_ids = [f"call-{i}" for i in range(5)]
        
        for call_id in call_ids:
            await gateway.on_call_started(call_id, {"test": True})
        
        # Send audio to each call
        for call_id in call_ids:
            for i in range(3):
                audio_chunk = generate_sine_wave(440, 80, 16000, 1, 0.5)
                await gateway.on_audio_received(call_id, audio_chunk)
        
        # Verify all calls tracked independently
        for call_id in call_ids:
            metrics = gateway.get_metrics(call_id)
            assert metrics["total_chunks"] == 3
            
            queue = gateway.get_audio_queue(call_id)
            assert queue.qsize() == 3


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
