"""
Simple test to verify Deepgram TTS is working and generating audio.
"""
import asyncio
import os
import aiohttp
import audioop
from dotenv import load_dotenv

load_dotenv()

async def test_tts():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("❌ DEEPGRAM_API_KEY not set!")
        return
    
    print(f"✓ API Key found: {api_key[:10]}...")
    
    text = "Hello! This is a test of Deepgram text to speech."
    print(f"🎤 Synthesizing: '{text}'")
    
    url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=linear16&sample_rate=16000"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }
    payload = {"text": text}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            print(f"📡 Response status: {response.status}")
            
            if response.status != 200:
                error = await response.text()
                print(f"❌ Error: {error}")
                return
            
            pcm_16k = await response.read()
            print(f"✓ Received {len(pcm_16k)} bytes of PCM 16kHz audio")
            
            # Resample to 8kHz
            pcm_8k, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
            print(f"✓ Resampled to {len(pcm_8k)} bytes of PCM 8kHz audio")
            
            # Encode to ulaw
            ulaw_data = audioop.lin2ulaw(pcm_8k, 2)
            print(f"✓ Encoded to {len(ulaw_data)} bytes of µ-law audio")
            
            # Calculate duration
            duration_ms = len(ulaw_data) / 8  # 8 bytes per millisecond at 8kHz mono µ-law
            print(f"✓ Audio duration: {duration_ms:.0f}ms ({duration_ms/1000:.1f}s)")
            
            # Count frames
            frame_count = len(ulaw_data) // 160
            print(f"✓ Number of 20ms frames: {frame_count}")
            
            # Save to file for verification
            with open("test_audio.ulaw", "wb") as f:
                f.write(ulaw_data)
            print("✓ Saved to test_audio.ulaw")
            
            # Also save PCM for playback testing
            with open("test_audio.pcm", "wb") as f:
                f.write(pcm_16k)
            print("✓ Saved to test_audio.pcm (16-bit 16kHz mono)")

if __name__ == "__main__":
    asyncio.run(test_tts())
