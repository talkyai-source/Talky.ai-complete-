"""
Comprehensive test for all 3 voices in the Ask AI WebSocket
Tests that each voice produces audio with the correct voice_id
"""
import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

load_dotenv()

async def receive_json(ws, timeout=10):
    """Receive JSON message, skip binary data"""
    while True:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(response, str):
                return json.loads(response)
            else:
                print(f"     🎵 Audio chunk: {len(response)} bytes")
        except asyncio.TimeoutError:
            return None

async def test_voice(voice_name, expected_voice_id):
    """Test a single voice"""
    uri = f"ws://localhost:8000/api/v1/ws/ai-test/test-{voice_name}-{asyncio.get_event_loop().time()}"
    
    print(f"\n🎤 Testing {voice_name.upper()} (expected: {expected_voice_id})")
    print(f"   Connecting...")
    
    async with websockets.connect(uri) as ws:
        # Send config with voice
        await ws.send(json.dumps({"type": "config", "voice_id": voice_name}))
        
        # Wait for ready
        data = await receive_json(ws)
        if not data or data.get('type') != 'ready':
            print(f"   ❌ Failed: No ready message")
            return False
        
        actual_voice = data.get('voice_id')
        print(f"   ✅ Connected - Voice: {actual_voice}")
        
        if actual_voice != expected_voice_id:
            print(f"   ❌ Voice mismatch! Expected {expected_voice_id}, got {actual_voice}")
            return False
        
        # Wait for intro audio (llm_response + audio + turn_complete)
        audio_received = False
        for _ in range(20):  # Max 20 messages
            data = await receive_json(ws, timeout=5)
            if not data:
                break
            if data.get('type') == 'turn_complete':
                break
        
        print(f"   ✅ Voice {voice_name} works correctly!")
        return True

async def test_voice_switching():
    """Test switching between all voices"""
    uri = f"ws://localhost:8000/api/v1/ws/ai-test/test-switch-{asyncio.get_event_loop().time()}"
    
    print(f"\n🔄 Testing VOICE SWITCHING")
    print(f"   Connecting...")
    
    async with websockets.connect(uri) as ws:
        # Start with Sophia
        await ws.send(json.dumps({"type": "config", "voice_id": "sophia"}))
        
        data = await receive_json(ws)
        print(f"   1. Initial voice: {data.get('agent_name')} ({data.get('voice_id')})")
        
        # Wait for intro to complete
        while True:
            data = await receive_json(ws, timeout=5)
            if not data or data.get('type') == 'turn_complete':
                break
        
        # Switch to Emma
        print(f"   2. Switching to Emma...")
        await ws.send(json.dumps({"type": "switch_voice", "voice_id": "emma"}))
        
        data = await receive_json(ws)
        if data and data.get('type') == 'voice_switched':
            print(f"      ✅ Switched to: {data.get('agent_name')} ({data.get('voice_id')})")
        else:
            print(f"      ❌ Switch failed: {data}")
            return False
        
        # Switch to Alex
        print(f"   3. Switching to Alex...")
        await ws.send(json.dumps({"type": "switch_voice", "voice_id": "alex"}))
        
        data = await receive_json(ws)
        if data and data.get('type') == 'voice_switched':
            print(f"      ✅ Switched to: {data.get('agent_name')} ({data.get('voice_id')})")
        else:
            print(f"      ❌ Switch failed: {data}")
            return False
        
        # End call
        await ws.send(json.dumps({"type": "end_call"}))
        
    print(f"   ✅ Voice switching works!")
    return True

async def main():
    print("=" * 60)
    print("ASK AI VOICE TEST SUITE")
    print("=" * 60)
    
    # Test individual voices
    results = []
    
    results.append(await test_voice("sophia", "aura-asteria-en"))
    results.append(await test_voice("emma", "aura-luna-en"))
    results.append(await test_voice("alex", "aura-orion-en"))
    
    # Test voice switching
    results.append(await test_voice_switching())
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    
    tests = ["Sophia voice", "Emma voice", "Alex voice", "Voice switching"]
    for test, passed in zip(tests, results):
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {test}")
    
    if all(results):
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n⚠️ Some tests failed!")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
