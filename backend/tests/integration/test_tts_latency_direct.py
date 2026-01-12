"""
Isolated TTS Latency Test
==========================
Tests Google TTS directly without WebSocket to measure pure API latency.
"""

import asyncio
import time
import os
from dotenv import load_dotenv

load_dotenv()

async def test_google_tts_latency():
    """Test Google TTS API latency directly."""
    from app.infrastructure.tts.google_tts import GoogleTTSProvider
    
    print("=" * 60)
    print("  ISOLATED GOOGLE TTS LATENCY TEST")
    print("=" * 60)
    
    # Initialize provider
    tts = GoogleTTSProvider()
    await tts.initialize({
        "api_key": os.getenv("GOOGLE_TTS_API_KEY"),
        "voice_id": "en-US-Chirp3-HD-Leda",
        "language_code": "en-US",
        "sample_rate": 16000
    })
    
    test_texts = [
        ("Short", "Hello, how are you?"),
        ("Medium", "Hi there! I'm Sophia. I help businesses connect with their customers."),
        ("Long", "Hi there! I'm Sophia. I help businesses connect with their customers through natural phone conversations. What would you like to know about how I can help your business?")
    ]
    
    print("\nTesting different text lengths:\n")
    
    for label, text in test_texts:
        # Measure total time including API call and processing
        start = time.time()
        chunks = []
        first_chunk_time = None
        
        async for chunk in tts.stream_synthesize(
            text=text,
            voice_id="en-US-Chirp3-HD-Leda",
            sample_rate=16000
        ):
            if first_chunk_time is None:
                first_chunk_time = time.time()
            chunks.append(chunk)
        
        end = time.time()
        
        total_latency = (end - start) * 1000
        first_chunk_latency = (first_chunk_time - start) * 1000 if first_chunk_time else 0
        total_bytes = sum(len(c.data) for c in chunks)
        audio_duration = total_bytes / (16000 * 4) * 1000  # 16kHz, Float32 = 4 bytes/sample
        
        print(f"  [{label}] \"{text[:40]}{'...' if len(text) > 40 else ''}\"")
        print(f"    ├── First chunk latency: {first_chunk_latency:.0f}ms")
        print(f"    ├── Total TTS latency:   {total_latency:.0f}ms")
        print(f"    ├── Audio duration:      {audio_duration:.0f}ms")
        print(f"    ├── Chunks generated:    {len(chunks)}")
        print(f"    └── Total bytes:         {total_bytes:,}")
        print()
    
    await tts.cleanup()
    
    print("=" * 60)
    print("\nNOTE: All latency is from Google's API - not truly streaming.")
    print("Google TTS generates complete audio before responding.")
    print("For lower latency, consider:")
    print("  1. Cartesia TTS (native streaming)")
    print("  2. ElevenLabs TTS (native streaming)")
    print("  3. Google TTS Streaming API (gRPC, more complex)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_google_tts_latency())
