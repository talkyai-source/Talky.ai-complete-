"""
Test Deepgram Flux STT with microphone
Real-time transcription using the updated Flux provider
"""
import os
import sys
import asyncio
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("DEEPGRAM_API_KEY")
if not api_key:
    print("❌ DEEPGRAM_API_KEY not set in .env!")
    sys.exit(1)

print(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")

import pyaudio
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.domain.models.conversation import AudioChunk

SAMPLE_RATE = 16000
CHUNK = 2560  # ~80ms chunks as recommended by Deepgram docs

async def main():
    print("=" * 50)
    print("🎤 Deepgram Flux STT Test")
    print("=" * 50)
    
    # Initialize our STT provider
    stt = DeepgramFluxSTTProvider()
    await stt.initialize({
        "api_key": api_key,
        "model": "flux-general-en",
        "sample_rate": SAMPLE_RATE,
        "encoding": "linear16"
    })
    print("✅ STT Provider initialized")
    
    # Setup PyAudio for mic
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK
    )
    print("✅ Microphone opened")
    print("\n🗣️  SPEAK NOW - Press Ctrl+C to stop\n")
    
    # Audio generator from microphone
    stop_flag = False
    
    async def mic_audio_stream():
        while not stop_flag:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                yield AudioChunk(data=data, sample_rate=SAMPLE_RATE, channels=1)
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"Mic error: {e}")
                break
    
    try:
        # Stream transcription
        async for transcript in stt.stream_transcribe(mic_audio_stream()):
            if transcript.text:
                prefix = "📝" if transcript.is_final else "💬"
                print(f"{prefix} {transcript.text}")
            if stt.detect_turn_end(transcript):
                print("⏸️  [EndOfTurn - ready for response]")
                
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped")
        stop_flag = True
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()
        await stt.cleanup()
        print("✅ Cleanup done")

if __name__ == "__main__":
    asyncio.run(main())
