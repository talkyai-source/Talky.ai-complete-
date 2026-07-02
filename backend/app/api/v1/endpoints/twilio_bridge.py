"""
Twilio Programmable Voice Bridge.

Routes: /api/v1/twilio/...

  POST /api/v1/twilio/answer        — Voice webhook: returns TwiML <Connect><Stream>
  POST /api/v1/twilio/event         — statusCallback: call lifecycle events
  WS   /api/v1/twilio/media-stream  — Twilio Media Streams (bidirectional audio)

Audio model:
  Twilio opens a WebSocket TO us (Media Streams) and exchanges audio as JSON
  text frames carrying base64 G.711 mu-law at 8 kHz. ``TwilioMediaGateway``
  handles the wire format; the pipeline runs natively at 8 kHz (no resampling).

Mirrors the shape of ``vonage_bridge.py``. Webhook requests are signature-
validated (env-gated — see telephony_webhook_auth).

See: https://www.twilio.com/docs/voice/twiml/connect
     https://www.twilio.com/docs/voice/media-streams/websocket-messages
"""
from __future__ import annotations

import base64
import json
import logging
import os
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

from app.core.security.telephony_webhook_auth import verify_twilio_signature
from app.domain.models.voice_contract import map_twilio_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["Twilio Voice Bridge"])

# streamSid -> voice_session
_twilio_sessions: dict[str, object] = {}


def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


def _public_base() -> str:
    return os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")


def _webhook_url(path: str) -> str:
    return f"{_public_base()}{path}"


async def _build_twilio_session_config(to_number: str | None = None):
    """VoiceSessionConfig tuned for Twilio Media Streams (8 kHz mu-law).

    Sources the provider SELECTION per-tenant: the dialed number (``to_number``,
    the DID) identifies the tenant, so we resolve that tenant's persisted
    AIProviderConfig from tenant_ai_configs. Falls back to the process default
    for an unknown/unroutable DID. The LLM provider is DERIVED from the resolved
    config — never hardcoded — because hardcoding "groq" while reading a
    possibly-gemini ``llm_model`` 404'd every turn.
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
        gateway_type="twilio",
        stt_provider_type="deepgram_flux",
        llm_provider_type=llm_provider_type,
        tts_provider_type=config.tts_provider,
        stt_model="flux-general-en",
        stt_sample_rate=8000,          # Twilio Media Streams = 8 kHz
        stt_encoding="linear16",       # we decode mu-law -> linear16 before STT
        stt_eot_threshold=0.85,
        stt_eot_timeout_ms=1500,
        stt_eager_eot_threshold=None,
        llm_model=config.llm_model,
        llm_temperature=config.llm_temperature,
        llm_max_tokens=config.llm_max_tokens,
        voice_id=config.tts_voice_id,
        tts_model=config.tts_model,
        tts_sample_rate=8000,          # synth at 8 kHz so no resample before mu-law
        gateway_sample_rate=8000,
        gateway_input_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="twilio",
        telephony_provider="twilio",
        campaign_id="twilio",
        lead_id="twilio-caller",
        # Thread tenant context so per-tenant credentials resolve too.
        tenant_id=tenant_id,
        # Preserve the tenant's realtime pipeline selection.
        pipeline_mode=getattr(config, "pipeline_mode", "cascaded") or "cascaded",
        realtime_model=getattr(config, "realtime_model", "gpt-realtime-2"),
        realtime_voice=getattr(config, "realtime_voice", "marin"),
        realtime_settings=getattr(config, "realtime_settings", None),
    )


def _twiml_stream_response(call_sid: str, from_number: str, to_number: str) -> str:
    """Build the <Connect><Stream> TwiML pointing at our Media Streams WS."""
    ws_base = _public_base().replace("https://", "wss://").replace("http://", "ws://")
    stream_url = f"{ws_base}/api/v1/twilio/media-stream"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Connect><Stream url={quoteattr(stream_url)}>"
        f'<Parameter name="callSid" value={quoteattr(call_sid)}/>'
        f'<Parameter name="from" value={quoteattr(from_number)}/>'
        f'<Parameter name="to" value={quoteattr(to_number)}/>'
        "</Stream></Connect>"
        "</Response>"
    )


# ---------------------------------------------------------------------------
# POST /answer — Twilio Voice webhook (returns TwiML)
# ---------------------------------------------------------------------------

@router.post("/answer")
async def twilio_answer(request: Request):
    """Twilio fetches TwiML here on call connect. We return a
    ``<Connect><Stream>`` that opens a bidirectional Media Streams WebSocket."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not verify_twilio_signature(
        url=_webhook_url("/api/v1/twilio/answer"),
        params=params,
        signature=request.headers.get("X-Twilio-Signature"),
    ):
        logger.warning("twilio_answer rejected: bad signature")
        return Response(status_code=403)

    call_sid = params.get("CallSid", "unknown")
    from_number = params.get("From", "unknown")
    to_number = params.get("To", "unknown")
    logger.info(
        "Twilio answer webhook: callSid=%s from=%s to=%s",
        call_sid[:16], from_number, to_number,
    )
    twiml = _twiml_stream_response(call_sid, from_number, to_number)
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# POST /event — Twilio statusCallback (informational)
# ---------------------------------------------------------------------------

@router.post("/event")
async def twilio_event(request: Request):
    """Call lifecycle status callbacks. The Media Streams WebSocket lifecycle
    is the source of truth for session teardown, so this is informational —
    we normalise + log the status."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not verify_twilio_signature(
        url=_webhook_url("/api/v1/twilio/event"),
        params=params,
        signature=request.headers.get("X-Twilio-Signature"),
    ):
        logger.warning("twilio_event rejected: bad signature")
        return Response(status_code=403)

    call_sid = params.get("CallSid", "")
    status = params.get("CallStatus", "")
    voice_state = map_twilio_status(status)
    logger.info(
        "Twilio event: callSid=%s status=%s mapped=%s",
        call_sid[:16] if call_sid else "?", status,
        voice_state.value if voice_state else "ignored",
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# WebSocket /media-stream — Twilio Media Streams (bidirectional audio)
# ---------------------------------------------------------------------------

@router.websocket("/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """Twilio connects here after the ``<Connect><Stream>`` TwiML.

    Protocol (JSON text frames): connected -> start -> media* -> stop.
    Inbound media is base64 mu-law 8 kHz; we decode + feed the pipeline.
    Outbound audio + barge-in ``clear`` frames are emitted by TwilioMediaGateway.
    """
    await websocket.accept()
    orchestrator = _get_orchestrator()
    voice_session = None
    stream_sid = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue
            event = data.get("event")

            if event == "start":
                start = data.get("start", {}) or {}
                stream_sid = start.get("streamSid") or data.get("streamSid")
                call_sid = start.get("callSid", "")
                # The dialed DID is passed through as a custom Stream Parameter
                # (see _twiml_stream_response). It identifies the tenant so the
                # session sources that tenant's own provider selection.
                to_number = (start.get("customParameters", {}) or {}).get("to")
                logger.info(
                    "Twilio media start: streamSid=%s callSid=%s to=%s",
                    (stream_sid or "?")[:16], (call_sid or "?")[:16], to_number or "?",
                )
                config = await _build_twilio_session_config(to_number)
                voice_session = await orchestrator.create_voice_session(config)
                gw = voice_session.media_gateway
                if hasattr(gw, "set_stream_sid") and stream_sid:
                    gw.set_stream_sid(voice_session.call_id, stream_sid)
                if stream_sid:
                    _twilio_sessions[stream_sid] = voice_session
                await orchestrator.start_pipeline(voice_session, websocket)

            elif event == "media":
                if voice_session is None:
                    continue
                payload_b64 = (data.get("media", {}) or {}).get("payload")
                if not payload_b64:
                    continue
                try:
                    ulaw = base64.b64decode(payload_b64)
                except Exception:
                    continue
                gw = voice_session.media_gateway
                if hasattr(gw, "feed_twilio_media"):
                    await gw.feed_twilio_media(voice_session.call_id, ulaw)

            elif event == "dtmf":
                digit = (data.get("dtmf", {}) or {}).get("digit")
                logger.info(
                    "Twilio DTMF: streamSid=%s digit=%s",
                    (stream_sid or "?")[:16], digit,
                )

            elif event == "stop":
                break
            # "connected" and "mark" frames are ignored.

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Twilio media-stream error: %s", exc, exc_info=True)
    finally:
        if stream_sid:
            _twilio_sessions.pop(stream_sid, None)
        if voice_session is not None:
            try:
                await orchestrator.end_session(voice_session)
            except Exception as cleanup_exc:
                logger.debug("Twilio session cleanup error: %s", cleanup_exc)
        logger.info("Twilio media-stream closed: streamSid=%s", (stream_sid or "?")[:16])
