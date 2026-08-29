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

Security: the whole bridge is DEFAULT-DENY (``TWILIO_BRIDGE_ENABLED``) and the
Media Streams socket authenticates a short-lived HMAC token minted by the
signature-verified ``/answer`` webhook — see "Media Streams WebSocket auth".

See: https://www.twilio.com/docs/voice/twiml/connect
     https://www.twilio.com/docs/voice/media-streams/websocket-messages
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
import time
from typing import Any, Optional
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Query, Request, Response, WebSocket, WebSocketDisconnect

from app.core.security.telephony_webhook_auth import verify_twilio_signature
from app.domain.models.voice_contract import map_twilio_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["Twilio Voice Bridge"])

# streamSid -> voice_session
_twilio_sessions: dict[str, object] = {}

# Concurrency cap so an attacker (or a stuck provider) can't pin an unbounded
# number of LLM/STT/TTS provider sockets. Same shape as campaign_test_ws.py /
# ask_ai / assistant_voice. RFC 6455 close 1013 = "Try Again Later".
_MAX_CONCURRENT_STREAMS = 20
_stream_semaphore: Optional[asyncio.Semaphore] = None

# An accepted-but-unauthenticated socket must not hold a slot indefinitely.
_AUTH_DEADLINE_S = 10.0


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
# Production runs active_telephony_provider="sip"; Twilio is not in live use.
# An unset env var therefore means CLOSED, so this bridge is not attack surface
# for deployments that never enabled it.

def _bridge_enabled() -> bool:
    return os.getenv("TWILIO_BRIDGE_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


_TWILIO_OUTBOUND_DIRECTIONS = {"outbound", "outbound-api", "outbound-dial"}


def _is_supported_answer_direction(value: object) -> bool:
    """Only provider-originated OUTBOUND answers use this legacy bridge.

    True inbound must enter through the Asterisk pre-answer admission path,
    which commits the tenant-scoped call row and reservations before Answer.
    Treat a missing/unknown direction as inbound-risk ambiguity and fail closed.
    """

    return str(value or "").strip().lower() in _TWILIO_OUTBOUND_DIRECTIONS


# ---------------------------------------------------------------------------
# Media Streams WebSocket auth
# ---------------------------------------------------------------------------
# Twilio does NOT authenticate the Media Streams WebSocket handshake. The
# documented frame set (connected / start / media / stop) carries no credential
# and no X-Twilio-Signature is sent on the upgrade:
#   https://www.twilio.com/docs/voice/media-streams/websocket-messages
# The socket is reachable from the public internet, so the ONLY vendor-supported
# way to carry a secret through is data WE put into the <Stream> ourselves: the
# `url` (query string) and <Parameter> custom parameters, which Twilio echoes
# back verbatim in `start.customParameters`.
#
# Therefore: /answer — which IS Twilio-signature-verified — mints a short-lived
# HMAC-SHA256 token bound to the dialed DID and CallSid, and this socket does no
# work at all until that token verifies. The tenant is taken from the TOKEN's
# claims, never from the client-supplied `start` frame. Minting additionally
# requires TWILIO_AUTH_TOKEN to be set, because without it the /answer signature
# check is fail-soft (skipped) and anyone could ask us to mint a token.
#
# Residual risk (documented, accepted): a token is bearer-style, so a party who
# can observe it inside Twilio's TLS session could replay it before `exp`.
# Single-use enforcement needs shared state (Redis) and is deliberately out of
# scope here; the exp window is 300s.

_TOKEN_AUDIENCE = "twilio-media-stream"
_TOKEN_TTL_S = 300
_TOKEN_VERSION = "v1"


def _ws_token_secret() -> Optional[str]:
    """HMAC key for Media Streams tokens. None => mint and verify both refuse."""
    for var in ("TWILIO_WS_TOKEN_SECRET", "TELEPHONY_WS_TOKEN_SECRET", "TWILIO_AUTH_TOKEN"):
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _mint_ws_token(*, to_number: str, call_sid: str) -> Optional[str]:
    """Mint ``v1.<payload>.<sig>``. Returns None when no secret is configured."""
    secret = _ws_token_secret()
    if not secret:
        return None
    claims = {
        "aud": _TOKEN_AUDIENCE,
        "to": to_number,
        "sid": call_sid,
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


def _custom_param(start: dict, name: str) -> Optional[str]:
    """Case-insensitive read from ``start.customParameters``."""
    params = start.get("customParameters") or {}
    if not isinstance(params, dict):
        return None
    wanted = name.lower()
    for key, value in params.items():
        if str(key).lower() == wanted and isinstance(value, str):
            return value
    return None


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


def _twiml_stream_response(
    call_sid: str,
    from_number: str,
    to_number: str,
    token: Optional[str] = None,
) -> str:
    """Build the <Connect><Stream> TwiML pointing at our Media Streams WS.

    ``token`` (minted by the signature-verified /answer) is carried BOTH in the
    stream URL query string and as a <Parameter>, because those are the only two
    channels Twilio echoes back to the socket. The callSid/from/to Parameters are
    kept for Twilio-side debugging only — the socket treats them as untrusted and
    reads the tenant-bearing DID from the token instead.
    """
    ws_base = _public_base().replace("https://", "wss://").replace("http://", "ws://")
    stream_url = f"{ws_base}/api/v1/twilio/media-stream"
    if token:
        stream_url = f"{stream_url}?token={token}"
    token_param = (
        f'<Parameter name="token" value={quoteattr(token)}/>' if token else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Connect><Stream url={quoteattr(stream_url)}>"
        f'<Parameter name="callSid" value={quoteattr(call_sid)}/>'
        f'<Parameter name="from" value={quoteattr(from_number)}/>'
        f'<Parameter name="to" value={quoteattr(to_number)}/>'
        f"{token_param}"
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
    if not _bridge_enabled():
        logger.warning("twilio_answer rejected: TWILIO_BRIDGE_ENABLED is not set")
        return Response(status_code=404)
    # Minting a media-stream token off an UNVERIFIED webhook would hand the
    # credential to anyone who can POST here, so require the signature secret.
    if not (os.getenv("TWILIO_AUTH_TOKEN") or "").strip():
        logger.error(
            "twilio_answer refused: TWILIO_AUTH_TOKEN unset, so the webhook "
            "signature is unenforced and a media-stream token cannot be minted"
        )
        return Response(status_code=503)
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
    if not _is_supported_answer_direction(params.get("Direction")):
        logger.error(
            "twilio_answer refused: direction=%s has no durable pre-answer "
            "inbound admission path",
            str(params.get("Direction") or "missing")[:32],
        )
        return Response(status_code=503)
    logger.info(
        "Twilio answer webhook: callSid=%s from=%s to=%s",
        call_sid[:16], from_number, to_number,
    )
    token = _mint_ws_token(to_number=to_number, call_sid=call_sid)
    if not token:
        logger.error("twilio_answer refused: no media-stream token secret configured")
        return Response(status_code=503)
    twiml = _twiml_stream_response(call_sid, from_number, to_number, token)
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# POST /event — Twilio statusCallback (informational)
# ---------------------------------------------------------------------------

@router.post("/event")
async def twilio_event(request: Request):
    """Call lifecycle status callbacks. The Media Streams WebSocket lifecycle
    is the source of truth for session teardown, so this is informational —
    we normalise + log the status."""
    if not _bridge_enabled():
        return Response(status_code=404)
    if not (os.getenv("TWILIO_AUTH_TOKEN") or "").strip():
        logger.error("twilio_event refused: TWILIO_AUTH_TOKEN unset")
        return Response(status_code=503)
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
async def twilio_media_stream(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    """Twilio connects here after the ``<Connect><Stream>`` TwiML.

    Protocol (JSON text frames): connected -> start -> media* -> stop.
    Inbound media is base64 mu-law 8 kHz; we decode + feed the pipeline.
    Outbound audio + barge-in ``clear`` frames are emitted by TwilioMediaGateway.

    Refuses the upgrade unless the bridge is explicitly enabled, and does no work
    until the /answer-minted token verifies (query string preferred so we can
    reject pre-accept; ``start.customParameters.token`` as the fallback, since
    Twilio does not sign the handshake).
    """
    if not _bridge_enabled():
        logger.warning(
            "twilio media-stream rejected: TWILIO_BRIDGE_ENABLED is not set"
        )
        await websocket.close(code=1008, reason="Twilio bridge disabled")
        return

    # Preferred path: token on the stream URL, so an unauthenticated client is
    # rejected before the handshake is ever accepted.
    claims = _verify_ws_token(token)

    sem = _get_semaphore()
    if sem._value == 0:
        logger.warning("twilio media-stream rejected: at capacity")
        await websocket.close(code=1013, reason="At capacity")
        return

    await websocket.accept()
    # Resolved only after authentication — an unauthenticated socket must not
    # reach into the container at all.
    orchestrator = None
    voice_session = None
    stream_sid = None
    try:
        async with sem:
            while True:
                # Until authenticated + started, bound how long this socket may
                # hold a concurrency slot.
                if voice_session is None:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=_AUTH_DEADLINE_S
                    )
                else:
                    raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                event = data.get("event")

                if event == "start":
                    start = data.get("start", {}) or {}
                    if claims is None:
                        claims = _verify_ws_token(_custom_param(start, "token"))
                    if claims is None:
                        logger.warning(
                            "twilio media-stream rejected: unauthenticated start frame"
                        )
                        await websocket.close(code=1008, reason="Unauthorized")
                        return
                    stream_sid = start.get("streamSid") or data.get("streamSid")
                    call_sid = start.get("callSid", "")
                    # The tenant-selecting DID comes from the SIGNED token minted
                    # by /answer — NOT from start.customParameters, which any
                    # anonymous client could set to a victim's DID.
                    to_number = claims.get("to")
                    if call_sid and claims.get("sid") and call_sid != claims.get("sid"):
                        # Not fatal (Twilio can stream a child call leg), but the
                        # token is what binds the tenant, so just record it.
                        logger.warning(
                            "Twilio media start: callSid %s differs from token sid %s",
                            call_sid[:16], str(claims.get("sid"))[:16],
                        )
                    logger.info(
                        "Twilio media start: streamSid=%s callSid=%s to=%s",
                        (stream_sid or "?")[:16], (call_sid or "?")[:16],
                        to_number or "?",
                    )
                    orchestrator = _get_orchestrator()
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

    except asyncio.TimeoutError:
        logger.warning("twilio media-stream closed: no authenticated start frame")
        try:
            await websocket.close(code=1008, reason="Unauthorized")
        except Exception:  # noqa: BLE001 — already closing
            pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Twilio media-stream error: %s", exc, exc_info=True)
    finally:
        if stream_sid:
            _twilio_sessions.pop(stream_sid, None)
        if voice_session is not None and orchestrator is not None:
            try:
                await orchestrator.end_session(voice_session)
            except Exception as cleanup_exc:
                logger.debug("Twilio session cleanup error: %s", cleanup_exc)
        logger.info("Twilio media-stream closed: streamSid=%s", (stream_sid or "?")[:16])
