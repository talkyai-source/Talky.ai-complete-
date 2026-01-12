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
_voice_pipelines: dict[str, VoicePipelineService] = {}
_tts_providers: dict[str, CartesiaTTSProvider] = {}


async def _on_sip_call_started(call_id: str) -> None:
    """
    Called when a SIP call is answered.
    Initializes the voice pipeline using GLOBAL config.
    
    Uses the LLM model and TTS voice selected in AI Options.
    """
    from app.domain.services.global_ai_config import get_global_config, get_selected_voice_info
    
    global _sip_gateway, _voice_pipelines, _tts_providers
    
    logger.info(f"Initializing voice pipeline for SIP call {call_id}")
    
    # Get global config (what's selected in AI Options)
    config = get_global_config()
    voice_info = get_selected_voice_info()
    
    try:
        # Initialize STT provider (fixed - Flux)
        stt_provider = DeepgramFluxSTTProvider()
        await stt_provider.initialize({
            "model": "flux-general-en",
            "sample_rate": 16000,
            "encoding": "linear16"
        })
        
        # Initialize LLM provider - FROM GLOBAL CONFIG
        llm_provider = GroqLLMProvider()
        await llm_provider.initialize({
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens
        })
        logger.info(f"SIP call using LLM: {config.llm_model}")
        
        # Initialize TTS provider - FROM GLOBAL CONFIG
        tts_provider = CartesiaTTSProvider()
        await tts_provider.initialize({
            "voice_id": config.tts_voice_id,
            "model_id": config.tts_model,
            "sample_rate": config.tts_sample_rate
        })
        logger.info(f"SIP call using TTS voice: {voice_info.get('name', 'Unknown')}")
        _tts_providers[call_id] = tts_provider
        
        # Create voice pipeline
        pipeline = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=_sip_gateway
        )
        _voice_pipelines[call_id] = pipeline
        
        # Create session
        session = CallSession(
            call_id=call_id,
            state=CallState.ACTIVE
        )
        _active_sessions[call_id] = session
        
        # Start processing audio from SIP
        asyncio.create_task(_process_sip_audio(call_id, session, pipeline))
        
        # Send greeting
        asyncio.create_task(_send_sip_greeting(call_id, tts_provider))
        
        logger.info(f"Voice pipeline started for SIP call {call_id}")
        
    except Exception as e:
        logger.error(f"Failed to initialize pipeline for call {call_id}: {e}")


async def _send_sip_greeting(call_id: str, tts_provider: CartesiaTTSProvider) -> None:
    """Send the AI greeting to the SIP caller using global config voice."""
    from app.domain.services.global_ai_config import get_global_config, get_selected_voice_info
    
    global _sip_server
    
    config = get_global_config()
    voice_info = get_selected_voice_info()
    
    try:
        # Use voice name from global config
        voice_name = voice_info.get("name", "Your AI Assistant")
        greeting = f"Hello! This is {voice_name} from Talky AI. How can I help you today?"
        
        # Synthesize greeting with global config voice
        async for chunk in tts_provider.stream_synthesize(
            text=greeting,
            voice_id=config.tts_voice_id,
            sample_rate=config.tts_sample_rate
        ):
            if _sip_server and chunk.data:
                await _sip_server.send_rtp_audio(call_id, chunk.data)
        
        logger.info(f"Sent greeting to SIP call {call_id} using voice: {voice_name}")
        
    except Exception as e:
        logger.error(f"Failed to send greeting for call {call_id}: {e}")


async def _process_sip_audio(
    call_id: str, 
    session: CallSession, 
    pipeline: VoicePipelineService
) -> None:
    """Process incoming SIP audio through the voice pipeline."""
    global _sip_gateway, _sip_server, _tts_providers
    
    if not _sip_gateway:
        return
    
    audio_queue = _sip_gateway.get_audio_queue(call_id)
    if not audio_queue:
        return
    
    logger.info(f"Starting audio processing for SIP call {call_id}")
    
    try:
        # Start the pipeline
        await pipeline.start_pipeline(session)
        
        # Process audio from the queue
        while call_id in _active_sessions:
            try:
                audio_data = await asyncio.wait_for(audio_queue.get(), timeout=1.0)
                
                # Feed audio to the pipeline's audio queue
                if hasattr(pipeline, '_audio_queue'):
                    await pipeline._audio_queue.put(audio_data)
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Audio processing error for call {call_id}: {e}")
                break
                
    except Exception as e:
        logger.error(f"Pipeline error for call {call_id}: {e}")
    finally:
        logger.info(f"Audio processing stopped for SIP call {call_id}")


async def _on_sip_audio(call_id: str, audio_data: bytes) -> None:
    """
    Callback when audio is received from SIP phone.
    Forwards to SIPMediaGateway for processing.
    """
    global _sip_gateway
    
    if _sip_gateway:
        await _sip_gateway.on_audio_received(call_id, audio_data)


async def _on_sip_call_ended(call_id: str) -> None:
    """
    Called when a SIP call ends.
    Cleans up the voice pipeline.
    """
    global _voice_pipelines, _tts_providers, _active_sessions
    
    logger.info(f"Cleaning up voice pipeline for SIP call {call_id}")
    
    # Stop pipeline
    if call_id in _voice_pipelines:
        pipeline = _voice_pipelines.pop(call_id)
        await pipeline.stop_pipeline(call_id)
    
    # Cleanup TTS
    if call_id in _tts_providers:
        tts = _tts_providers.pop(call_id)
        await tts.cleanup()
    
    # Remove session
    _active_sessions.pop(call_id, None)


async def start_sip_bridge(host: str = "0.0.0.0", port: int = 5060) -> None:
    """
    Start the SIP bridge server with full voice pipeline integration.
    
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
    
    # Create SIP server with callbacks
    _sip_server = SIPBridgeServer(
        host=host,
        sip_port=port,
        rtp_port_start=10000,
        on_audio_callback=_on_sip_audio
    )
    
    # Register call lifecycle callbacks
    _sip_server.on_call_started = _on_sip_call_started
    _sip_server.on_call_ended = _on_sip_call_ended
    
    # Start server in background task
    asyncio.create_task(_sip_server.start())
    
    logger.info(f"SIP bridge started on {host}:{port} with voice pipeline integration")


async def stop_sip_bridge() -> None:
    """Stop the SIP bridge server and cleanup all pipelines."""
    global _sip_server, _sip_gateway, _voice_pipelines, _tts_providers
    
    # Cleanup all active pipelines
    for call_id in list(_voice_pipelines.keys()):
        await _on_sip_call_ended(call_id)
    
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
