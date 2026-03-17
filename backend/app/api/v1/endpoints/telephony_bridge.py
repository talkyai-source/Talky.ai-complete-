"""
Generic Telephony Bridge Endpoint  (PBX-agnostic)

Routes: /api/v1/sip/telephony/...

This endpoint is the single entry-point for ALL SIP B2BUA integrations.
It uses CallControlAdapterFactory to obtain the active PBX adapter
(Asterisk or FreeSWITCH) — the caller never needs to know which one is live.

Endpoints
---------
  POST   /api/v1/sip/telephony/start            — connect to active B2BUA
  POST   /api/v1/sip/telephony/stop             — disconnect adapter
  GET    /api/v1/sip/telephony/status           — health + active calls
  POST   /api/v1/sip/telephony/call             — originate outbound call
  POST   /api/v1/sip/telephony/hangup/{id}      — hang up a call
  POST   /api/v1/sip/telephony/transfer/blind   — blind transfer
  POST   /api/v1/sip/telephony/transfer/attended— attended transfer
  POST   /api/v1/sip/telephony/transfer/deflect — deflect (REFER)
  POST   /api/v1/sip/telephony/audio/{id}       — C++ Gateway audio callback
  WS     /ws/telephony-audio/{uuid}             — mod_audio_fork WebSocket
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/telephony", tags=["Telephony Bridge (generic)"])

# ---------------------------------------------------------------------------
# Module-level adapter instance (one per process)
# ---------------------------------------------------------------------------
_adapter: Optional[CallControlAdapter] = None

# Active voice sessions keyed by PBX call_id (channel_id / call UUID)
_telephony_sessions: dict[str, object] = {}  # VoiceSession objects

# Maps C++ gateway session_id → PBX call_id for the audio callback path.
# Populated in _on_new_call when the AsteriskAdapter registers a gateway session.
_gateway_session_to_call_id: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


def _build_telephony_session_config(gateway_type: str = "browser"):
    """
    Build a VoiceSessionConfig tuned for telephony (8 kHz, SIP).
    
    Uses global AI config from AI Options API for voice and model selection.

    Parameters
    ----------
    gateway_type:
        "browser"   — used for the FreeSWITCH mod_audio_fork WebSocket path.
        "telephony" — used for the Asterisk HTTP-callback path
                      (TelephonyMediaGateway, no WebSocket required).
    """
    from app.domain.services.voice_orchestrator import VoiceSessionConfig
    from app.domain.services.global_ai_config import get_global_config
    from app.domain.models.agent_config import AgentConfig

    # Get global AI configuration (from AI Options API)
    global_config = get_global_config()

    # Use the TTS provider directly from global config
    # The global config already knows which provider to use
    tts_provider_type = global_config.tts_provider
    tts_voice_id = global_config.tts_voice_id
    
    logger.info(f"[TELEPHONY CONFIG] Using TTS provider: {tts_provider_type}, voice: {tts_voice_id}")

    # Create agent config for telephony calls
    # The LLM will generate its own human name and introduction
    agent_config = AgentConfig(
        agent_name="",  # LLM will choose its own name
        company_name="",  # LLM will introduce naturally
        business_type="general",
        goal="information_gathering",  # Valid enum value
        goal_description="have a natural conversation",
        tone="friendly and professional",
        response_max_sentences=3,
    )

    # System prompt that instructs the AI to introduce itself naturally
    system_prompt = """You are a helpful AI assistant making a phone call.

IMPORTANT INSTRUCTIONS:
1. When the call connects, introduce yourself with a natural human name (choose one yourself)
2. Be warm, friendly, and professional
3. Speak naturally like a real person would on the phone
4. Keep responses brief and conversational
5. Listen carefully and respond appropriately to what the person says
6. Do NOT mention you are an AI or assistant
7. Act like a real human having a natural phone conversation

Start the conversation by introducing yourself and asking how you can help."""

    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=tts_provider_type,  # Use detected provider from global config
        stt_model="flux-general-en",  # Flux model for deepgram_flux provider
        stt_sample_rate=8000,
        stt_encoding="linear16",
        llm_model=global_config.llm_model,  # From global config
        llm_temperature=global_config.llm_temperature,  # From global config
        llm_max_tokens=global_config.llm_max_tokens,  # From global config
        voice_id=tts_voice_id,  # From global config
        tts_sample_rate=8000,
        gateway_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        session_type="telephony",
        campaign_id="telephony",
        lead_id="sip-caller",
        agent_config=agent_config,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Audio pipeline lifecycle (called when a new call arrives on any B2BUA)
# ---------------------------------------------------------------------------

async def _on_new_call(call_id: str) -> None:
    """Initialize AI pipeline when a new SIP call arrives."""
    logger.info(f"Telephony bridge: new call {call_id[:12]}")
    try:
        orchestrator = _get_orchestrator()

        # Select the correct media gateway based on the active PBX adapter:
        #   - Asterisk path: TelephonyMediaGateway (HTTP callbacks, no WebSocket)
        #   - FreeSWITCH path: BrowserMediaGateway (mod_audio_fork WebSocket)
        is_asterisk = bool(_adapter and _adapter.name == "asterisk")
        gateway_type = "telephony" if is_asterisk else "browser"

        config = _build_telephony_session_config(gateway_type=gateway_type)
        voice_session = await orchestrator.create_voice_session(config)
        _telephony_sessions[call_id] = voice_session

        if is_asterisk:
            # Register gateway_session_id → call_id mapping so the audio callback
            # endpoint can route audio without a fragile string-prefix scan.
            gateway_session_id = getattr(_adapter, "_gateway_sessions", {}).get(call_id)
            if gateway_session_id:
                _gateway_session_to_call_id[gateway_session_id] = call_id

            # Initialise TelephonyMediaGateway with the adapter and PBX call ID.
            # This creates the session's input_queue and wires TTS output back to
            # the C++ gateway via adapter.send_tts_audio().
            await voice_session.media_gateway.on_call_started(
                voice_session.call_id,
                {"adapter": _adapter, "pbx_call_id": call_id},
            )

            # Start the voice pipeline - this handles everything naturally:
            # 1. STT listens for user speech
            # 2. LLM processes and responds (including initial greeting if user speaks first)
            # 3. TTS sends audio back
            # 4. Full conversational flow
            voice_session.pipeline_task = asyncio.create_task(
                voice_session.pipeline.start_pipeline(voice_session.call_session, None)
            )
            logger.info(f"Voice pipeline started for {call_id[:12]}")

        # Tell the adapter to start streaming audio.
        # For Asterisk this is a no-op (audio_callback_url handles it via C++ gateway).
        # For FreeSWITCH this triggers mod_audio_fork which connects the WebSocket,
        # which then triggers _on_ws_session_start to complete the FS pipeline setup.
        if _adapter:
            await _adapter.start_audio_stream(call_id)

        logger.info(f"AI pipeline initialized for {call_id[:12]}")
    except Exception as exc:
        logger.error(f"Failed to initialize AI pipeline for {call_id[:12]}: {exc}", exc_info=True)


async def _on_audio_received(call_id: str, audio_bytes: bytes) -> None:
    """Route incoming audio from the PBX into the media gateway (STT input)."""
    voice_session = _telephony_sessions.get(call_id)
    if not voice_session:
        return
    try:
        await voice_session.media_gateway.on_audio_received(
            voice_session.call_id, audio_bytes
        )
    except Exception as exc:
        logger.debug(f"Audio route error {call_id[:12]}: {exc}")


async def _on_call_ended(call_id: str) -> None:
    """Clean up voice session when the call hangs up."""
    logger.info(f"Telephony bridge: call ended {call_id[:12]}")
    voice_session = _telephony_sessions.pop(call_id, None)
    if voice_session:
        try:
            await _get_orchestrator().end_session(voice_session)
        except Exception:
            pass
    # Clean up gateway session mapping
    keys_to_remove = [k for k, v in _gateway_session_to_call_id.items() if v == call_id]
    for k in keys_to_remove:
        _gateway_session_to_call_id.pop(k, None)





async def _on_ws_session_start(call_id: str) -> None:
    """
    Called when FreeSWITCH mod_audio_fork WebSocket connects.
    Wires the bridge WebSocket into the media gateway and starts the pipeline.
    """
    from app.infrastructure.telephony.freeswitch_audio_bridge import get_audio_bridge

    voice_session = _telephony_sessions.get(call_id)
    if not voice_session:
        # Race: wait briefly for session to be stored
        for _ in range(20):
            await asyncio.sleep(0.05)
            voice_session = _telephony_sessions.get(call_id)
            if voice_session:
                break

    if not voice_session:
        logger.error(f"No voice session for WS session start: {call_id[:12]}")
        return

    bridge_ws = get_audio_bridge().get_websocket(call_id)
    if not bridge_ws:
        logger.error(f"No bridge WebSocket for {call_id[:12]}")
        return

    try:
        await voice_session.media_gateway.on_call_started(
            voice_session.call_id, {"websocket": bridge_ws}
        )

        if voice_session.pipeline:
            async def _run():
                try:
                    await voice_session.pipeline.start_pipeline(
                        voice_session.call_session, None
                    )
                except Exception as exc:
                    logger.error(f"Pipeline error {call_id[:12]}: {exc}", exc_info=True)

            voice_session.pipeline_task = asyncio.create_task(_run())
            logger.info(f"Voice pipeline started for {call_id[:12]}")
    except Exception as exc:
        logger.error(f"WS session start error: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_telephony(
    adapter_type: str = Query(
        default="auto",
        description="'auto' (detect), 'asterisk', or 'freeswitch'",
    ),
):
    """
    Connect to the active B2BUA and start handling calls.
    Use adapter_type='auto' to let the system choose based on health checks.
    """
    global _adapter

    if _adapter and _adapter.connected:
        return JSONResponse({
            "status": "already_connected",
            "adapter": _adapter.name,
        })

    try:
        _adapter = await CallControlAdapterFactory.create(adapter_type)

        # Register call event handlers via the generic interface.
        # Every adapter (Asterisk, FreeSWITCH, future PBXes) implements
        # register_call_event_handlers() so the bridge never needs to
        # check adapter.name or access internal fields like _esl.
        _adapter.register_call_event_handlers(
            on_new_call=_on_new_call,
            on_call_ended=_on_call_ended,
            on_audio_received=_on_audio_received,
        )

        # For adapters that use a WebSocket audio bridge (FreeSWITCH),
        # also wire the session-start callback so the pipeline knows
        # when the WebSocket connection is established.
        if hasattr(_adapter, "set_global_session_start_callback"):
            _adapter.set_global_session_start_callback(_on_ws_session_start)

        await _adapter.connect()

        return JSONResponse({
            "status": "connected",
            "adapter": _adapter.name,
            "message": f"Connected to {_adapter.name} B2BUA",
        })

    except Exception as exc:
        logger.error(f"Failed to start telephony adapter: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/stop")
async def stop_telephony():
    """Disconnect from the active B2BUA."""
    global _adapter

    if not _adapter:
        return JSONResponse({"status": "not_running"})

    await _adapter.disconnect()
    _adapter = None
    return JSONResponse({"status": "stopped"})


@router.get("/status")
async def telephony_status():
    """Return health and active call information for the current adapter."""
    if not _adapter:
        return JSONResponse({
            "status": "not_started",
            "connected": False,
            "adapter": None,
        })

    healthy = await _adapter.health_check()
    return JSONResponse({
        "status": "running" if healthy else "degraded",
        "connected": _adapter.connected,
        "adapter": _adapter.name,
        "active_sessions": len(_telephony_sessions),
        "healthy": healthy,
    })


@router.post("/call")
async def make_call(
    destination: str = Query(..., description="Destination extension or phone number"),
    caller_id: str = Query(default="1001", description="Caller ID to display"),
):
    """Originate an outbound call via the active B2BUA adapter."""
    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")

    try:
        call_id = await _adapter.originate_call(destination=destination, caller_id=caller_id)
        return JSONResponse({
            "status": "calling",
            "call_id": call_id,
            "destination": destination,
            "adapter": _adapter.name,
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/hangup/{call_id}")
async def hangup_call(call_id: str):
    """Hang up a specific call."""
    if not _adapter:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")
    await _adapter.hangup(call_id)
    return JSONResponse({"status": "ok", "call_id": call_id})


# ---------------------------------------------------------------------------
# Transfer endpoints
# ---------------------------------------------------------------------------

class TransferPayload(BaseModel):
    call_id: str = Field(..., description="PBX call / channel UUID")
    destination: str = Field(..., description="Transfer destination")
    mode: Literal["blind", "attended", "deflect"] = Field(default="blind")


@router.post("/transfer/blind")
async def transfer_blind(payload: TransferPayload):
    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")
    result = await _adapter.transfer(payload.call_id, payload.destination, "blind")
    return JSONResponse(result)


@router.post("/transfer/attended")
async def transfer_attended(payload: TransferPayload):
    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")
    result = await _adapter.transfer(payload.call_id, payload.destination, "attended")
    return JSONResponse(result)


@router.post("/transfer/deflect")
async def transfer_deflect(payload: TransferPayload):
    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")
    result = await _adapter.transfer(payload.call_id, payload.destination, "deflect")
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# C++ Gateway audio callback (Asterisk path)
# ---------------------------------------------------------------------------

@router.post("/audio/{session_id}")
async def receive_gateway_audio(session_id: str, request: Request):
    """
    HTTP callback invoked by the C++ Voice Gateway to push caller audio chunks
    to the backend AI pipeline (Asterisk path).

    The gateway POSTs JSON: {"session_id":"...","pcmu_base64":"...","codec":"pcmu"}
    """
    import base64

    try:
        request_body = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"})

    if not request_body:
        return JSONResponse({"status": "ok"})

    # C++ gateway sends pcmu_base64; fall back to audio_base64 for compatibility.
    audio_b64 = request_body.get("pcmu_base64") or request_body.get("audio_base64", "")
    if not audio_b64:
        return JSONResponse({"status": "ok"})

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        return JSONResponse({"status": "ok"})

    # Direct lookup first (fast path — registered in _on_new_call).
    matched_call_id: Optional[str] = _gateway_session_to_call_id.get(session_id)

    # Fallback: prefix match for race conditions before mapping is registered.
    if not matched_call_id:
        for call_id in list(_telephony_sessions.keys()):
            if session_id.startswith(f"asterisk-{call_id[:12]}"):
                matched_call_id = call_id
                # Cache it to speed up future lookups.
                _gateway_session_to_call_id[session_id] = call_id
                break

    if matched_call_id:
        await _on_audio_received(matched_call_id, audio_bytes)

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# WebSocket endpoint for FreeSWITCH mod_audio_fork
# ---------------------------------------------------------------------------

@router.websocket("/ws-audio/{call_uuid}")
async def telephony_audio_websocket(websocket: WebSocket, call_uuid: str):
    """
    WebSocket endpoint for FreeSWITCH mod_audio_fork.

    FreeSWITCH dials:
        <action application="audio_fork" data="ws://HOST:8000/api/v1/sip/telephony/ws-audio/{uuid}"/>

    Receives caller audio and forwards TTS responses back.
    """
    from app.infrastructure.telephony.freeswitch_audio_bridge import get_audio_bridge
    bridge = get_audio_bridge()
    await bridge.handle_websocket(websocket, call_uuid)
