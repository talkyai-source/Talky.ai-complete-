"""
WebSocket Endpoints
Handles real-time voice streaming
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websockets"])


@router.websocket("/ws/voice/{call_id}")
async def voice_stream(websocket: WebSocket, call_id: str):
    """
    WebSocket endpoint for bidirectional voice streaming
    """
    await websocket.accept()
    
    try:
        while True:
            # Receive audio data
            data = await websocket.receive_bytes()
            
            # TODO: Process audio through AI pipeline
            # 1. Send to STT
            # 2. Get transcript
            # 3. Send to LLM
            # 4. Get response
            # 5. Send to TTS
            # 6. Send audio back
            
            # Echo back for now
            await websocket.send_bytes(data)
            
    except WebSocketDisconnect:
        print(f"WebSocket disconnected for call {call_id}")
