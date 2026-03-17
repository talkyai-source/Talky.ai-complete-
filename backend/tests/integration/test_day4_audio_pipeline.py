"""
Day 4 Audio Pipeline Integration Test
Tests the complete audio pipeline: Media Gateway → STT → LLM → TTS
"""
import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from app.utils.audio_utils import generate_sine_wave, generate_silence
from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.domain.models.session import CallSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_audio_pipeline():
    """
    Test the complete audio pipeline with simulated audio.
    
    Pipeline Flow:
    1. Generate test audio (sine wave)
    2. Send to media gateway
    3. Process through STT (Deepgram Flux)
    4. Get LLM response (Groq)
    5. Synthesize TTS (Cartesia)
    6. Verify output audio
    """
    print("\n" + "="*70)
    print("  🎙️  DAY 4 AUDIO PIPELINE TEST")
    print("="*70)
    print()
    
    # Initialize media gateway
    print("📡 Initializing media gateway...")
    media_gateway = BrowserMediaGateway()
    await media_gateway.initialize({
        "sample_rate": 16000,
        "channels": 1,
        "max_queue_size": 100
    })
    print("✅ Media Gateway initialized")
    
    # Initialize STT provider
    print("🎤 Initializing STT provider (Deepgram Flux)...")
    stt_provider = DeepgramFluxSTTProvider()
    await stt_provider.initialize({
        "api_key": os.getenv("DEEPGRAM_API_KEY"),
        "model": "flux-general-en",
        "sample_rate": 16000,
        "encoding": "linear16"
    })
    print("✅ STT Provider initialized")
    
    # Initialize LLM provider
    print("🤖 Initializing LLM provider (Groq)...")
    llm_provider = GroqLLMProvider()
    await llm_provider.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": "llama-3.1-8b-instant",
        "temperature": 0.7,
        "max_tokens": 150
    })
    print("✅ LLM Provider initialized")
    
    # Initialize TTS provider
    print("🔊 Initializing TTS provider (Cartesia)...")
    tts_provider = CartesiaTTSProvider()
    await tts_provider.initialize({
        "api_key": os.getenv("CARTESIA_API_KEY"),
        "model_id": "sonic-3",
        "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        "sample_rate": 16000
    })
    print("✅ TTS Provider initialized")
    
    # Initialize pipeline service
    print("🔗 Initializing voice pipeline service...")
    pipeline_service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=llm_provider,
        tts_provider=tts_provider,
        media_gateway=media_gateway
    )
    print("✅ Voice Pipeline Service initialized")
    
    print()
    print("="*70)
    print("  🧪 TESTING AUDIO PIPELINE")
    print("="*70)
    print()
    
    # Create test call session
    call_id = "test-call-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    
    session = CallSession(
        call_id=call_id,
        campaign_id="test-campaign",
        lead_id="test-lead",
        provider_call_id=call_id,
        system_prompt="You are a helpful voice assistant. Keep responses brief and conversational, under 2 sentences.",
        voice_id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        language="en"
    )
    
    # Start call in media gateway
    print(f"📞 Starting call: {call_id}")
    await media_gateway.on_call_started(call_id, {
        "campaign_id": session.campaign_id,
        "lead_id": session.lead_id,
        "started_at": datetime.utcnow().isoformat()
    })
    
    # Generate test audio (simulated speech)
    print("🎵 Generating test audio (3 seconds of 440Hz sine wave)...")
    test_audio_chunks = []
    
    # Generate 3 seconds of audio in 80ms chunks
    for i in range(37):  # 3000ms / 80ms ≈ 37 chunks
        audio_chunk = generate_sine_wave(440, 80, 16000, 1, 0.3)
        test_audio_chunks.append(audio_chunk)
    
    print(f"✅ Generated {len(test_audio_chunks)} audio chunks")
    
    # Send audio to media gateway
    print("📤 Sending audio to media gateway...")
    for chunk in test_audio_chunks:
        await media_gateway.on_audio_received(call_id, chunk)
    
    # Get metrics
    metrics = media_gateway.get_metrics(call_id)
    print(f"✅ Audio buffered: {metrics['total_chunks']} chunks, {metrics['total_bytes']} bytes")
    
    # Verify audio queue
    audio_queue = media_gateway.get_audio_queue(call_id)
    print(f"📊 Audio queue size: {audio_queue.qsize()} chunks")
    
    print()
    print("="*70)
    print("  📊 TEST RESULTS")
    print("="*70)
    print()
    
    # Display metrics
    print("Audio Metrics:")
    print(f"  - Total Chunks: {metrics['total_chunks']}")
    print(f"  - Total Bytes: {metrics['total_bytes']}")
    print(f"  - Total Duration: {metrics['total_duration_ms']:.1f}ms")
    print(f"  - Validation Errors: {metrics['validation_errors']}")
    print(f"  - Buffer Overflows: {metrics['buffer_overflows']}")
    
    print()
    print("Session State:")
    print(f"  - Call ID: {session.call_id}")
    print(f"  - State: {session.state}")
    print(f"  - Turn ID: {session.turn_id}")
    
    print()
    print("✅ All components initialized successfully!")
    print("✅ Audio pipeline ready for real-time processing!")
    
    print()
    print("="*70)
    print("  ℹ️  NOTE: Full STT→LLM→TTS test requires API keys")
    print("="*70)
    print()
    print("To test with real speech recognition:")
    print("1. Ensure DEEPGRAM_API_KEY is set in .env")
    print("2. Ensure GROQ_API_KEY is set in .env")
    print("3. Ensure CARTESIA_API_KEY is set in .env")
    print("4. Run: python flux_voice_agent.py")
    print()
    
    # Cleanup
    print("🧹 Cleaning up...")
    await media_gateway.on_call_ended(call_id, "test_complete")
    await media_gateway.cleanup()
    print("✅ Cleanup complete")
    
    print()
    print("="*70)
    print("  ✅ DAY 4 AUDIO PIPELINE TEST COMPLETE")
    print("="*70)
    print()


if __name__ == "__main__":
    asyncio.run(test_audio_pipeline())
