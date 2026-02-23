"""
Test script for voice switching in WebSocket
"""
import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

load_dotenv()

async def receive_json(ws):
    """Receive JSON message, skip binary data"""
    while True:
        response = await ws.recv()
        if isinstance(response, str):
            return json.loads(response)
        else:
            print(f"   (Received binary audio: {len(response)} bytes)")

async def test_voice_switching():
    """Test that voice switching works correctly"""
    uri = "ws://localhost:8000/api/v1/ws/ai-test/test-session-123"
    
    print("Connecting to WebSocket...")
    async with websockets.connect(uri) as ws:
        print("Connected!")
        
        # Send config with sophia
        print("\n1. Sending config with voice: sophia")
        await ws.send(json.dumps({"type": "config", "voice_id": "sophia"}))
        
        # Wait for ready message
        data = await receive_json(ws)
        print(f"   Received: {data['type']} - Agent: {data.get('agent_name')}, Voice: {data.get('voice_id')}")
        
        # Wait for turn_complete (skip audio)
        data = await receive_json(ws)
        print(f"   Received: {data['type']}")
        
        # Switch to emma
        print("\n2. Switching voice to: emma")
        await ws.send(json.dumps({"type": "switch_voice", "voice_id": "emma"}))
        
        data = await receive_json(ws)
        print(f"   Received: {data['type']} - Agent: {data.get('agent_name')}, Voice: {data.get('voice_id')}")
        
        # Switch to alex
        print("\n3. Switching voice to: alex")
        await ws.send(json.dumps({"type": "switch_voice", "voice_id": "alex"}))
        
        data = await receive_json(ws)
        print(f"   Received: {data['type']} - Agent: {data.get('agent_name')}, Voice: {data.get('voice_id')}")
        
        # End call
        print("\n4. Ending call")
        await ws.send(json.dumps({"type": "end_call"}))
        
    print("\n✅ Test completed successfully!")

if __name__ == "__main__":
    asyncio.run(test_voice_switching())
