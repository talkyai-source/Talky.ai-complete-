"""
Test audio flow through the entire pipeline
"""
import asyncio
import websockets
import json
import os
import numpy as np
from dotenv import load_dotenv

load_dotenv()

async def test_audio_flow():
    """Test that audio flows from client to STT"""
    uri = "ws://localhost:8000/api/v1/ws/ai-test/test-audio-flow"
    
    print("="*60)
    print("AUDIO FLOW TEST")
    print("="*60)
    
    async with websockets.connect(uri) as ws:
        print("\n1️⃣ Connected, sending config...")
        await ws.send(json.dumps({"type": "config", "voice_id": "sophia"}))
        
        # Wait for ready
        response = await ws.recv()
        data = json.loads(response)
        print(f"   ✅ {data.get('type')}: {data.get('agent_name')}")
        
        # Wait for intro
        print("\n2️⃣ Waiting for intro...")
        audio_chunks = 0
        while True:
            response = await ws.recv()
            if isinstance(response, str):
                data = json.loads(response)
                if data.get('type') == 'turn_complete':
                    break
            else:
                audio_chunks += 1
        print(f"   ✅ Intro complete ({audio_chunks} audio chunks)")
        
        # Select voice
        print("\n3️⃣ Selecting voice...")
        await ws.send(json.dumps({"type": "voice_selected", "voice_id": "sophia"}))
        
        response = await ws.recv()
        data = json.loads(response)
        print(f"   ✅ {data.get('type')}")
        
        # Send real-looking audio (speech-like)
        print("\n4️⃣ Sending speech-like audio...")
        sample_rate = 16000
        duration = 5  # 5 seconds
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        
        # Create speech-like signal (multiple formants + noise)
        signal = np.sin(2 * np.pi * 120 * t) * 0.15  # F0 (fundamental)
        signal += np.sin(2 * np.pi * 600 * t) * 0.1   # F1 (first formant)
        signal += np.sin(2 * np.pi * 1200 * t) * 0.08 # F2 (second formant)
        signal += np.sin(2 * np.pi * 2500 * t) * 0.05 # F3 (third formant)
        signal += np.random.normal(0, 0.02, len(t))   # Breath noise
        
        # Modulate like speech (syllables)
        syllable_rate = 4  # 4 syllables per second
        modulation = 0.3 + 0.7 * (0.5 + 0.5 * np.sin(2 * np.pi * syllable_rate * t)) ** 2
        signal *= modulation
        
        # Normalize
        signal = signal / np.max(np.abs(signal)) * 0.9
        
        # Convert to Int16
        audio_int16 = (signal * 32767).astype(np.int16)
        
        # Send in 100ms chunks (3200 samples at 16kHz)
        chunk_size = 3200
        chunks_sent = 0
        for i in range(0, len(audio_int16), chunk_size):
            chunk = audio_int16[i:i+chunk_size]
            if len(chunk) == chunk_size:
                await ws.send(chunk.tobytes())
                chunks_sent += 1
                await asyncio.sleep(0.05)  # 50ms delay between chunks
        
        print(f"   ✅ Sent {chunks_sent} audio chunks ({duration}s of audio)")
        
        # Send silence for 2 seconds to let STT process
        print("\n5️⃣ Sending silence to let STT process...")
        for _ in range(20):
            await ws.send(bytes(3200))
            await asyncio.sleep(0.1)
        
        # Wait for transcripts
        print("\n6️⃣ Waiting for transcripts (10s timeout)...")
        transcripts = []
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < 10:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=1.0)
                if isinstance(response, str):
                    data = json.loads(response)
                    if data.get('type') == 'transcript':
                        transcripts.append(data)
                        print(f"   📨 Transcript: {data.get('text')!r} (final={data.get('is_final')})")
                    elif data.get('type') == 'llm_response':
                        print(f"   🤖 LLM: {data.get('text')!r}")
                    elif data.get('type') == 'barge_in':
                        print(f"   🔔 Barge-in detected")
            except asyncio.TimeoutError:
                continue
        
        print(f"\n   Total transcripts: {len(transcripts)}")
        
        # End call
        await ws.send(json.dumps({"type": "end_call"}))
        
        if transcripts:
            print("\n🎉 Audio flow is working! STT is producing transcripts.")
            return True
        else:
            print("\n⚠️ No transcripts received. STT may not be working.")
            return False

if __name__ == "__main__":
    result = asyncio.run(test_audio_flow())
    exit(0 if result else 1)
