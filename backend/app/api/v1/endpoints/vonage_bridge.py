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

Security: the whole bridge is DEFAULT-DENY (``VONAGE_BRIDGE_ENABLED``) and the
audio socket authenticates a short-lived HMAC token minted by the signature-
verified ``/answer`` webhook — see "Audio WebSocket auth".

See: https://developer.vonage.com/en/voice/voice-api/concepts/websockets
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.core.security.telephony_webhook_auth import verify_vonage_signature
from app.domain.models.voice_contract import map_vonage_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vonage", tags=["Vonage Voice Bridge"])

_vonage_sessions: dict[str, object] = {}

# Concurrency cap so an attacker (or a stuck provider) can't pin an unbounded
# number of LLM/STT/TTS provider sockets. Same shape as campaign_test_ws.py /
# ask_ai / assistant_voice. RFC 6455 close 1013 = "Try Again Later".
_MAX_CONCURRENT_STREAMS = 20
_stream_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _stream_semaphore
    if _stream_semaphore is None:
        _stream_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_STREAMS)
    return _stream_semaphore


def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


# ---------------------------------------------------------------------------
# Feature gate — DEFAULT DENY
# ---------------------------------------------------------------------------
# Production runs active_telephony_provider="sip"; Vonage is not in live use.
# An unset env var therefore means CLOSED, so this bridge is not attack surface
# for deployments that never enabled it.

def _bridge_enabled() -> bool:
    return os.getenv("VONAGE_BRIDGE_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ---------------------------------------------------------------------------
# Audio WebSocket auth
# ---------------------------------------------------------------------------
# Unlike Twilio, Vonage DOES authenticate the WebSocket opening handshake: the
# NCCO ``connect``/``websocket`` endpoint accepts an ``Authorization`` header
# value that "Vonage will send verbatim in the opening handshake"
#   https://developer.vonage.com/en/voice/voice-api/concepts/websockets
# so we mint our own short-lived HMAC-SHA256 token in /answer (which IS
# signature-verified) and set it as that Authorization value. The token is also
# appended to the WS URI as ``?token=`` and sent as a custom header, because the
# exact echo behaviour of NCCO custom headers is under-specified — accepting it
# from any of the three keeps this compatible without weakening anything (all
# three go through the same verifier).
#
# The token is bound to the dialed DID and the call uuid, and the tenant is read
# from its claims — NEVER from the ``to_number`` request header, which an
# anonymous client could set to a victim's DID. Minting additionally requires
# VONAGE_SIGNATURE_SECRET, because without it the /answer signature check is
# fail-soft (skipped) and anyone could ask us to mint a token.
#
# Residual risk (documented, accepted): a token is bearer-style, so a party who
# can observe it inside Vonage's TLS session could replay it before ``exp``.
# Single-use enforcement needs shared state (Redis) and is out of scope here.

_TOKEN_AUDIENCE = "vonage-ws-audio"
_TOKEN_TTL_S = 300
_TOKEN_VERSION = "v1"
_WS_TOKEN_HEADER = "x-talky-ws-token"


def _ws_token_secret() -> Optional[str]:
    """HMAC key for audio-socket tokens. None => mint and verify both refuse."""
    for var in (
        "VONAGE_WS_TOKEN_SECRET",
        "TELEPHONY_WS_TOKEN_SECRET",
        "VONAGE_SIGNATURE_SECRET",
    ):
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _mint_ws_token(*, to_number: str, call_uuid: str) -> Optional[str]:
    """Mint ``v1.<payload>.<sig>``. Returns None when no secret is configured."""
    secret = _ws_token_secret()
    if not secret:
        return None
    claims = {
        "aud": _TOKEN_AUDIENCE,
        "to": to_number,
        "uuid": call_uuid,
        "exp": int(time.time()) + _TOKEN_TTL_S,
        "jti": secrets.token_hex(8),
    }
    payload = _b64url_encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{_TOKEN_VERSION}.{payload}"
    sig = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def _verify_ws_token(token: Optional[str]) -> Optional[dict[str, Any]]:
    """Verify a minted token. Returns claims, or None for ANY failure.

    Fail-closed: no secret, no token, wrong version, bad signature, wrong
    audience, expired or malformed all return None. Never logs the token.
    """
    secret = _ws_token_secret()
    if not secret or not token:
        return None
    try:
        version, payload, sig_b64 = token.split(".")
    except (ValueError, AttributeError):
        return None
    if version != _TOKEN_VERSION:
        return None
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{version}.{payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception:  # noqa: BLE001 — malformed base64
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        claims = json.loads(_b64url_decode(payload))
    except Exception:  # noqa: BLE001 — malformed payload
        return None
    if not isinstance(claims, dict):
        return None
    if claims.get("aud") != _TOKEN_AUDIENCE:
        return None
    try:
        if float(claims.get("exp", 0)) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    return claims


def _extract_ws_token(websocket: WebSocket, query_token: Optional[str]) -> Optional[str]:
    """Pull the token from the handshake: Authorization bearer, custom header,
    then the URI query. All three are verified identically."""
    authorization = websocket.headers.get("authorization")
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        stripped = authorization.strip()
        if stripped:
            return stripped
    header_token = websocket.headers.get(_WS_TOKEN_HEADER)
    if header_token and header_token.strip():
        return header_token.strip()
    if query_token and query_token.strip():
        return query_token.strip()
    return None


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
    if not _bridge_enabled():
        logger.warning("vonage_answer rejected: VONAGE_BRIDGE_ENABLED is not set")
        return JSONResponse(content={"error": "not_found"}, status_code=404)
    # Minting an audio-socket token off an UNVERIFIED webhook would hand the
    # credential to anyone who can POST here, so require the signature secret.
    if not (os.getenv("VONAGE_SIGNATURE_SECRET") or "").strip():
        logger.error(
            "vonage_answer refused: VONAGE_SIGNATURE_SECRET unset, so the webhook "
            "signature is unenforced and an audio-socket token cannot be minted"
        )
        return JSONResponse(content={"error": "unavailable"}, status_code=503)
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

    token = _mint_ws_token(to_number=to_number, call_uuid=call_uuid)
    if not token:
        logger.error("vonage_answer refused: no audio-socket token secret configured")
        return JSONResponse(content={"error": "unavailable"}, status_code=503)

    api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
    ws_base = api_base.replace("https://", "wss://").replace("http://", "ws://")

    # The token goes on the handshake three ways (Authorization value, custom
    # header, URI query) — whichever Vonage forwards, the socket verifies it.
    # call_uuid/from_number/to_number stay for provider-side debugging only; the
    # socket treats them as untrusted and reads the DID from the token claims.
    ncco = [
        {
            "action": "connect",
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": (
                        f"{ws_base}/api/v1/vonage/ws-audio/{call_uuid}"
                        f"?token={token}"
                    ),
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "call_uuid": call_uuid,
                        "from_number": from_number,
                        "to_number": to_number,
                        "Authorization": f"Bearer {token}",
                        _WS_TOKEN_HEADER: token,
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
async def vonage_ws_audio(
    websocket: WebSocket,
    call_uuid: str,
    token: Optional[str] = Query(None),
):
    """
    Vonage connects TO this WebSocket after the NCCO ``connect`` action.

    Audio format: raw 16-bit linear PCM, 16 kHz, mono (little-endian).
    This is the same model as FreeSWITCH mod_audio_fork — provider-initiated
    WebSocket — so BrowserMediaGateway works out of the box.

    Refuses the upgrade unless the bridge is explicitly enabled AND the
    /answer-minted token verifies against this ``call_uuid``. Everything is
    checked before ``accept()``, so an unauthenticated client never gets a
    socket, a session, or a concurrency slot.
    """
    if not _bridge_enabled():
        logger.warning("vonage ws-audio rejected: VONAGE_BRIDGE_ENABLED is not set")
        await websocket.close(code=1008, reason="Vonage bridge disabled")
        return

    claims = _verify_ws_token(_extract_ws_token(websocket, token))
    if claims is None:
        logger.warning(
            "vonage ws-audio rejected: missing/invalid token for %s", call_uuid[:12]
        )
        await websocket.close(code=1008, reason="Unauthorized")
        return
    # Bind the token to this call so one minted token can't be replayed onto a
    # different call's socket.
    if claims.get("uuid") != call_uuid:
        logger.warning(
            "vonage ws-audio rejected: token/call uuid mismatch for %s", call_uuid[:12]
        )
        await websocket.close(code=1008, reason="Unauthorized")
        return

    sem = _get_semaphore()
    if sem._value == 0:
        logger.warning("vonage ws-audio rejected: at capacity")
        await websocket.close(code=1013, reason="At capacity")
        return

    await websocket.accept()
    logger.info("Vonage WS audio connected: %s", call_uuid[:12])

    voice_session = None
    try:
        async with sem:
            orchestrator = _get_orchestrator()
            # The tenant-selecting DID comes from the SIGNED token minted by
            # /answer — NOT from the ``to_number`` request header, which any
            # anonymous client could set to a victim's DID.
            to_number = claims.get("to")
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
