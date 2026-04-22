"""
Ask AI WebSocket - Simplified Voice Assistant Demo

One-click voice interaction without voice selection.
Uses Deepgram Aura-2 TTS (Google Chirp3-HD commented out for future switching).

Voice: Andromeda (aura-2-andromeda-en) - Customer service optimized
Sample Rate: 24000 Hz (Deepgram recommended for streaming TTS)

Day 41: Refactored to use VoiceOrchestrator for lifecycle management.
"""

import os
import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.services.ask_ai_session_config import build_ask_ai_session_config, ASK_AI_GREETING
from app.domain.models.conversation import Message, MessageRole

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Ask AI"])

# Concurrency cap: reject connections over the limit with WebSocket close 1013
# (RFC 6455 "Try Again Later") rather than silently accepting and starving.
# Set ASK_AI_MAX_SESSIONS env var to override; default 20 leaves headroom for
# Groq rate limits and per-session TTS WebSocket connections.
_MAX_CONCURRENT_ASK_AI = int(os.getenv("ASK_AI_MAX_SESSIONS", "20"))
_ask_ai_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _ask_ai_semaphore
    if _ask_ai_semaphore is None:
        _ask_ai_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_ASK_AI)
    return _ask_ai_semaphore


@router.websocket("/ws/ask-ai/{session_id}")
async def ask_ai_websocket(websocket: WebSocket, session_id: str):
    """
    Ask AI WebSocket — one-click voice assistant.

    Lifecycle is managed by VoiceOrchestrator; this endpoint only handles
    the WebSocket message loop (transport concern).
    """
    await websocket.accept()

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

    logger.info(f"Ask AI session started: {session_id}")

    # Get orchestrator from DI container
    from app.core.container import get_container

    container = get_container()

    voice_session = None
    receiver_task: Optional[asyncio.Task] = None

    # Hold semaphore slot for the duration of the session.
    async with sem:
        try:
            orchestrator = container.voice_orchestrator

            # 1. Create session via orchestrator
            config = build_ask_ai_session_config()
            voice_session = await orchestrator.create_voice_session(config)

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
                while gateway.is_session_active(call_id):
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
                                mark_playback_complete(call_id)
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
            logger.error(f"Ask AI error: {e}", exc_info=True)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        finally:
            if receiver_task and not receiver_task.done():
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass
            if voice_session:
                await container.voice_orchestrator.end_session(voice_session)
            logger.info(f"Ask AI session ended: {session_id}")
