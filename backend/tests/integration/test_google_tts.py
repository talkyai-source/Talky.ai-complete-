"""
Test script for Deepgram TTS - Saves and plays audio
Run: python test_deepgram_tts.py
"""
import asyncio
import os
import sys
import wave
import subprocess

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_deepgram_tts():
    """Test Deepgram TTS with a greeting message and play it."""
    from dotenv import load_dotenv
    load_dotenv()
    
    from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
    
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found in environment")
        return
    
    print(f"✓ Using Deepgram API key: {api_key[:10]}...")
    print("\n=== Testing Deepgram TTS ===\n")
    
    try:
        # Initialize provider
        tts_provider = DeepgramTTSProvider()
        await tts_provider.initialize({
            "api_key": api_key,
            "voice_id": "aura-asteria-en",
            "sample_rate": 16000
        })
        print("✓ Deepgram TTS initialized successfully")
        
        # Test synthesis
        greeting = "Hello! This is a test greeting from Talky AI. How can I help you today?"
        print(f"\n📢 Synthesizing: \"{greeting}\"\n")
        
        # Get raw audio (Int16 PCM for saving as WAV)
        print("Generating audio...")
        raw_audio = await tts_provider.synthesize_raw(
            text=greeting,
            voice_id="aura-asteria-en",
            sample_rate=16000
        )
        print(f"✓ Generated {len(raw_audio)} bytes ({len(raw_audio) / 1024:.1f} KB)")
        print(f"  Duration: ~{len(raw_audio) / (16000 * 2):.2f} seconds")
        
        # Save to WAV file
        wav_path = os.path.join(os.path.dirname(__file__), "test_greeting.wav")
        with wave.open(wav_path, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit = 2 bytes
            wav_file.setframerate(16000)  # 16kHz
            wav_file.writeframes(raw_audio)
        
        print(f"✓ Saved to: {wav_path}")
        
        # Play the audio (Windows)
        print("\n🔊 Playing audio...")
        try:
            # Use Windows Media Player
            subprocess.run(
                ['powershell', '-c', f'(New-Object Media.SoundPlayer "{wav_path}").PlaySync()'],
                check=True,
                timeout=15
            )
            print("✓ Audio played successfully!")
        except subprocess.TimeoutExpired:
            print("✓ Audio playback started (may still be playing)")
        except Exception as e:
            print(f"⚠ Could not play audio automatically: {e}")
            print(f"   Please manually open: {wav_path}")
        
        await tts_provider.cleanup()
        print("\n✓ Test complete!")
        
    except Exception as e:
        print(f"\n✗ TTS FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_deepgram_tts())
