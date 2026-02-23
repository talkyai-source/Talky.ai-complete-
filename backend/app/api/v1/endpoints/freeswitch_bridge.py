"""
FreeSWITCH SIP Bridge Endpoint
API endpoints for controlling SIP calls via FreeSWITCH ESL.

Day 43: Cleaned for Linux-only deployment.
        - Removed Docker CLI workaround (Windows-only)
        - Removed record-then-process AIConversationController (Windows-only)
        - ESL socket is the only supported mode

Usage:
    POST /api/v1/sip/freeswitch/start  - Start FreeSWITCH client
    POST /api/v1/sip/freeswitch/stop   - Stop FreeSWITCH client
    POST /api/v1/sip/freeswitch/call   - Make outbound call
    GET  /api/v1/sip/freeswitch/status - Get registration status
    WS   /ws/freeswitch-audio/{uuid}   - Audio WebSocket endpoint
"""
import asyncio
import logging
import os
import wave
from typing import Optional, Literal
from datetime import datetime

from fastapi import APIRouter, WebSocket, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.infrastructure.telephony.freeswitch_esl import (
    FreeSwitchESL,
    ESLConfig,
    CallInfo,
    TransferRequest,
    TransferMode,
    TransferLeg,
    TransferStatus,
)
from app.infrastructure.telephony.freeswitch_audio_bridge import (
    FreeSwitchAudioBridge,
    get_audio_bridge,
)
from app.domain.services.voice_orchestrator import (
    VoiceOrchestrator,
    VoiceSessionConfig,
    VoiceSession,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/freeswitch", tags=["FreeSWITCH SIP Bridge"])

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_esl_client: Optional[FreeSwitchESL] = None
_audio_bridge: Optional[FreeSwitchAudioBridge] = None

# VoiceSessions keyed by FreeSWITCH call_uuid — provider lifecycle is
# managed by the VoiceOrchestrator; we just keep a handle here for lookup.
_freeswitch_sessions: dict[str, VoiceSession] = {}

# Audio files directory (Linux path)
_AUDIO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "audio_files")
)


# ---------------------------------------------------------------------------
# Helpers — VoiceOrchestrator integration
# ---------------------------------------------------------------------------

def _get_orchestrator() -> VoiceOrchestrator:
    """Retrieve the VoiceOrchestrator singleton from the DI container."""
    from app.core.container import get_container
    return get_container().voice_orchestrator


def _build_freeswitch_session_config() -> VoiceSessionConfig:
    """Build a VoiceSessionConfig tuned for FreeSWITCH telephony (8 kHz)."""
    from app.domain.services.global_ai_config import get_global_config

    config = get_global_config()

    tts_voice_id = config.tts_voice_id
    if not tts_voice_id.startswith("aura-"):
        tts_voice_id = "aura-asteria-en"

    return VoiceSessionConfig(
        gateway_type="browser",
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type="deepgram",
        stt_model="nova-2",
        stt_sample_rate=8000,
        stt_encoding="linear16",
        llm_model=config.llm_model,
        llm_temperature=config.llm_temperature,
        llm_max_tokens=config.llm_max_tokens,
        voice_id=tts_voice_id,
        tts_sample_rate=8000,
        gateway_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        session_type="freeswitch",
        campaign_id="freeswitch",
        lead_id="sip-caller",
    )


# ---------------------------------------------------------------------------
# Voice pipeline lifecycle
# ---------------------------------------------------------------------------

async def _initialize_voice_pipeline(call_uuid: str) -> None:
    """Create a VoiceSession via the orchestrator & start audio fork.

    Order matters: the VoiceSession MUST exist in ``_freeswitch_sessions``
    before ``uuid_audio_fork`` fires, because the audio-bridge callbacks
    look the session up by *call_uuid*.
    """
    from app.domain.services.global_ai_config import get_selected_voice_info

    logger.info(f"🤖 Initializing AI pipeline for call {call_uuid[:8]}")

    try:
        # Step 1: Create voice session FIRST (before audio fork)
        orchestrator = _get_orchestrator()
        config = _build_freeswitch_session_config()
        voice_session = await orchestrator.create_voice_session(config)
        _freeswitch_sessions[call_uuid] = voice_session

        # Step 2: Start audio fork — FreeSWITCH will connect the WebSocket,
        #         which triggers _on_freeswitch_session_start → pipeline start
        await _start_audio_fork(call_uuid)

        # Step 3: Play file-based greeting (runs in parallel with pipeline)
        voice_info = get_selected_voice_info()
        await _send_ai_greeting(call_uuid, voice_session, voice_info)

        logger.info(f"✓ AI pipeline ready for {call_uuid[:8]}")
    except Exception as e:
        logger.error(f"Failed to initialize AI pipeline: {e}", exc_info=True)


async def _start_audio_fork(call_uuid: str) -> None:
    """Start streaming call audio to our WebSocket via uuid_audio_fork."""
    global _esl_client
    if not _esl_client:
        logger.error("ESL client not available for audio_fork")
        return

    # Build WebSocket URL for this call
    ws_url = f"ws://127.0.0.1:8000/api/v1/sip/freeswitch/audio/{call_uuid}"
    logger.info(f"🔊 Starting audio_fork for {call_uuid[:8]} → {ws_url}")

    try:
        # uuid_audio_fork <uuid> start <ws-url> mono 8000
        result = await _esl_client.api(
            f"uuid_audio_fork {call_uuid} start {ws_url} mono 8000"
        )
        if "+OK" in result or "Success" in result:
            logger.info(f"✓ Audio fork started for {call_uuid[:8]}")
        else:
            logger.warning(f"Audio fork result for {call_uuid[:8]}: {result}")
    except Exception as e:
        logger.error(f"Failed to start audio fork: {e}")


async def _send_ai_greeting(
    call_uuid: str,
    voice_session: VoiceSession,
    voice_info: dict,
) -> None:
    """Synthesize a greeting via the session's TTS provider and play it
    through FreeSWITCH (file-based playback)."""
    global _esl_client

    if not _esl_client:
        return

    try:
        voice_name = voice_info.get("name", "Your AI Assistant")
        greeting = f"Hello! This is {voice_name} from Talky AI. How can I help you today?"

        tts_provider = voice_session.tts_provider
        voice_id = (
            voice_session.config.voice_id if voice_session.config else "aura-asteria-en"
        )

        logger.info(f"🎤 Generating greeting for {call_uuid[:8]}")

        audio_data = await tts_provider.synthesize_raw(
            text=greeting,
            voice_id=voice_id,
            sample_rate=8000,
        )

        os.makedirs(_AUDIO_DIR, exist_ok=True)

        raw_path = os.path.join(_AUDIO_DIR, f"{call_uuid}_greeting.raw")
        wav_path = raw_path.replace(".raw", ".wav")

        with open(raw_path, "wb") as f:
            f.write(audio_data)

        _write_wav(raw_path, wav_path, 8000)

        await _esl_client.play_audio(
            call_uuid,
            f"/var/lib/freeswitch/sounds/custom/{call_uuid}_greeting.wav",
        )

        logger.info(f"✓ Greeting playing for {call_uuid[:8]}")
    except Exception as e:
        logger.error(f"Failed to send greeting: {e}", exc_info=True)


async def _generate_greeting_file(
    call_id: str,
    greeting: str | None = None,
) -> str | None:
    """Generate a TTS greeting with the orchestrator and return the WAV path."""
    try:
        orchestrator = _get_orchestrator()
        config = _build_freeswitch_session_config()
        voice_session = await orchestrator.create_voice_session(config)

        if not greeting:
            greeting = "Hello! This is your AI assistant from Talky. How can I help you today?"

        tts_provider = voice_session.tts_provider
        voice_id = (
            voice_session.config.voice_id if voice_session.config else "aura-asteria-en"
        )

        audio_data = await tts_provider.synthesize_raw(
            text=greeting,
            voice_id=voice_id,
            sample_rate=8000,
        )

        # We only needed TTS — tear the session back down.
        await orchestrator.end_session(voice_session)

        os.makedirs(_AUDIO_DIR, exist_ok=True)
        wav_path = os.path.join(_AUDIO_DIR, f"greeting_{call_id}.wav")
        raw_path = wav_path.replace(".wav", ".raw")

        with open(raw_path, "wb") as f:
            f.write(audio_data)

        _write_wav(raw_path, wav_path, 8000)
        os.remove(raw_path)  # raw file no longer needed

        logger.info(f"🎤 Generated greeting: {wav_path}")
        return wav_path
    except Exception as e:
        logger.error(f"Failed to generate greeting: {e}", exc_info=True)
        return None


async def _cleanup_call(call_uuid: str) -> None:
    """Tear down the voice session and delete temp audio files."""
    logger.info(f"🧹 Cleaning up call {call_uuid[:8]}")

    voice_session = _freeswitch_sessions.pop(call_uuid, None)
    if voice_session:
        try:
            orchestrator = _get_orchestrator()
            await orchestrator.end_session(voice_session)
        except Exception:
            pass

    # Remove leftover audio artefacts
    try:
        for ext in (".raw", ".wav"):
            path = os.path.join(_AUDIO_DIR, f"{call_uuid}_greeting{ext}")
            if os.path.exists(path):
                os.remove(path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Audio bridge callbacks — wire FreeSWITCH audio into the voice pipeline
# ---------------------------------------------------------------------------

async def _on_freeswitch_session_start(call_uuid: str) -> None:
    """Called when FreeSWITCH WebSocket connects (mod_audio_fork).

    Wires the BrowserMediaGateway with the bridge's WebSocket and starts
    the voice pipeline (STT → LLM → TTS continuous loop).
    """
    global _audio_bridge

    voice_session = _freeswitch_sessions.get(call_uuid)
    if not voice_session:
        # Race: audio fork connected before session was stored — wait briefly
        for _ in range(20):  # up to ~1 s
            await asyncio.sleep(0.05)
            voice_session = _freeswitch_sessions.get(call_uuid)
            if voice_session:
                break

    if not voice_session:
        logger.error(f"No voice session for audio bridge start: {call_uuid[:8]}")
        return

    # Get the WebSocket that FreeSWITCH opened to us
    bridge_ws = _audio_bridge.get_websocket(call_uuid) if _audio_bridge else None
    if not bridge_ws:
        logger.error(f"No bridge websocket for {call_uuid[:8]}")
        return

    try:
        # Register the bridge WebSocket with the media gateway so TTS output
        # flows back to FreeSWITCH through this same WebSocket.
        await voice_session.media_gateway.on_call_started(
            voice_session.call_id, {"websocket": bridge_ws}
        )

        # Start the voice pipeline as a background task.
        # websocket=None → no JSON status messages (FreeSWITCH is audio-only)
        if voice_session.pipeline:
            async def _run_pipeline():
                try:
                    await voice_session.pipeline.start_pipeline(
                        voice_session.call_session, None
                    )
                except Exception as e:
                    logger.error(
                        f"Pipeline error for {call_uuid[:8]}: {e}", exc_info=True
                    )

            voice_session.pipeline_task = asyncio.create_task(_run_pipeline())
            logger.info(f"🚀 Voice pipeline started for {call_uuid[:8]}")
        else:
            logger.error(f"No pipeline on voice session for {call_uuid[:8]}")

    except Exception as e:
        logger.error(f"Failed to start pipeline on bridge connect: {e}", exc_info=True)


async def _on_freeswitch_audio(call_uuid: str, audio_bytes: bytes) -> None:
    """Called for every audio chunk from FreeSWITCH (caller's speech).

    Routes the raw PCM audio into the media gateway's input queue so the
    STT provider can pick it up.
    """
    voice_session = _freeswitch_sessions.get(call_uuid)
    if not voice_session:
        return

    try:
        await voice_session.media_gateway.on_audio_received(
            voice_session.call_id, audio_bytes
        )
    except Exception as e:
        logger.warning(f"Audio route error for {call_uuid[:8]}: {e}")


async def _on_freeswitch_session_end(call_uuid: str) -> None:
    """Called when the FreeSWITCH audio WebSocket disconnects."""
    logger.info(f"🔌 Audio bridge session ended for {call_uuid[:8]}")
    # Cleanup is handled by _cleanup_call (triggered by CHANNEL_HANGUP)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _write_wav(raw_path: str, wav_path: str, sample_rate: int) -> None:
    """Convert raw 16-bit mono PCM to a WAV file."""
    with open(raw_path, "rb") as f:
        raw_data = f.read()

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(raw_data)


# ============================================================================
# Transfer API models (WS-C)
# ============================================================================

class TransferPayload(BaseModel):
    call_uuid: str = Field(..., description="FreeSWITCH call UUID")
    destination: str = Field(..., description="Transfer destination extension or SIP URI")
    leg: Literal["aleg", "bleg", "both"] = Field(default="aleg")
    context: str = Field(default="default")
    timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    attended_cancel_key: str = Field(default="*")
    attended_complete_key: str = Field(default="#")


class TransferResponse(BaseModel):
    attempt_id: str
    call_uuid: str
    mode: Literal["blind", "attended", "deflect"]
    destination: str
    leg: Literal["aleg", "bleg", "both"]
    status: str
    reason: Optional[str] = None
    command: Optional[str] = None
    started_at: str
    finished_at: Optional[str] = None
    context: str


def _to_transfer_response(result: dict) -> TransferResponse:
    return TransferResponse(
        attempt_id=result["attempt_id"],
        call_uuid=result["uuid"],
        mode=result["mode"],
        destination=result["destination"],
        leg=result["leg"],
        status=result["status"],
        reason=result.get("reason"),
        command=result.get("command"),
        started_at=result["started_at"],
        finished_at=result.get("finished_at"),
        context=result.get("context", "default"),
    )


async def _execute_transfer(mode: TransferMode, payload: TransferPayload) -> TransferResponse:
    global _esl_client
    if not _esl_client or not _esl_client.connected:
        raise HTTPException(status_code=400, detail="FreeSWITCH ESL client not connected")

    request = TransferRequest(
        uuid=payload.call_uuid,
        destination=payload.destination,
        mode=mode,
        leg=TransferLeg(payload.leg),
        context=payload.context,
        timeout_seconds=payload.timeout_seconds,
        attended_cancel_key=payload.attended_cancel_key,
        attended_complete_key=payload.attended_complete_key,
    )
    try:
        result = await _esl_client.request_transfer(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_transfer_response(result.to_dict())


# ============================================================================
# REST endpoints
# ============================================================================

@router.post("/start")
async def start_freeswitch_client(
    host: str = Query(default=None, description="FreeSWITCH ESL host (default: from env)"),
    port: int = Query(default=None, description="FreeSWITCH ESL port (default: from env)"),
    password: str = Query(default=None, description="ESL password (default: from env)"),
):
    """
    Connect to FreeSWITCH via ESL and start handling calls.

    FreeSWITCH must be running and configured with the 3CX gateway.
    """
    global _esl_client, _audio_bridge

    if _esl_client and _esl_client.connected:
        return JSONResponse({
            "status": "already_connected",
            "message": "FreeSWITCH ESL client already connected",
        })

    try:
        esl_host = host or os.getenv("FREESWITCH_ESL_HOST", "127.0.0.1")
        esl_port = port or int(os.getenv("FREESWITCH_ESL_PORT", "8021"))
        esl_password = password or os.getenv("FREESWITCH_ESL_PASSWORD", "ClueCon")

        config = ESLConfig(host=esl_host, port=esl_port, password=esl_password)
        _esl_client = FreeSwitchESL(config)

        _esl_client.on_call_start(_initialize_voice_pipeline)
        _esl_client.on_call_end(_cleanup_call)

        if not await _esl_client.connect():
            raise HTTPException(
                status_code=500, detail="Failed to connect to FreeSWITCH ESL"
            )

        _audio_bridge = get_audio_bridge()

        # Wire audio bridge callbacks so FreeSWITCH audio flows into the
        # voice pipeline (STT → LLM → TTS) and TTS output flows back.
        _audio_bridge.set_session_start_callback(_on_freeswitch_session_start)
        _audio_bridge.set_audio_callback(_on_freeswitch_audio)
        _audio_bridge.set_session_end_callback(_on_freeswitch_session_end)

        gateway_status = await _esl_client.get_gateway_status("3cx-pbx")

        return JSONResponse({
            "status": "connected",
            "message": "Connected to FreeSWITCH ESL",
            "mode": "esl_socket",
            "esl": {"host": esl_host, "port": esl_port},
            "gateway_status": gateway_status[:200] if gateway_status else "Unknown",
        })

    except Exception as e:
        logger.error(f"Failed to start FreeSWITCH client: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_freeswitch_client():
    """Disconnect from FreeSWITCH ESL."""
    global _esl_client

    if not _esl_client:
        return JSONResponse({
            "status": "not_running",
            "message": "FreeSWITCH client not running",
        })

    await _esl_client.disconnect()
    _esl_client = None

    return JSONResponse({
        "status": "stopped",
        "message": "FreeSWITCH client disconnected",
    })


@router.get("/status")
async def get_freeswitch_status():
    """Get FreeSWITCH connection and gateway status."""
    global _esl_client

    if not _esl_client:
        return JSONResponse({
            "status": "not_started",
            "connected": False,
            "message": "FreeSWITCH client not started",
        })

    try:
        sofia_status = await _esl_client.get_sofia_status()
        gateway_status = await _esl_client.get_gateway_status("3cx-pbx")

        registered = "REGED" in gateway_status if gateway_status else False

        return JSONResponse({
            "status": "running",
            "connected": _esl_client.connected,
            "registered": registered,
            "active_calls": len(_esl_client.calls),
            "calls": [
                {
                    "uuid": c.uuid[:8],
                    "caller": c.caller_id,
                    "destination": c.destination,
                    "state": c.state,
                    "direction": c.direction,
                }
                for c in _esl_client.calls.values()
            ],
            "gateway_status": gateway_status[:500] if gateway_status else None,
        })

    except Exception as e:
        return JSONResponse({
            "status": "error",
            "connected": _esl_client.connected,
            "error": str(e),
        })


@router.post("/call")
async def make_freeswitch_call(
    to_extension: str = Query(..., description="Extension to call"),
    caller_id: str = Query(default="1001", description="Caller ID to display"),
    with_greeting: bool = Query(default=True, description="Play AI greeting after connect"),
):
    """Make an outbound call via FreeSWITCH ESL.
    
    The call is originated with &park to keep it alive.
    When the call is answered (CHANNEL_ANSWER event), the ESL event handler
    starts uuid_audio_fork to stream audio to the WebSocket pipeline.
    """
    global _esl_client

    if not _esl_client or not _esl_client.connected:
        raise HTTPException(
            status_code=400, detail="FreeSWITCH ESL client not connected"
        )

    try:
        # Always originate with &park — audio_fork is started via ESL 
        # when the CHANNEL_ANSWER event fires (in _initialize_voice_pipeline)
        call_uuid = await _esl_client.originate_call(
            destination=to_extension,
            gateway="3cx-pbx",
            caller_id=caller_id,
        )

        if not call_uuid:
            raise HTTPException(status_code=500, detail="Failed to originate call")

        return JSONResponse({
            "status": "calling",
            "call_uuid": call_uuid,
            "to_extension": to_extension,
            "mode": "esl_socket",
            "message": f"Calling {to_extension}...",
        })

    except Exception as e:
        logger.error(f"Call origination error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hangup/{call_uuid}")
async def hangup_call(call_uuid: str):
    """Hang up a specific call."""
    global _esl_client

    if not _esl_client:
        raise HTTPException(status_code=400, detail="FreeSWITCH client not connected")

    success = await _esl_client.hangup_call(call_uuid)

    if success:
        return JSONResponse({"status": "ok", "message": f"Call {call_uuid[:8]} hung up"})
    else:
        raise HTTPException(status_code=404, detail="Call not found or already ended")


@router.post("/play/{call_uuid}")
async def play_audio_to_call(
    call_uuid: str,
    text: str = Query(None, description="Text to speak via TTS"),
    file: str = Query(None, description="Audio file path to play"),
):
    """Play TTS or audio file to a call."""
    global _esl_client

    if not _esl_client:
        raise HTTPException(status_code=400, detail="FreeSWITCH client not connected")

    if text:
        voice_session = _freeswitch_sessions.get(call_uuid)
        if not voice_session or not voice_session.tts_provider:
            raise HTTPException(status_code=404, detail="No TTS provider for this call")

        voice_id = (
            voice_session.config.voice_id if voice_session.config else "aura-asteria-en"
        )

        audio_data = await voice_session.tts_provider.synthesize_raw(
            text=text,
            voice_id=voice_id,
            sample_rate=8000,
        )

        os.makedirs(_AUDIO_DIR, exist_ok=True)
        ts = int(datetime.now().timestamp())
        raw_path = os.path.join(_AUDIO_DIR, f"{call_uuid}_{ts}.raw")
        wav_path = raw_path.replace(".raw", ".wav")

        with open(raw_path, "wb") as f:
            f.write(audio_data)

        _write_wav(raw_path, wav_path, 8000)

        await _esl_client.play_audio(
            call_uuid,
            f"/var/lib/freeswitch/sounds/custom/{call_uuid}_{ts}.wav",
        )

        return JSONResponse({"status": "playing", "text": text[:50]})

    elif file:
        await _esl_client.play_audio(call_uuid, file)
        return JSONResponse({"status": "playing", "file": file})

    else:
        raise HTTPException(status_code=400, detail="Either text or file required")


# ============================================================================
# WS-C Transfer endpoints
# ============================================================================

@router.post("/transfer/blind", response_model=TransferResponse)
async def transfer_blind(payload: TransferPayload):
    """Blind transfer using FreeSWITCH uuid_transfer."""
    return await _execute_transfer(TransferMode.BLIND, payload)


@router.post("/transfer/attended", response_model=TransferResponse)
async def transfer_attended(payload: TransferPayload):
    """Attended transfer using FreeSWITCH att_xfer app via uuid_transfer inline."""
    return await _execute_transfer(TransferMode.ATTENDED, payload)


@router.post("/transfer/deflect", response_model=TransferResponse)
async def transfer_deflect(payload: TransferPayload):
    """REFER-based transfer (deflect) for answered SIP dialogs."""
    return await _execute_transfer(TransferMode.DEFLECT, payload)


@router.get("/transfer/{attempt_id}", response_model=TransferResponse)
async def get_transfer_attempt(attempt_id: str):
    """Fetch a previously recorded transfer attempt result."""
    global _esl_client
    if not _esl_client or not _esl_client.connected:
        raise HTTPException(status_code=400, detail="FreeSWITCH ESL client not connected")

    result = _esl_client.get_transfer_result(attempt_id)
    if not result:
        raise HTTPException(status_code=404, detail="Transfer attempt not found")
    return _to_transfer_response(result.to_dict())


# ============================================================================
# WebSocket endpoint for FreeSWITCH audio streaming
# ============================================================================

@router.websocket("/audio/{call_uuid}")
async def freeswitch_audio_websocket(websocket: WebSocket, call_uuid: str):
    """
    WebSocket endpoint for FreeSWITCH mod_audio_fork.

    Receives caller audio and sends AI responses.
    """
    global _audio_bridge

    if not _audio_bridge:
        _audio_bridge = get_audio_bridge()

    await _audio_bridge.handle_websocket(websocket, call_uuid)
