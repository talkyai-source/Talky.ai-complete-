"""
SIP Bridge WebSocket Endpoint
Connects SIP bridge server to existing voice pipeline.

Day 18: Enables MicroSIP to interact with Talky.ai voice agent.

Usage:
    1. Start FastAPI server: uvicorn app.main:app
    2. SIP bridge auto-starts and listens on port 5060
    3. Configure MicroSIP to connect to localhost:5060
    4. Call any extension → AI agent answers
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import JSONResponse

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.infrastructure.telephony.sip_bridge_server import SIPBridgeServer
from app.infrastructure.telephony.sip_media_gateway import SIPMediaGateway
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.services.voice_pipeline_service import VoicePipelineService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip", tags=["SIP Bridge"])

# Global SIP server instance
_sip_server: Optional[SIPBridgeServer] = None
_sip_gateway: Optional[SIPMediaGateway] = None
_active_sessions: dict[str, CallSession] = {}


async def _on_sip_audio(call_id: str, audio_data: bytes) -> None:
    """
    Callback when audio is received from SIP phone.
    Forwards to SIPMediaGateway for processing.
    """
    global _sip_gateway
    
    if _sip_gateway:
        await _sip_gateway.on_audio_received(call_id, audio_data)


async def start_sip_bridge(host: str = "0.0.0.0", port: int = 5060) -> None:
    """
    Start the SIP bridge server.
    
    Called during application startup.
    """
    global _sip_server, _sip_gateway
    
    # Initialize gateway
    _sip_gateway = SIPMediaGateway()
    await _sip_gateway.initialize({
        "audio": {
            "input_sample_rate": 8000,
            "output_sample_rate": 16000
        }
    })
    
    # Create SIP server with audio callback
    _sip_server = SIPBridgeServer(
        host=host,
        sip_port=port,
        rtp_port_start=10000,
        on_audio_callback=_on_sip_audio
    )
    
    # Start server in background task
    asyncio.create_task(_sip_server.start())
    
    logger.info(f"SIP bridge started on {host}:{port}")


async def stop_sip_bridge() -> None:
    """Stop the SIP bridge server."""
    global _sip_server, _sip_gateway
    
    if _sip_server:
        await _sip_server.stop()
        _sip_server = None
    
    if _sip_gateway:
        await _sip_gateway.cleanup()
        _sip_gateway = None
    
    logger.info("SIP bridge stopped")


@router.get("/status")
async def get_sip_status():
    """
    Get SIP bridge status.
    
    Returns:
        Status information about the SIP bridge
    """
    global _sip_server, _sip_gateway
    
    if not _sip_server:
        return JSONResponse({
            "status": "stopped",
            "message": "SIP bridge is not running"
        })
    
    active_calls = len(_sip_server._calls) if _sip_server else 0
    
    return JSONResponse({
        "status": "running",
        "sip_port": _sip_server.sip_port,
        "host": _sip_server.host,
        "active_calls": active_calls,
        "active_sessions": len(_active_sessions),
        "rtp_ports_in_use": list(_sip_server._rtp_sockets.keys()) if _sip_server else []
    })


@router.post("/start")
async def start_sip_server(
    host: str = Query(default="0.0.0.0", description="Host to bind to"),
    port: int = Query(default=5060, description="SIP port")
):
    """
    Start the SIP bridge server.
    
    Args:
        host: Host address to bind to
        port: SIP port (default 5060)
    """
    global _sip_server
    
    if _sip_server and _sip_server._running:
        return JSONResponse({
            "status": "already_running",
            "message": f"SIP bridge already running on port {_sip_server.sip_port}"
        })
    
    try:
        await start_sip_bridge(host, port)
        return JSONResponse({
            "status": "started",
            "message": f"SIP bridge started on {host}:{port}",
            "sip_port": port,
            "instructions": {
                "microsip": f"Configure SIP Server: {host}:{port}",
                "username": "any (e.g., agent001)",
                "call_to": "any extension to invoke AI agent"
            }
        })
    except Exception as e:
        logger.error(f"Failed to start SIP bridge: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_sip_server():
    """Stop the SIP bridge server."""
    global _sip_server
    
    if not _sip_server or not _sip_server._running:
        return JSONResponse({
            "status": "not_running",
            "message": "SIP bridge is not running"
        })
    
    await stop_sip_bridge()
    
    return JSONResponse({
        "status": "stopped",
        "message": "SIP bridge stopped"
    })


@router.get("/calls")
async def list_active_calls():
    """
    List all active SIP calls.
    
    Returns:
        List of active call details
    """
    global _sip_server
    
    if not _sip_server:
        return JSONResponse({"calls": []})
    
    calls = []
    for call_id, call in _sip_server._calls.items():
        calls.append({
            "call_id": call_id,
            "from": call.from_uri,
            "to": call.to_uri,
            "state": call.state,
            "duration_seconds": (datetime.utcnow() - call.created_at).seconds,
            "rtp_port": call.rtp_session.local_rtp_port if call.rtp_session else None
        })
    
    return JSONResponse({"calls": calls, "count": len(calls)})


@router.websocket("/audio/{call_id}")
async def sip_audio_websocket(
    websocket: WebSocket,
    call_id: str
):
    """
    WebSocket endpoint for SIP audio bridging.
    
    This allows external tools to connect to a SIP call's audio stream
    for custom processing or monitoring.
    
    Args:
        call_id: SIP call identifier
    """
    global _sip_gateway
    
    await websocket.accept()
    
    logger.info(f"SIP audio WebSocket connected for call {call_id}")
    
    try:
        if not _sip_gateway:
            await websocket.close(code=1011, reason="SIP gateway not running")
            return
        
        audio_queue = _sip_gateway.get_audio_queue(call_id)
        if not audio_queue:
            await websocket.close(code=1008, reason="Call not found")
            return
        
        # Stream audio to WebSocket client
        while True:
            try:
                audio = await asyncio.wait_for(audio_queue.get(), timeout=1.0)
                await websocket.send_bytes(audio)
            except asyncio.TimeoutError:
                # Check if still active
                if not _sip_gateway.is_session_active(call_id):
                    break
            except WebSocketDisconnect:
                break
    
    except Exception as e:
        logger.error(f"SIP audio WebSocket error: {e}")
    
    finally:
        logger.info(f"SIP audio WebSocket disconnected for call {call_id}")
