"""
Vonage Voice Bridge Endpoint.

Routes: /api/v1/vonage/...

Implements the official Vonage Voice API webhook/WebSocket model:

  POST /api/v1/vonage/answer       — answer_url: returns NCCO JSON
  POST /api/v1/vonage/event        — event_url:  receives call status events
  WS   /api/v1/vonage/ws-audio/{uuid} — Vonage-initiated audio WebSocket

Audio model:
  Vonage opens a WebSocket **to us** (provider-initiated, same as FreeSWITCH
  mod_audio_fork). Audio arrives as raw 16-bit linear PCM at 16 kHz mono.
  We feed it into ``BrowserMediaGateway`` (which already handles provider-
  initiated WebSocket audio) → ``VoicePipelineService`` → STT → LLM → TTS.

See: https://developer.vonage.com/en/voice/voice-api/concepts/websockets
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.domain.models.voice_contract import map_vonage_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vonage", tags=["Vonage Voice Bridge"])

_vonage_sessions: dict[str, object] = {}


def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


def _build_vonage_session_config():
    """Build a VoiceSessionConfig tuned for Vonage (16 kHz linear16 WebSocket)."""
    from app.domain.services.voice_orchestrator import VoiceSessionConfig
    from app.domain.services.global_ai_config import get_global_config

    config = get_global_config()

    return VoiceSessionConfig(
        gateway_type="browser",
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type="deepgram",
        stt_model="nova-2",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        llm_model=config.llm_model,
        llm_temperature=config.llm_temperature,
        llm_max_tokens=config.llm_max_tokens,
        voice_id=config.tts_voice_id,
        tts_sample_rate=16000,
        gateway_sample_rate=16000,
        gateway_channels=1,
        gateway_bit_depth=16,
        session_type="vonage",
        telephony_provider="vonage",
        campaign_id="vonage",
        lead_id="vonage-caller",
    )


# ---------------------------------------------------------------------------
# POST /answer — Vonage answer_url webhook
# ---------------------------------------------------------------------------

@router.post("/answer")
async def vonage_answer(request: Request):
    """
    Called by Vonage when an inbound call arrives or an outbound call is answered.

    Returns an NCCO that connects the call audio to our WebSocket endpoint.
    This is the official pattern per Vonage Voice API documentation.
    """
    body = await request.json()
    call_uuid = body.get("uuid", body.get("conversation_uuid", "unknown"))
    from_number = body.get("from", "unknown")
    to_number = body.get("to", "unknown")

    logger.info(
        "Vonage answer webhook: uuid=%s from=%s to=%s",
        call_uuid[:12], from_number, to_number,
    )

    api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
    ws_base = api_base.replace("https://", "wss://").replace("http://", "ws://")

    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": f"{ws_base}/api/v1/vonage/ws-audio/{call_uuid}",
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "call_uuid": call_uuid,
                        "from_number": from_number,
                        "to_number": to_number,
                    },
                }
            ],
        }
    ]

    return JSONResponse(content=ncco)


# ---------------------------------------------------------------------------
# POST /event — Vonage event_url webhook
# ---------------------------------------------------------------------------

@router.post("/event")
async def vonage_event(request: Request):
    """
    Receives call lifecycle events from Vonage (started, ringing, answered,
    completed, failed, etc.).

    Maps Vonage-specific statuses to the canonical VoiceCallState via
    ``map_vonage_status()``.
    """
    body = await request.json()
    call_uuid = body.get("uuid", "")
    status = body.get("status", "")
    direction = body.get("direction", "")

    voice_state = map_vonage_status(status)

    logger.info(
        "Vonage event: uuid=%s status=%s direction=%s mapped=%s",
        call_uuid[:12] if call_uuid else "?",
        status,
        direction,
        voice_state.value if voice_state else "ignored",
    )

    if status == "completed" and call_uuid in _vonage_sessions:
        voice_session = _vonage_sessions.pop(call_uuid, None)
        if voice_session:
            orchestrator = _get_orchestrator()
            try:
                await orchestrator.end_session(voice_session)
            except Exception as exc:
                logger.warning("Failed to end Vonage session %s: %s", call_uuid[:12], exc)

    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# WebSocket /ws-audio/{call_uuid} — Vonage-initiated audio stream
# ---------------------------------------------------------------------------

@router.websocket("/ws-audio/{call_uuid}")
async def vonage_ws_audio(websocket: WebSocket, call_uuid: str):
    """
    Vonage connects TO this WebSocket after the NCCO ``connect`` action.

    Audio format: raw 16-bit linear PCM, 16 kHz, mono (little-endian).
    This is the same model as FreeSWITCH mod_audio_fork — provider-initiated
    WebSocket — so BrowserMediaGateway works out of the box.
    """
    await websocket.accept()
    logger.info("Vonage WS audio connected: %s", call_uuid[:12])

    voice_session = None
    try:
        orchestrator = _get_orchestrator()
        config = _build_vonage_session_config()
        voice_session = await orchestrator.create_voice_session(config)
        _vonage_sessions[call_uuid] = voice_session

        pipeline_task = await orchestrator.start_pipeline(voice_session, websocket)

        try:
            while True:
                data = await websocket.receive()
                if data.get("type") == "websocket.disconnect":
                    break
                raw = data.get("bytes") or data.get("text", b"")
                if isinstance(raw, (bytes, bytearray)) and raw:
                    await voice_session.media_gateway.on_audio_received(
                        voice_session.call_id, raw
                    )
        except WebSocketDisconnect:
            pass

    except Exception as exc:
        logger.error("Vonage WS audio error for %s: %s", call_uuid[:12], exc, exc_info=True)
    finally:
        if voice_session:
            _vonage_sessions.pop(call_uuid, None)
            try:
                orchestrator = _get_orchestrator()
                await orchestrator.end_session(voice_session)
            except Exception as cleanup_exc:
                logger.debug("Vonage session cleanup error: %s", cleanup_exc)
        logger.info("Vonage WS audio disconnected: %s", call_uuid[:12])
