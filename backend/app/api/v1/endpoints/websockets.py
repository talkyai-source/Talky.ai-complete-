"""
WebSocket Endpoints
Handles real-time voice streaming with full AI pipeline integration

Day 16: Added query parameter handling, call record creation, and session close updates.
Works with all providers: Vonage, RTP (MicroSIP/Asterisk), and Browser.
"""
import os
import uuid
import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from datetime import datetime

from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.telephony.vonage_media_gateway import VonageMediaGateway
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.domain.services.recording_service import RecordingService
from app.api.v1.dependencies import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websockets"])


# Initialize providers (singleton pattern)
# In production, these would be managed by a DI container
_media_gateway = None
_stt_provider = None
_llm_provider = None
_tts_provider = None
_pipeline_service = None


async def get_providers():
    """Get or initialize providers"""
    global _media_gateway, _stt_provider, _llm_provider, _tts_provider, _pipeline_service
    
    if not _media_gateway:
        # Initialize media gateway
        _media_gateway = VonageMediaGateway()
        await _media_gateway.initialize({
            "sample_rate": 16000,
            "channels": 1,
            "max_queue_size": 100
        })
        
        # Initialize STT provider
        _stt_provider = DeepgramFluxSTTProvider()
        await _stt_provider.initialize({
            "api_key": os.getenv("DEEPGRAM_API_KEY"),
            "model": "flux-general-en",
            "sample_rate": 16000,
            "encoding": "linear16"
        })
        
        # Initialize LLM provider
        _llm_provider = GroqLLMProvider()
        await _llm_provider.initialize({
            "api_key": os.getenv("GROQ_API_KEY"),
            "model": "llama-3.1-8b-instant",
            "temperature": 0.7,
            "max_tokens": 150
        })
        
        # Initialize TTS provider
        _tts_provider = CartesiaTTSProvider()
        await _tts_provider.initialize({
            "api_key": os.getenv("CARTESIA_API_KEY"),
            "model_id": "sonic-3",
            "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
            "sample_rate": 16000
        })
        
        # Initialize pipeline service
        _pipeline_service = VoicePipelineService(
            stt_provider=_stt_provider,
            llm_provider=_llm_provider,
            tts_provider=_tts_provider,
            media_gateway=_media_gateway
        )
    
    return _media_gateway, _pipeline_service


async def create_or_link_call_record(
    call_id: str,
    tenant_id: str,
    campaign_id: str,
    lead_id: str,
    phone_number: str,
    external_call_uuid: str = None
) -> bool:
    """
    Create or link a call record at session start.
    
    Works with all providers (Vonage, RTP/MicroSIP, Browser).
    
    Args:
        call_id: Our internal call UUID (database primary key)
        tenant_id: Tenant UUID
        campaign_id: Campaign UUID
        lead_id: Lead UUID
        phone_number: Phone number (required by schema)
        external_call_uuid: Provider-specific ID (Vonage UUID, SIP Call-ID, etc.)
    
    Returns:
        True if call record created/linked successfully
    """
    try:
        supabase = next(get_supabase())
        
        # Check if call already exists (for linking existing calls)
        existing = supabase.table("calls").select("id").eq("id", call_id).execute()
        
        if existing.data:
            # Update existing call to active status
            supabase.table("calls").update({
                "status": "active",
                "started_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            logger.info(f"Linked to existing call: {call_id}")
        else:
            # Create new call record
            call_data = {
                "id": call_id,
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "phone_number": phone_number,
                "external_call_uuid": external_call_uuid,
                "status": "active",
                "started_at": datetime.utcnow().isoformat(),
                "created_at": datetime.utcnow().isoformat()
            }
            supabase.table("calls").insert(call_data).execute()
            logger.info(
                f"Created call record: {call_id} "
                f"(tenant: {tenant_id}, campaign: {campaign_id}, lead: {lead_id})"
            )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to create/link call record: {e}", exc_info=True)
        return False


@router.websocket("/ws/voice/{external_uuid}")
async def voice_stream(websocket: WebSocket, external_uuid: str):
    """
    WebSocket endpoint for bidirectional voice streaming.
    
    Works with all providers: Vonage, RTP (MicroSIP/Asterisk), and Browser.
    
    Path Parameters:
        external_uuid: Provider-specific call ID (Vonage UUID, SIP Call-ID, etc.)
    
    Query Parameters (required):
        tenant_id: Tenant UUID
        campaign_id: Campaign UUID
        lead_id: Lead UUID
    
    Query Parameters (optional):
        call_id: Our internal call UUID (if linking to existing call)
        phone_number: Phone number (defaults to "unknown")
    
    Handles:
        - Audio input (PCM 16kHz mono)
        - Session management with call record creation
        - Voice pipeline orchestration (STT → LLM → TTS)
        - Audio output back to provider
        - Call record update on close (status, duration, transcript)
    """
    await websocket.accept()
    
    # Parse query parameters
    query_params = dict(websocket.query_params)
    tenant_id = query_params.get("tenant_id")
    campaign_id = query_params.get("campaign_id")
    lead_id = query_params.get("lead_id")
    call_id_param = query_params.get("call_id")
    phone_number = query_params.get("phone_number", "unknown")
    
    # Validate required parameters
    if not all([tenant_id, campaign_id, lead_id]):
        logger.warning(
            f"Missing required query params for WebSocket: {external_uuid}",
            extra={"query_params": query_params}
        )
        await websocket.send_json({
            "type": "error",
            "message": "Missing required query parameters: tenant_id, campaign_id, lead_id"
        })
        await websocket.close(code=4000)
        return
    
    # Generate internal call_id or use provided one
    call_id = call_id_param or str(uuid.uuid4())
    
    logger.info(
        f"WebSocket connection accepted: call_id={call_id}, external_uuid={external_uuid}",
        extra={
            "call_id": call_id,
            "external_uuid": external_uuid,
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id
        }
    )
    
    # Create call record in database
    call_created = await create_or_link_call_record(
        call_id=call_id,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        phone_number=phone_number,
        external_call_uuid=external_uuid
    )
    
    if not call_created:
        logger.warning(f"Call record creation failed for {call_id} - continuing anyway")
    
    # Get providers
    media_gateway, pipeline_service = await get_providers()
    
    # Create call session with real values (no placeholders)
    session = CallSession(
        call_id=call_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        tenant_id=tenant_id,
        vonage_call_uuid=external_uuid,
        system_prompt="You are a helpful voice assistant. Keep responses brief and conversational, under 2 sentences.",
        voice_id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        language="en"
    )
    
    # Initialize session in media gateway
    await media_gateway.on_call_started(call_id, {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "phone_number": phone_number,
        "external_uuid": external_uuid,
        "started_at": datetime.utcnow().isoformat()
    })
    
    # Start voice pipeline in background
    pipeline_task = asyncio.create_task(
        pipeline_service.start_pipeline(session, websocket)
    )
    
    # Start output audio sender in background
    output_task = asyncio.create_task(
        send_output_audio(websocket, media_gateway, call_id)
    )
    
    try:
        while True:
            # Receive audio data from Vonage
            audio_data = await websocket.receive_bytes()
            
            # Pass to media gateway for validation and buffering
            await media_gateway.on_audio_received(call_id, audio_data)
    
    except WebSocketDisconnect:
        logger.info(
            f"WebSocket disconnected for call {call_id}",
            extra={"call_id": call_id}
        )
    
    except Exception as e:
        logger.error(
            f"WebSocket error for call {call_id}: {e}",
            extra={"call_id": call_id, "error": str(e)},
            exc_info=True
        )
    
    finally:
        # Stop pipeline
        await pipeline_service.stop_pipeline(call_id)
        
        # End call in media gateway
        await media_gateway.on_call_ended(call_id, "websocket_closed")
        
        # Cancel background tasks
        pipeline_task.cancel()
        output_task.cancel()
        
        try:
            await pipeline_task
        except asyncio.CancelledError:
            pass
        
        try:
            await output_task
        except asyncio.CancelledError:
            pass
        
        logger.info(
            f"Call {call_id} cleanup complete",
            extra={"call_id": call_id}
        )
        
        # Day 10: Save recording and transcript (async background task)
        asyncio.create_task(
            _save_call_data(
                call_id=call_id,
                gateway=media_gateway,
                pipeline=pipeline_service,
                session=session
            )
        )


async def send_output_audio(
    websocket: WebSocket,
    media_gateway: VonageMediaGateway,
    call_id: str
):
    """
    Send output audio from TTS back to Vonage via WebSocket.
    
    Args:
        websocket: WebSocket connection
        media_gateway: Media gateway instance
        call_id: Call identifier
    """
    output_queue = media_gateway.get_output_queue(call_id)
    
    if not output_queue:
        logger.warning(
            f"No output queue for call {call_id}",
            extra={"call_id": call_id}
        )
        return
    
    try:
        while True:
            # Get audio chunk from output queue
            audio_chunk = await asyncio.wait_for(
                output_queue.get(),
                timeout=0.1
            )
            
            # Send to Vonage via WebSocket
            await websocket.send_bytes(audio_chunk)
    
    except asyncio.TimeoutError:
        # No audio available, continue
        await asyncio.sleep(0.01)
    
    except Exception as e:
        logger.error(
            f"Error sending output audio: {e}",
            extra={"call_id": call_id, "error": str(e)}
        )


async def _save_call_data(
    call_id: str,
    gateway: VonageMediaGateway,
    pipeline: VoicePipelineService,
    session: CallSession
) -> None:
    """
    Save recording, transcript, and update call record after call ends.
    
    This is a background task that runs after the WebSocket closes.
    Works with all providers: Vonage, RTP, and Browser.
    
    Day 16: Added call record update with ended_at, duration, status, transcript, cost.
    
    Args:
        call_id: Unique call identifier
        gateway: Media gateway instance (Vonage, RTP, or Browser)
        pipeline: Voice pipeline service
        session: Call session with metadata
    """
    try:
        # Get Supabase client
        supabase = next(get_supabase())
        
        # Get tenant and campaign info from session
        tenant_id = getattr(session, 'tenant_id', None) or 'default'
        campaign_id = str(session.campaign_id) if session.campaign_id else 'unknown'
        
        # Calculate duration and cost
        duration_seconds = int(session.get_duration_seconds())
        cost = round(duration_seconds * 0.001, 4)  # ~$0.001/second
        
        # Get transcript text for call record
        full_transcript = None
        try:
            full_transcript = pipeline.transcript_service.get_transcript_text(call_id)
        except Exception:
            pass
        
        # 1. Save recording
        buffer = gateway.get_recording_buffer(call_id)
        if buffer and hasattr(buffer, 'total_bytes') and buffer.total_bytes > 0:
            logger.info(
                f"Saving recording for call {call_id}: {buffer.total_bytes} bytes"
            )
            
            recording_service = RecordingService(supabase)
            recording_id = await recording_service.save_and_link(
                call_id=call_id,
                buffer=buffer,
                tenant_id=tenant_id,
                campaign_id=campaign_id
            )
            
            if recording_id:
                logger.info(f"Recording saved: {recording_id}")
            
            # Clear buffer to free memory
            gateway.clear_recording_buffer(call_id)
        else:
            logger.debug(f"No recording buffer for call {call_id}")
        
        # 2. Save transcript to transcripts table
        transcript_id = await pipeline.transcript_service.save_transcript(
            call_id=call_id,
            supabase_client=supabase,
            tenant_id=tenant_id
        )
        
        if transcript_id:
            logger.info(f"Transcript saved: {transcript_id}")
        
        # Clear transcript buffer
        pipeline.transcript_service.clear_buffer(call_id)
        
        # 3. Update call record with final status (Day 16)
        try:
            update_data = {
                "status": "completed",
                "ended_at": datetime.utcnow().isoformat(),
                "duration_seconds": duration_seconds,
                "cost": cost,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            # Add transcript if available
            if full_transcript:
                update_data["transcript"] = full_transcript
            
            supabase.table("calls").update(update_data).eq("id", call_id).execute()
            logger.info(
                f"Call record updated: {call_id} "
                f"(duration: {duration_seconds}s, cost: ${cost})"
            )
        except Exception as e:
            logger.warning(f"Could not update call record for {call_id}: {e}")
        
        logger.info(f"Call data saved successfully for {call_id}")
        
    except Exception as e:
        logger.error(
            f"Failed to save call data for {call_id}: {e}",
            exc_info=True
        )

