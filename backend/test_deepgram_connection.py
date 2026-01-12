"""Test Deepgram Flux WebSocket connection"""
import asyncio
import os
import websockets
from dotenv import load_dotenv

load_dotenv()

async def test_connection():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("ERROR: DEEPGRAM_API_KEY not found in .env")
        return
    
    url = "wss://api.deepgram.com/v2/listen?model=flux-general-en&encoding=linear16&sample_rate=16000"
    headers = {"Authorization": f"Token {api_key}"}
    
    print(f"Connecting to Deepgram Flux...")
    print(f"URL: {url}")
    
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                url, 
                additional_headers=headers,
                open_timeout=30,
                ping_interval=20
            ),
            timeout=35
        )
        print("✅ WebSocket connected successfully!")
        
        # Send a silent audio frame to keep connection alive
        silent_audio = bytes(3200)  # 100ms of silence at 16kHz
        await ws.send(silent_audio)
        print("✅ Sent silent audio frame")
        
        # Wait for response
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"✅ Received response: {response[:100]}...")
        except asyncio.TimeoutError:
            print("⚠️ No response received (normal if no speech)")
        
        await ws.close()
        print("✅ Connection closed cleanly")
        
    except asyncio.TimeoutError:
        print("❌ Connection timed out during handshake")
        print("\nPossible causes:")
        print("1. Firewall blocking WebSocket connections")
        print("2. VPN interfering with TLS")
        print("3. Network congestion")
        print("4. Deepgram service issue")
        
    except Exception as e:
        print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
