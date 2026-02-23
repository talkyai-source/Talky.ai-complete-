"""
Full pipeline test - simulate audio input and verify response
"""
import asyncio
import websockets
import json
import os
import numpy as np
from dotenv import load_dotenv

load_dotenv()

async def receive_messages(ws, timeout_secs=30):
    """Receive all messages until timeout or end of conversation"""
    messages = []
    start_time = asyncio.get_event_loop().time()
    
    while True:
        try:
            remaining = timeout_secs - (asyncio.get_event_loop().time() - start_time)
            if remaining <= 0:
                break
                
            response = await asyncio.wait_for(ws.recv(), timeout=1.0)
            
            if isinstance(response, str):
                data = json.loads(response)
                messages.append(data)
                print(f"   📨 {data.get('type')}: {data.get('text', '')[:50] if data.get('text') else ''}")
                
                # Stop if we get turn_complete
                if data.get('type') == 'turn_complete':
                    break
            else:
                print(f"   🎵 Audio: {len(response)} bytes")
                
        except asyncio.TimeoutError:
            continue
            
    return messages

async def send_test_audio(ws):
    """Send test audio (simulated speech)"""
    # Create 3 seconds of test audio (16kHz, 16-bit PCM)
    # This is just noise but should trigger STT
    sample_rate = 16000
    duration = 3
    
    # Generate sine wave at 440Hz (like "A" musical note)
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    tone = np.sin(2 * np.pi * 440 * t) * 0.3
    
    # Convert to Int16
    audio_int16 = (tone * 32767).astype(np.int16)
    
    # Send in chunks (like microphone does)
    chunk_size = 4096  # 256ms at 16kHz
    for i in range(0, len(audio_int16), chunk_size):
        chunk = audio_int16[i:i+chunk_size]
        if len(chunk) > 0:
            await ws.send(chunk.tobytes())
            await asyncio.sleep(0.05)  # Small delay between chunks
    
    print(f"   🎤 Sent {duration}s of test audio")

async def test_full_conversation():
    """Test full conversation flow"""
    uri = "ws://localhost:8000/api/v1/ws/ai-test/test-pipeline-full"
    
    print("="*60)
    print("FULL PIPELINE TEST")
    print("="*60)
    
    async with websockets.connect(uri) as ws:
        print("\n1️⃣ Connecting and sending config...")
        await ws.send(json.dumps({"type": "config", "voice_id": "sophia"}))
        
        # Wait for ready
        response = await ws.recv()
        data = json.loads(response)
        print(f"   ✅ {data.get('type')}: {data.get('agent_name')}")
        
        # Wait for intro to complete
        print("\n2️⃣ Waiting for intro...")
        while True:
            response = await ws.recv()
            if isinstance(response, str):
                data = json.loads(response)
                if data.get('type') == 'turn_complete':
                    break
        print("   ✅ Intro complete")
        
        # Send voice_selected to start listening
        print("\n3️⃣ Selecting voice to start listening...")
        await ws.send(json.dumps({"type": "voice_selected", "voice_id": "sophia"}))
        
        # Receive voice_confirmed
        response = await ws.recv()
        data = json.loads(response)
        print(f"   ✅ {data.get('type')}")
        
        # Send test audio
        print("\n4️⃣ Sending test audio (simulated speech)...")
        await send_test_audio(ws)
        
        # Keep sending silence for a bit to let STT process
        print("\n5️⃣ Sending silence to let STT process...")
        silence = bytes(3200)  # 100ms silence
        for i in range(20):  # 2 seconds of silence
            await ws.send(silence)
            await asyncio.sleep(0.1)
        
        # Wait for response
        print("\n6️⃣ Waiting for AI response...")
        messages = await receive_messages(ws, timeout_secs=15)
        
        # Check what we received
        transcript_count = len([m for m in messages if m.get('type') == 'transcript'])
        llm_count = len([m for m in messages if m.get('type') == 'llm_response'])
        turn_complete_count = len([m for m in messages if m.get('type') == 'turn_complete'])
        
        print("\n" + "="*60)
        print("RESULTS")
        print("="*60)
        print(f"  Transcripts received: {transcript_count}")
        print(f"  LLM responses: {llm_count}")
        print(f"  Turn completes: {turn_complete_count}")
        
        # End call
        await ws.send(json.dumps({"type": "end_call"}))
        
        if transcript_count > 0 and llm_count > 0:
            print("\n🎉 Pipeline is working!")
            return True
        else:
            print("\n❌ Pipeline issue detected!")
            if transcript_count == 0:
                print("   - STT not producing transcripts")
            if llm_count == 0:
                print("   - LLM not generating responses")
            return False

if __name__ == "__main__":
    result = asyncio.run(test_full_conversation())
    exit(0 if result else 1)
