"""Assistant VOICE WebSocket — speak to the tool-enabled floating assistant.

This is the voice twin of `/assistant/chat`. It reuses our OWN STT (Deepgram
Flux) and TTS (Cartesia) as bare utilities and bridges them to the SAME
tool-calling agent the text chat uses (`stream_assistant_reply`), so voice mode
has identical tools and access — including the new `create_campaign` flow that
asks for fields one at a time.

    mic PCM16 @16k ──▶ Deepgram Flux ──▶ stream_assistant_reply (tools) ──▶ Cartesia ──▶ float32 @24k
                         │ stt_partial/stt_final          │ assistant_token/…/edit_proposal   │ tts audio
                         └──────────── all emitted to the client as a LIVE TRANSCRIPT ─────────┘

Design notes:
  * The phone-call turn machinery (VoicePipelineService / VoiceOrchestrator:
    turn_ender, barge-in, silence monitor, machine detection) is deliberately
    bypassed — those assume a live PSTN call. We drive STT and TTS directly.
  * Server→client events reuse the text-chat names (assistant_message_start /
    assistant_token / assistant_message_end / assistant_typing / edit_proposal /
    proposal_result) so the frontend renders the live transcript with the SAME
    code path, plus voice-only events: ready / stt_partial / stt_final /
    tts_start / tts_end / error.
  * Turns are processed sequentially (one agent+TTS turn at a time). Browser
    echo cancellation (getUserMedia echoCancellation:true) keeps the agent's own
    voice out of the mic; barge-in is a follow-up.

Wire protocol
  Client → Server:
    {"type":"auth","token":"<JWT>"}            once, immediately after onopen
    <binary>                                    Int16 PCM, mono, 16 kHz mic audio
    {"type":"apply_proposal","proposal_id":…}   confirm a create/edit proposal
    {"type":"reject_proposal","proposal_id":…}
    {"type":"ping"}
  Server → Client:
    {"type":"ready", ...}
    {"type":"stt_partial","text":…}             live partial user transcript
    {"type":"stt_final","text":…}               finalized user utterance
    {"type":"assistant_typing","content":bool}
    {"type":"assistant_message_start","id":…}
    {"type":"assistant_token","id":…,"delta":…}
    {"type":"assistant_message_end","id":…,"content":…}
    {"type":"edit_proposal", …}                 confirm card (e.g. create_campaign)
    {"type":"proposal_result", …}
    {"type":"tts_start","sample_rate":24000,"encoding":"float32"}
    <binary>                                    Float32 PCM, mono, 24 kHz TTS audio
    {"type":"tts_end"}
    {"type":"error","content":…}
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.api.v1.dependencies import get_db_client
from app.api.v1.endpoints.assistant_ws import _is_origin_allowed, _resolve_ws_token
from app.core.jwt_security import JWTValidationError, decode_and_validate_token
from app.domain.models.conversation import AudioChunk
from app.infrastructure.assistant.model_config import get_tenant_assistant_model
from app.infrastructure.assistant.proposals import (
    clear_proposal,
    get_proposal,
    store_proposal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["Assistant"])

# Voice defaults — the same known-good Cartesia voice the one-click Ask AI demo
# uses. STT is Deepgram Flux at 16 kHz linear16 (what the mic sends); TTS is
# Cartesia sonic-3 at 24 kHz (float32 out, played natively by Web Audio).
_STT_SAMPLE_RATE = 16000
_TTS_SAMPLE_RATE = 24000
# The browser AudioWorklet emits 128-sample / 8ms / 256-byte frames. Deepgram
# Flux's PCM validator REJECTS anything < 10ms, so raw worklet frames would be
# discarded and NOTHING would ever transcribe. Aggregate to Flux's optimal
# 40ms / 1280-byte chunk (256 divides 1280 exactly) before handing to STT —
# the same accumulation the browser_media_gateway does for the phone path.
_MIC_CHUNK_BYTES = 1280
_TTS_VOICE_ID = "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"  # Cartesia "Tessa"
_TTS_MODEL_ID = "sonic-3"

_VOICE_GREETING = (
    "Hi! I'm your voice assistant. I can pull up your campaigns, calls and stats, "
    "or set up a brand-new campaign — just tell me what you'd like to do."
)

# Cap concurrent voice sessions (each holds a Deepgram + Cartesia WebSocket).
_MAX_CONCURRENT = 20
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


async def _resolve_tenant(user_id: str, db_client: Any) -> Optional[str]:
    try:
        profile = (
            db_client.table("user_profiles").select("tenant_id").eq("id", user_id).single().execute()
        )
        if profile.data and profile.data.get("tenant_id"):
            return str(profile.data["tenant_id"])
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("assistant_voice: profile lookup failed: %s", exc)
    return None


@router.websocket("/voice")
async def assistant_voice(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
):
    # --- origin + auth (same surface as /assistant/chat) --------------------
    if not _is_origin_allowed(websocket):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()

    sem = _get_semaphore()
    if sem._value == 0:  # noqa: SLF001 - fast reject before touching providers
        await websocket.send_json({"type": "error", "content": "Voice is at capacity — please retry shortly."})
        await websocket.close(code=1013)
        return

    resolved_token = await _resolve_ws_token(websocket, token)
    if not resolved_token:
        await websocket.send_json({"type": "error", "content": "Authentication required."})
        await websocket.close(code=1008, reason="Missing auth")
        return
    try:
        payload = decode_and_validate_token(resolved_token)
    except JWTValidationError:
        await websocket.send_json({"type": "error", "content": "Invalid or expired token."})
        await websocket.close(code=1008, reason="Invalid token")
        return

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        await websocket.close(code=1008, reason="Invalid token")
        return
    user_id = str(user_id)

    db_client = get_db_client()
    tenant_id = await _resolve_tenant(user_id, db_client)
    if not tenant_id:
        await websocket.send_json({"type": "error", "content": "User profile not found."})
        await websocket.close(code=1008, reason="No tenant")
        return

    # RLS tenant context for this task — the agent's tools query via .table()
    # which reads the tenant from a contextvar (HTTP sets it in middleware; a WS
    # must set it explicitly, else every query runs as the NIL tenant).
    from app.core.security.tenant_isolation import set_current_tenant_id

    set_current_tenant_id(tenant_id)

    session_id = f"voice_{uuid.uuid4().hex[:12]}"

    async with sem:
        await _run_voice_session(
            websocket=websocket,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            db_client=db_client,
        )


async def _run_voice_session(
    *,
    websocket: WebSocket,
    session_id: str,
    tenant_id: str,
    user_id: str,
    conversation_id: Optional[str],
    db_client: Any,
) -> None:
    """Own the STT + TTS providers and the three concurrent loops for one
    voice session, tearing everything down cleanly on disconnect."""
    from app.domain.services.credential_resolver import get_credential_resolver
    from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
    from app.infrastructure.tts.cartesia import CartesiaTTSProvider

    # A closed socket must never raise out of a send — one helper swallows the
    # post-close RuntimeError/WebSocketDisconnect for every emitter below.
    active = True

    async def send_json(data: Dict[str, Any]) -> None:
        nonlocal active
        if not active:
            return
        try:
            await websocket.send_json(jsonable_encoder(data))
        except (WebSocketDisconnect, RuntimeError):
            active = False

    async def send_bytes(data: bytes) -> None:
        nonlocal active
        if not active or not data:
            return
        try:
            await websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            active = False

    # --- resolve provider keys ---------------------------------------------
    resolver = get_credential_resolver()
    try:
        deepgram_key = await resolver.resolve("deepgram", tenant_id=tenant_id)
        cartesia_key = await resolver.resolve("cartesia", tenant_id=tenant_id)
    except Exception as exc:
        logger.error("assistant_voice: credential resolve failed: %s", exc)
        await send_json({"type": "error", "content": "Voice providers are not configured."})
        await websocket.close(code=1011)
        return
    if not deepgram_key or not cartesia_key:
        await send_json({"type": "error", "content": "Voice providers are not configured (missing STT/TTS key)."})
        await websocket.close(code=1011)
        return

    stt = DeepgramFluxSTTProvider()
    tts = CartesiaTTSProvider()
    try:
        # eot/eager thresholds MUST be passed — Flux's initialize() defaults to a
        # 5s end-of-turn timeout with eager OFF when these keys are omitted, which
        # makes every turn feel ~5s unresponsive. Match the Ask-AI voice config.
        await stt.initialize({
            "api_key": deepgram_key,
            "sample_rate": _STT_SAMPLE_RATE,
            "encoding": "linear16",
            "eot_threshold": 0.7,
            "eager_eot_threshold": 0.5,
            "eot_timeout_ms": 3000,
        })
        await tts.initialize({
            "api_key": cartesia_key,
            "voice_id": _TTS_VOICE_ID,
            "model_id": _TTS_MODEL_ID,
            "sample_rate": _TTS_SAMPLE_RATE,
        })
    except Exception as exc:
        logger.error("assistant_voice: provider init failed: %s", exc, exc_info=True)
        await send_json({"type": "error", "content": "Could not start the voice pipeline."})
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
        return

    # --- conversation history (resume same thread as text chat) ------------
    current_conversation_id = conversation_id
    messages_history: List[Dict[str, Any]] = []
    if conversation_id:
        try:
            conv = (
                db_client.table("assistant_conversations")
                .select("messages")
                .eq("id", conversation_id)
                .eq("tenant_id", tenant_id)
                .single()
                .execute()
            )
            raw = conv.data.get("messages", []) if conv.data else []
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, list):
                messages_history = raw
        except Exception:
            pass

    async def persist(title_hint: str = "") -> None:
        nonlocal current_conversation_id
        try:
            if current_conversation_id:
                db_client.table("assistant_conversations").update({
                    "messages": messages_history,
                    "message_count": len(messages_history),
                    "last_message_at": datetime.utcnow().isoformat(),
                }).eq("id", current_conversation_id).execute()
            else:
                title = (title_hint or "Voice conversation")[:50]
                created = (
                    db_client.table("assistant_conversations")
                    .insert({
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "messages": messages_history,
                        "message_count": len(messages_history),
                        "title": title,
                        "started_at": datetime.utcnow().isoformat(),
                        "last_message_at": datetime.utcnow().isoformat(),
                    })
                    .single()
                    .execute()
                )
                row = created.data[0] if isinstance(created.data, list) else created.data
                if row and row.get("id"):
                    current_conversation_id = str(row["id"])
                    await send_json({"type": "conversation_created", "conversation_id": current_conversation_id})
        except Exception:
            logger.exception("assistant_voice: persist failed")

    # Queues bridging the loops. audio_queue: mic frames → STT; final_queue:
    # end-of-turn user text → agent turn worker. audio_queue is BOUNDED
    # (~4s of 8ms mic frames): if the STT stream ever dies, the mic keeps
    # producing with nothing draining, and an unbounded queue would grow
    # without limit for the rest of the session. Overflow drops the newest
    # frame instead (see receive_loop).
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=512)
    final_queue: asyncio.Queue = asyncio.Queue()

    # Barge-in state. speak_state["active"] is True while TTS audio streams;
    # current_turn["task"] is the in-flight greeting/agent turn. When the user
    # starts speaking mid-agent-speech we cancel that task (stops the backend
    # TTS) and tell the client to stop playback.
    speak_state = {"active": False}
    current_turn: Dict[str, Any] = {"task": None}

    async def barge_in() -> None:
        t = current_turn["task"]
        if t is not None and not t.done():
            t.cancel()
        await send_json({"type": "tts_interrupt"})

    tenant_model = await get_tenant_assistant_model(db_client, tenant_id)

    await send_json({
        "type": "ready",
        "session_id": session_id,
        "conversation_id": current_conversation_id or "new",
        "stt_sample_rate": _STT_SAMPLE_RATE,
        "tts_sample_rate": _TTS_SAMPLE_RATE,
    })

    async def audio_gen() -> AsyncIterator[AudioChunk]:
        """Feed mic frames to STT until the session ends (sentinel None)."""
        while active:
            chunk = await audio_queue.get()
            if chunk is None:
                return
            yield chunk

    async def speak(text: str) -> None:
        """Synthesize `text` to the client as float32 @24k, framed by markers.

        Cancellation-safe: a barge-in cancels the enclosing turn task, which
        raises CancelledError here — we stop streaming immediately and still
        send tts_end so the client leaves the 'speaking' state."""
        if not text.strip() or not active:
            return
        speak_state["active"] = True
        await send_json({"type": "tts_start", "sample_rate": _TTS_SAMPLE_RATE, "encoding": "float32"})
        try:
            async for ac in tts.stream_synthesize(
                text=text, voice_id=_TTS_VOICE_ID, sample_rate=_TTS_SAMPLE_RATE, call_id=session_id
            ):
                if not active:
                    break
                await send_bytes(ac.data)  # Cartesia yields float32 PCM bytes
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("assistant_voice: TTS synth failed: %s", exc)
        finally:
            speak_state["active"] = False
            await send_json({"type": "tts_end"})

    async def run_agent_turn(user_text: str) -> None:
        """One agent turn: stream tokens/proposals as live transcript, then speak
        the final answer. Mirrors the /assistant/chat event contract."""
        from app.infrastructure.assistant.streaming import stream_assistant_reply

        messages_history.append({
            "role": "user",
            "content": user_text,
            "timestamp": datetime.utcnow().isoformat(),
        })
        await send_json({"type": "assistant_typing", "content": True})

        # Keep a LONG window: a create-campaign flow collects 5 fields across
        # many short voice turns (with confirmations + suggestions). A 10-message
        # window let the earliest answers (name, goal) scroll out of context, so
        # the model re-asked them forever. 40 short turns holds the whole flow.
        chat_messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages_history[-40:]
        ]

        current_msg_id: Optional[str] = None
        buf: List[str] = []
        spoken = False
        try:
            async for ev in stream_assistant_reply(
                chat_messages=chat_messages,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=current_conversation_id,
                db_client=db_client,
                model=tenant_model,
            ):
                etype = ev.get("type")
                if etype == "token":
                    if current_msg_id is None:
                        current_msg_id = str(uuid.uuid4())
                        buf = []
                        await send_json({"type": "assistant_typing", "content": False})
                        await send_json({"type": "assistant_message_start", "id": current_msg_id})
                    buf.append(ev.get("delta", ""))
                    await send_json({"type": "assistant_token", "id": current_msg_id, "delta": ev.get("delta", "")})

                elif etype == "tool_start":
                    if current_msg_id is not None:
                        text = "".join(buf)
                        await send_json({"type": "assistant_message_end", "id": current_msg_id, "content": text})
                        if text.strip():
                            messages_history.append({"role": "assistant", "content": text, "timestamp": datetime.utcnow().isoformat()})
                        current_msg_id = None
                        buf = []
                    await send_json({"type": "assistant_typing", "content": True})

                elif etype == "final":
                    final_text = ev.get("content", "") or "".join(buf)
                    if current_msg_id is not None:
                        await send_json({"type": "assistant_message_end", "id": current_msg_id, "content": final_text})
                    else:
                        msg_id = str(uuid.uuid4())
                        if not final_text.strip():
                            final_text = "Okay."
                        await send_json({"type": "assistant_message_start", "id": msg_id})
                        await send_json({"type": "assistant_message_end", "id": msg_id, "content": final_text})
                    if final_text.strip():
                        messages_history.append({"role": "assistant", "content": final_text, "timestamp": datetime.utcnow().isoformat()})
                    current_msg_id = None
                    buf = []
                    await speak(final_text)
                    spoken = True

                elif etype == "proposal":
                    # A create/edit tool returned a preview → confirm card. Speak
                    # a short spoken cue so the voice user knows to look/confirm.
                    if current_msg_id is not None:
                        text = "".join(buf)
                        await send_json({"type": "assistant_message_end", "id": current_msg_id, "content": text})
                        if text.strip():
                            messages_history.append({"role": "assistant", "content": text, "timestamp": datetime.utcnow().isoformat()})
                        current_msg_id = None
                        buf = []
                    await send_json({"type": "assistant_typing", "content": False})
                    proposal = store_proposal(
                        tool=ev.get("tool", ""),
                        args=ev.get("args") or {},
                        result=ev.get("result") or {},
                        tenant_id=tenant_id,
                        conversation_id=current_conversation_id,
                    )
                    await send_json({
                        "type": "edit_proposal",
                        "proposal_id": proposal["proposal_id"],
                        "tool": proposal["tool"],
                        "note": proposal["note"],
                        "warnings": proposal["warnings"],
                        "changes": proposal["changes"],
                        "campaigns": proposal["campaigns"],
                    })
                    messages_history.append({
                        "role": "assistant",
                        "content": "_Proposed changes — awaiting your approval._",
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                    cue = proposal.get("note") or "I've prepared that — review it and tap Confirm to apply."
                    await speak(cue)
                    spoken = True

                elif etype == "error":
                    err_text = ev.get("content", "Sorry, I hit an error.")
                    await send_json({"type": "assistant_typing", "content": False})
                    await send_json({"type": "assistant_message", "content": err_text})
                    messages_history.append({"role": "assistant", "content": err_text, "timestamp": datetime.utcnow().isoformat()})
                    await speak(err_text)
                    spoken = True
        except Exception as exc:
            logger.error("assistant_voice: agent turn failed: %s", exc, exc_info=True)
            await send_json({"type": "error", "content": "Something went wrong on that turn."})
        finally:
            await send_json({"type": "assistant_typing", "content": False})
            if not spoken and current_msg_id is not None:
                # A dangling text bubble with no final event — close + speak it.
                text = "".join(buf)
                await send_json({"type": "assistant_message_end", "id": current_msg_id, "content": text})
                if text.strip():
                    messages_history.append({"role": "assistant", "content": text, "timestamp": datetime.utcnow().isoformat()})
                    await speak(text)
            await persist(user_text)

    async def stt_loop() -> None:
        """Consume transcripts; stream partials, enqueue finalized utterances.

        Barge-in: the FIRST partial of a new utterance while the agent is
        speaking cancels the agent's turn (stops its TTS) so the user is never
        talked over."""
        try:
            barged = False
            async for tc in stt.stream_transcribe(audio_gen(), language="en"):
                if not active:
                    break
                text = getattr(tc, "text", "") or ""
                is_final = bool(getattr(tc, "is_final", False))
                if is_final and text.strip():
                    await send_json({"type": "stt_final", "text": text})
                    await final_queue.put(text)
                    barged = False
                elif text.strip():
                    if not barged and speak_state["active"]:
                        barged = True
                        await barge_in()
                    await send_json({"type": "stt_partial", "text": text})
        except Exception as exc:
            logger.warning("assistant_voice: STT loop ended: %s", exc)
        finally:
            # If STT stopped while the session is still live, the user would
            # otherwise speak into silence with no explanation. Say so.
            if active:
                await send_json({
                    "type": "error",
                    "content": "Speech recognition disconnected — close and reopen voice mode to continue.",
                })

    async def turn_loop() -> None:
        """Process finalized utterances one at a time. Each turn runs as its own
        cancellable task so a barge-in can stop it mid-flight; if barge-in fired,
        the interrupting utterance is the next final and starts a fresh turn."""
        while active:
            user_text = await final_queue.get()
            if user_text is None:
                return
            # Cancel any still-running turn before starting the next.
            prev = current_turn["task"]
            if prev is not None and not prev.done():
                prev.cancel()
                try:
                    await prev
                except (asyncio.CancelledError, Exception):
                    pass
            current_turn["task"] = asyncio.create_task(run_agent_turn(user_text))
            try:
                await current_turn["task"]
            except (asyncio.CancelledError, Exception):
                pass

    async def apply_proposal(proposal_id: Optional[str]) -> None:
        proposal = get_proposal(proposal_id, tenant_id) if isinstance(proposal_id, str) else None
        if not proposal:
            await send_json({"type": "proposal_result", "proposal_id": proposal_id, "applied": False,
                             "error": "That proposal is no longer available — please ask again."})
            return
        try:
            from app.infrastructure.assistant.tools.dispatch import dispatch_tool

            result = await dispatch_tool(
                proposal["tool"], tenant_id, db_client, current_conversation_id,
                {**proposal["args"], "confirm": True},
            )
            applied = (
                isinstance(result, dict)
                and (result.get("applied") is True or result.get("success") is True)
                and not result.get("error")
            )
            err = result.get("error") if isinstance(result, dict) else "Apply failed"
            clear_proposal(proposal_id)
            await send_json({
                "type": "proposal_result",
                "proposal_id": proposal_id,
                "applied": applied,
                "changes": proposal["changes"],
                "campaigns": proposal["campaigns"],
                "error": None if applied else (err or "Could not apply."),
            })
            spoken_note = (result.get("note") if isinstance(result, dict) else None) or (
                "Done." if applied else "I couldn't apply that."
            )
            messages_history.append({"role": "assistant", "content": spoken_note, "timestamp": datetime.utcnow().isoformat()})
            await persist()
            await speak(spoken_note)
        except Exception as exc:
            logger.error("assistant_voice: apply_proposal failed: %s", exc, exc_info=True)
            clear_proposal(proposal_id)
            await send_json({"type": "proposal_result", "proposal_id": proposal_id, "applied": False,
                             "error": "Something went wrong applying that."})

    async def receive_loop() -> None:
        nonlocal active
        mic_buf = bytearray()
        while active:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=60.0)
            except asyncio.TimeoutError:
                await send_json({"type": "heartbeat"})
                continue
            except (WebSocketDisconnect, RuntimeError):
                break

            if message.get("type") == "websocket.disconnect":
                break
            if message.get("type") != "websocket.receive":
                continue

            raw = message.get("bytes")
            if isinstance(raw, (bytes, bytearray)) and raw:
                # Aggregate the 8ms/256-byte worklet frames into 40ms/1280-byte
                # chunks — Flux rejects sub-10ms frames, so without this NOTHING
                # transcribes. Emit each full 1280-byte chunk to STT.
                mic_buf.extend(raw)
                while len(mic_buf) >= _MIC_CHUNK_BYTES:
                    chunk = bytes(mic_buf[:_MIC_CHUNK_BYTES])
                    del mic_buf[:_MIC_CHUNK_BYTES]
                    try:
                        # Non-blocking: a full queue means STT stopped draining —
                        # drop rather than block this loop (which also carries
                        # control messages) or grow memory unboundedly.
                        audio_queue.put_nowait(
                            AudioChunk(data=chunk, sample_rate=_STT_SAMPLE_RATE, channels=1)
                        )
                    except asyncio.QueueFull:
                        pass
                continue

            text_data = message.get("text")
            if not text_data:
                continue
            try:
                data = json.loads(text_data)
            except json.JSONDecodeError:
                continue
            mtype = data.get("type")
            if mtype == "ping":
                await send_json({"type": "pong"})
            elif mtype == "apply_proposal":
                await apply_proposal(data.get("proposal_id"))
            elif mtype == "reject_proposal":
                pid = data.get("proposal_id")
                if isinstance(pid, str):
                    clear_proposal(pid)
                await send_json({"type": "proposal_result", "proposal_id": pid, "applied": False})
            elif mtype == "end":
                break

        active = False

    stt_task = asyncio.create_task(stt_loop())
    turn_task = asyncio.create_task(turn_loop())

    # Opening greeting so the user hears the assistant come alive and learns it
    # can create campaigns. Recorded in history so the model never re-greets.
    # Emit it as an assistant_message FIRST so the greeting text lands in the
    # chat transcript (not just spoken) — without this the intro never appears.
    # Speak it in a BACKGROUND task so receive_loop starts immediately: if we
    # awaited the full greeting synth first, early mic frames the client sends
    # right after `ready` would sit unread in the transport and be processed
    # late (agent finding). The greeting audio still streams concurrently.
    messages_history.append({"role": "assistant", "content": _VOICE_GREETING, "timestamp": datetime.utcnow().isoformat()})
    await send_json({"type": "assistant_message", "content": _VOICE_GREETING})
    greeting_task = asyncio.create_task(speak(_VOICE_GREETING))
    # Register the greeting as the current turn so a user barge-in during the
    # greeting cancels it too.
    current_turn["task"] = greeting_task

    try:
        await receive_loop()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("assistant_voice: session error: %s", exc, exc_info=True)
    finally:
        active = False
        # Non-blocking sentinels: a full audio queue (dead STT) must not stall
        # teardown — the task cancellation below covers the consumer anyway.
        try:
            audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        await final_queue.put(None)
        for task in (stt_task, turn_task, greeting_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        try:
            await stt.cleanup()
        except Exception:
            pass
        try:
            await tts.cleanup()
        except Exception:
            pass
        logger.info("assistant_voice: session ended %s", session_id)
