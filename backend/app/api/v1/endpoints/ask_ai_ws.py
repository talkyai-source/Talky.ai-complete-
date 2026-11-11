"""
Ask AI WebSocket - Simplified Voice Assistant Demo

One-click voice interaction without voice selection, open to every visitor.
Uses AssemblyAI Universal-3.6 Pro STT + DeepSeek V4.1 Flash (thinking off) +
Cartesia Tessa (sonic-3) TTS; see ask_ai_session_config.

Sample Rate: 24000 Hz (Cartesia recommended for streaming TTS)

Day 41: Refactored to use VoiceOrchestrator for lifecycle management.
"""

import os
import json
import time
import uuid
import asyncio
import logging
import ipaddress
from collections import deque
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.domain.services.ask_ai_session_config import (
    build_ask_ai_session_config,
    ASK_AI_GREETING,
    ASK_AI_CONFIG,
)
from app.domain.models.conversation import Message, MessageRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ask AI"])

# Concurrency cap: reject connections over the limit with WebSocket close 1013
# (RFC 6455 "Try Again Later") rather than silently accepting and starving.
# Set ASK_AI_MAX_SESSIONS env var to override; default 20 leaves headroom for
# Groq rate limits and per-session TTS WebSocket connections.
_MAX_CONCURRENT_ASK_AI = int(os.getenv("ASK_AI_MAX_SESSIONS", "20"))
_ask_ai_semaphore: Optional[asyncio.Semaphore] = None

# The endpoint is public by design, so one client must not be able to hold every
# slot, and a forgotten tab must not hold one forever. Each slot runs paid STT,
# TTS and LLM sessions. Override with the env vars below.
_MAX_SESSIONS_PER_IP = int(os.getenv("ASK_AI_MAX_SESSIONS_PER_IP", "3"))
# 5 minutes: enough for a demo conversation, and with sign-in gone (12 Nov
# 2026) a third of the old 15-minute ceiling per slot.
_MAX_SESSION_SECONDS = float(os.getenv("ASK_AI_MAX_SESSION_SECONDS", "300"))
_IDLE_SECONDS = float(os.getenv("ASK_AI_IDLE_SECONDS", "180"))
_sessions_by_ip: dict[str, int] = {}

# Start budgets for the open demo, over rolling windows: a visitor may start a
# few sessions an hour and a day, and the whole site a bounded number a day, so
# a script cycling short sessions cannot run up the provider bill. Kept in this
# process's memory, like the caps above; a restart forgets them.
_STARTS_PER_IP_PER_HOUR = int(os.getenv("ASK_AI_STARTS_PER_IP_PER_HOUR", "6"))
_STARTS_PER_IP_PER_DAY = int(os.getenv("ASK_AI_STARTS_PER_IP_PER_DAY", "20"))
_STARTS_PER_DAY = int(os.getenv("ASK_AI_STARTS_PER_DAY", "400"))
_starts_by_ip: dict[str, deque] = {}
_starts_all: deque = deque()
_MAX_TRACKED_IPS = 50_000


def _visitor_key(client_ip: str) -> str:
    """Who a start is counted against: an IPv4 address, or an IPv6 /64. One
    host is normally given a whole /64, so single IPv6 addresses cost nothing
    to rotate through."""
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if address.version == 6 and address.ipv4_mapped is None:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(getattr(address, "ipv4_mapped", None) or address)


def _admit_start(client_ip: str, now: float) -> Optional[str]:
    """Count a session start against the budgets; the name of the budget that
    refuses it, or None when it may start."""
    client_ip = _visitor_key(client_ip)
    day_ago, hour_ago = now - 86_400, now - 3_600
    while _starts_all and _starts_all[0] <= day_ago:
        _starts_all.popleft()
    if len(_starts_by_ip) > _MAX_TRACKED_IPS:
        for ip in [ip for ip, times in _starts_by_ip.items() if times[-1] <= day_ago]:
            del _starts_by_ip[ip]
    starts = _starts_by_ip.get(client_ip) or deque()
    while starts and starts[0] <= day_ago:
        starts.popleft()
    if len(_starts_all) >= _STARTS_PER_DAY:
        return "site_daily"
    if len(starts) >= _STARTS_PER_IP_PER_DAY:
        return "visitor_daily"
    if sum(1 for started in starts if started > hour_ago) >= _STARTS_PER_IP_PER_HOUR:
        return "visitor_hourly"
    starts.append(now)
    _starts_by_ip[client_ip] = starts
    _starts_all.append(now)
    if len(_starts_all) == int(_STARTS_PER_DAY * 0.8):
        # When the site budget runs out the demo refuses everyone until the
        # window rolls; this line is the early warning for whoever watches logs.
        logger.warning("Ask AI site start budget 80%% used: %d of %d in the last 24 h",
                       len(_starts_all), _STARTS_PER_DAY)
    return None


def _client_ip(websocket: WebSocket) -> str:
    """Real client address. Forwarded headers count only when the peer is our
    own proxy (nginx sets X-Real-IP and APPENDS to X-Forwarded-For, so its
    leftmost entry is caller-supplied); see resolve_client_ip."""
    from app.core.api_security_middleware import resolve_client_ip

    return resolve_client_ip(websocket)


def _get_semaphore() -> asyncio.Semaphore:
    global _ask_ai_semaphore
    if _ask_ai_semaphore is None:
        _ask_ai_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_ASK_AI)
    return _ask_ai_semaphore


# The prices the demo may quote are the ones the public pricing page shows,
# read from the same two endpoints (QA F11-05). Cached as long as the
# catalogue caches its Stripe reads; a failure isn't cached, and a slow or
# failed read leaves the demo pointing to the pricing page.
_PRICE_LIST_TTL_SECONDS = 300.0
_PRICE_LIST_WAIT_SECONDS = 2.0
_price_list_cache: Optional[tuple[float, Optional[str]]] = None


async def _live_price_list(pool) -> Optional[str]:
    global _price_list_cache
    from fastapi import HTTPException

    from app.api.v1.endpoints import plans
    from app.domain.services.ask_ai_constants import format_price_list

    now = time.monotonic()
    if _price_list_cache is not None and _price_list_cache[0] > now:
        return _price_list_cache[1]
    catalogue = await plans.list_plans(pool)
    try:
        payg = await plans.pay_as_you_go(pool)
    except HTTPException:
        payg = None  # not offered, or its bundles can't be read
    text = format_price_list(catalogue, payg)
    _price_list_cache = (now + _PRICE_LIST_TTL_SECONDS, text)
    return text


async def _session_price_list(pool) -> Optional[str]:
    try:
        return await asyncio.wait_for(_live_price_list(pool), timeout=_PRICE_LIST_WAIT_SECONDS)
    except Exception as exc:
        logger.warning("ask_ai_price_list_unavailable err=%s; the demo points to the pricing page",
                       type(exc).__name__)
        return None


# Cache synthesized greeting bytes in memory — the text is static so there is
# no reason to hit Cartesia on every button press.
_greeting_audio_cache: Optional[bytes] = None
_greeting_cache_lock = asyncio.Lock()


@router.get("/ask-ai/greeting")
async def get_greeting_audio():
    """
    Synthesize the Ask AI greeting and return raw float32 PCM at 24 kHz.

    The frontend fetches this before opening the WebSocket and plays it
    immediately so the caller hears a greeting with no perceptible delay.
    The backend seeds the LLM conversation history with the same greeting
    text so the model never re-greets.
    """
    global _greeting_audio_cache

    async with _greeting_cache_lock:
        if _greeting_audio_cache is not None:
            return Response(
                content=_greeting_audio_cache,
                media_type="application/octet-stream",
                headers={
                    "X-Audio-Sample-Rate": str(ASK_AI_CONFIG["sample_rate"]),
                    "X-Audio-Encoding": "float32",
                    "X-Audio-Channels": "1",
                    "Cache-Control": "public, max-age=3600",
                },
            )

        from app.domain.services.credential_resolver import get_credential_resolver
        from app.infrastructure.tts.cartesia import CartesiaTTSProvider

        resolver = get_credential_resolver()
        api_key = await resolver.resolve("cartesia", tenant_id=None)

        tts = CartesiaTTSProvider()
        await tts.initialize(
            {
                "api_key": api_key,
                "voice_id": ASK_AI_CONFIG["voice_id"],
                "model_id": ASK_AI_CONFIG["model_id"],
                "sample_rate": ASK_AI_CONFIG["sample_rate"],
            }
        )

        try:
            chunks: list[bytes] = []
            async for chunk in tts.stream_synthesize(
                text=ASK_AI_GREETING,
                voice_id=ASK_AI_CONFIG["voice_id"],
                sample_rate=ASK_AI_CONFIG["sample_rate"],
            ):
                chunks.append(chunk.data)
        finally:
            await tts.cleanup()

        _greeting_audio_cache = b"".join(chunks)

    return Response(
        content=_greeting_audio_cache,
        media_type="application/octet-stream",
        headers={
            "X-Audio-Sample-Rate": str(ASK_AI_CONFIG["sample_rate"]),
            "X-Audio-Encoding": "float32",
            "X-Audio-Channels": "1",
            "Cache-Control": "public, max-age=3600",
        },
    )


def _is_origin_allowed(websocket: WebSocket) -> bool:
    """Only the website's own pages may open the open demo. Browsers always
    send Origin on a socket upgrade, so a request without one is a script and
    is refused (12 Nov 2026, when sign-in stopped being the gate)."""
    origin = websocket.headers.get("origin")
    if not origin:
        return False
    from app.core.config import get_settings

    return origin in get_settings().allowed_origins


@router.websocket("/ws/ask-ai/{session_id}")
async def ask_ai_websocket(websocket: WebSocket, session_id: str):
    """
    Ask AI WebSocket — one-click voice assistant.

    Lifecycle is managed by VoiceOrchestrator; this endpoint only handles
    the WebSocket message loop (transport concern).
    """
    if not _is_origin_allowed(websocket):
        logger.warning("Ask AI rejecting cross-origin upgrade from %r", websocket.headers.get("origin"))
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    await websocket.accept()

    # Owner decision 12 Nov 2026: the website's hero demo talks to visitors
    # without sign-in (this reverses QA F11-06). The origin check above and the
    # capacity, per-client and session-length caps below bound its cost.

    # Reject over-capacity connections immediately — before touching any provider.
    # RFC 6455 close code 1013 = "Try Again Later".
    sem = _get_semaphore()
    if sem._value == 0:
        logger.warning(
            "Ask AI at capacity (%d sessions), rejecting %s",
            _MAX_CONCURRENT_ASK_AI, session_id,
        )
        await websocket.send_json({"type": "error", "message": "Server at capacity, please retry shortly"})
        await websocket.close(code=1013)
        return

    client_ip = _client_ip(websocket)
    if _sessions_by_ip.get(client_ip, 0) >= _MAX_SESSIONS_PER_IP:
        logger.warning("Ask AI per-client cap reached ip=%s, rejecting %s", client_ip, session_id)
        await websocket.send_json({"type": "error", "message": "Too many open sessions, please close one and retry"})
        await websocket.close(code=1013)
        return

    refused = _admit_start(client_ip, time.monotonic())
    if refused:
        logger.warning("Ask AI start budget reached budget=%s ip=%s, rejecting %s", refused, client_ip, session_id)
        await websocket.send_json({"type": "error", "message": "The assistant is busy right now. Please try again later."})
        await websocket.close(code=1013)
        return

    logger.info(f"Ask AI session started: {session_id}")

    # Get orchestrator from DI container
    from app.core.container import get_container

    container = get_container()

    voice_session = None
    receiver_task: Optional[asyncio.Task] = None

    # Hold semaphore slot for the duration of the session.
    async with sem:
        # Counted here, with no await before the try, so the finally below is
        # the only way out and always gives the slot back.
        _sessions_by_ip[client_ip] = _sessions_by_ip.get(client_ip, 0) + 1
        try:
            orchestrator = container.voice_orchestrator

            # 1. Create session via orchestrator
            config = build_ask_ai_session_config()
            voice_session = await orchestrator.create_voice_session(config)

            # The prices the demo may quote: the live list (QA F11-05).
            voice_session.call_session._ask_ai_price_list = await _session_price_list(
                getattr(container, "db_pool", None)
            )

            # Seed conversation history with the greeting the client already played.
            # This tells the LLM what was said so it never re-greets.
            voice_session.call_session.conversation_history.append(
                Message(role=MessageRole.ASSISTANT, content=ASK_AI_GREETING)
            )

            # 2. Send ready message
            await websocket.send_json(
                {
                    "type": "ready",
                    "session_id": session_id,
                    "call_id": voice_session.call_id,
                    "sample_rate": config.gateway_sample_rate,
                    "audio_format": "s16le",
                }
            )

            call_id = voice_session.call_id
            gateway = voice_session.media_gateway

            async def _receive_messages() -> None:
                """
                Continuously consume websocket frames.

                Running this concurrently with greeting prevents stale mic audio
                buildup and keeps audio flow real-time.
                """
                started = time.monotonic()
                while gateway.is_session_active(call_id):
                    # Hard length cap, and an idle cap on conversational
                    # activity (transcripts / completed turns - an open mic
                    # keeps sending frames, so frames alone prove nothing).
                    limit_reason = None
                    if time.monotonic() - started > _MAX_SESSION_SECONDS:
                        limit_reason = "max_duration"
                    elif voice_session.call_session.is_stale(_IDLE_SECONDS):
                        limit_reason = "idle_timeout"
                    if limit_reason:
                        logger.info("Ask AI session %s ended: %s", session_id, limit_reason)
                        try:
                            await websocket.send_json({"type": "session_limit", "reason": limit_reason})
                        except (WebSocketDisconnect, RuntimeError):
                            pass
                        await gateway.on_call_ended(call_id, limit_reason)
                        break
                    try:
                        message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                        message_type = message.get("type")

                        # Starlette emits explicit disconnect frames; stop reading immediately.
                        if message_type == "websocket.disconnect":
                            logger.info(f"Ask AI websocket disconnected: {session_id}")
                            break

                        if message_type != "websocket.receive":
                            continue

                        audio_data = message.get("bytes")
                        if isinstance(audio_data, (bytes, bytearray)):
                            if not audio_data:
                                continue

                            await gateway.on_audio_received(call_id, bytes(audio_data))
                            continue

                        text_data = message.get("text")
                        if not text_data:
                            continue
                        try:
                            data = json.loads(text_data)
                        except json.JSONDecodeError:
                            logger.debug(
                                f"Ignoring non-JSON websocket text frame: {text_data[:120]}"
                            )
                            continue
                        if data.get("type") == "end_call":
                            await gateway.on_call_ended(call_id, "user_ended")
                            break
                        if data.get("type") == "playback_complete":
                            mark_playback_complete = getattr(
                                gateway, "mark_playback_complete", None
                            )
                            if callable(mark_playback_complete):
                                mark_playback_complete(call_id, data.get("utterance_id"))
                            continue

                    except asyncio.TimeoutError:
                        try:
                            await websocket.send_json({"type": "heartbeat"})
                        except (WebSocketDisconnect, RuntimeError):
                            break
                        continue
                    except WebSocketDisconnect:
                        break
                    except RuntimeError as e:
                        if "disconnect message has been received" in str(e):
                            logger.info(
                                f"Ask AI websocket closed after disconnect: {session_id}"
                            )
                            break
                        raise

            # 3. Start pipeline before greeting so STT queue is active immediately.
            await orchestrator.start_pipeline(voice_session, websocket)

            # 4. Start frame receiver before greeting to avoid buffered stale audio.
            receiver_task = asyncio.create_task(_receive_messages())

            # 5. Greeting is played client-side (pre-fetched audio) — no server greeting.

            # 6. Keep endpoint alive until receiver exits (disconnect/end_call).
            await receiver_task

        except WebSocketDisconnect:
            logger.info(f"Ask AI disconnected: {session_id}")
        except Exception as e:
            # QA F2-07: the exception text stays in the log, under a reference
            # the visitor can quote; the page gets a fixed message.
            reference = uuid.uuid4().hex[:12]
            logger.error("Ask AI error ref=%s session=%s: %s", reference, session_id, e, exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error", "reference": reference,
                    "message": f"Something went wrong on our side. Please try again (reference {reference}).",
                })
                # Close the socket so the client's onclose fires immediately and
                # the UI leaves its "speaking" state. Without this the connection
                # lingered until the 30s idle timeout while the user was stuck.
                await websocket.close(code=1011, reason="server error")
            except Exception:
                pass
        finally:
            remaining = _sessions_by_ip.get(client_ip, 1) - 1
            if remaining > 0:
                _sessions_by_ip[client_ip] = remaining
            else:
                _sessions_by_ip.pop(client_ip, None)
            if receiver_task and not receiver_task.done():
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass
            if voice_session:
                await container.voice_orchestrator.end_session(voice_session)
            logger.info(f"Ask AI session ended: {session_id}")
