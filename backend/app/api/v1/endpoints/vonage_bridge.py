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

from app.core.security.telephony_webhook_auth import verify_vonage_signature
from app.domain.models.voice_contract import map_vonage_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vonage", tags=["Vonage Voice Bridge"])

_vonage_sessions: dict[str, object] = {}


def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


async def _build_vonage_session_config(to_number: Optional[str] = None):
    """Build a VoiceSessionConfig tuned for Vonage (16 kHz linear16 WebSocket).

    Sources provider SELECTION per-tenant off the dialed DID (``to_number``):
    resolve the tenant, load its persisted AIProviderConfig, and DERIVE the LLM
    provider from it. Hardcoding "groq" while reading a possibly-gemini
    ``llm_model`` 404'd every turn. Falls back to the process default for an
    unknown/unroutable DID.
    """
    from app.domain.services.voice_orchestrator import VoiceSessionConfig
    from app.domain.services.tenant_ai_config_resolver import (
        resolve_ai_config_for_did,
    )

    tenant_id, config = await resolve_ai_config_for_did(to_number)
    llm_provider_type = (
        getattr(config.llm_provider, "value", None)
        or str(config.llm_provider)
        or "groq"
    )

    return VoiceSessionConfig(
        gateway_type="browser",
        stt_provider_type="deepgram_flux",
        llm_provider_type=llm_provider_type,
        tts_provider_type=config.tts_provider,
        stt_model="flux-general-en",       # was nova-2 — use Flux for better EOT detection
        stt_sample_rate=16000,
        stt_encoding="linear16",
        stt_eot_threshold=0.85,            # was default 0.7 — stop cutting users off
        stt_eot_timeout_ms=1500,           # was 5000 — industry min is 1000ms; Flux integrated EOT handles accuracy
        stt_eager_eot_threshold=None,      # disable eager — no speculative LLM yet
        llm_model=config.llm_model,
        llm_temperature=config.llm_temperature,
        llm_max_tokens=config.llm_max_tokens,
        voice_id=config.tts_voice_id,
        tts_model=config.tts_model,
        tts_sample_rate=16000,
        gateway_sample_rate=16000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,       # was default 100ms — saves 60ms per chunk
        mute_during_tts=False,             # must be explicit — default True blocks barge-in
        session_type="vonage",
        telephony_provider="vonage",
        campaign_id="vonage",
        lead_id="vonage-caller",
        # Thread tenant context so per-tenant credentials resolve too.
        tenant_id=tenant_id,
        # Preserve the tenant's realtime pipeline selection.
        pipeline_mode=getattr(config, "pipeline_mode", "cascaded") or "cascaded",
        realtime_model=getattr(config, "realtime_model", "gpt-realtime-2"),
        realtime_voice=getattr(config, "realtime_voice", "marin"),
        realtime_settings=getattr(config, "realtime_settings", None),
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
    if not verify_vonage_signature(authorization=request.headers.get("Authorization")):
        logger.warning("vonage_answer rejected: bad signature")
        return JSONResponse(content={"error": "unauthorized"}, status_code=403)
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
    if not verify_vonage_signature(authorization=request.headers.get("Authorization")):
        logger.warning("vonage_event rejected: bad signature")
        return JSONResponse(content={"error": "unauthorized"}, status_code=403)
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
        # Vonage echoes the custom headers set on the NCCO websocket endpoint
        # (see vonage_answer) back as WS headers — to_number is the dialed DID
        # and identifies the tenant. Best-effort: absent → process default.
        to_number = websocket.headers.get("to_number")
        config = await _build_vonage_session_config(to_number)
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
