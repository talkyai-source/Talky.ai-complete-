"""
Campaign Test WebSocket — talk to the REAL campaign agent from the browser.

This is NOT a demo agent. It reuses the exact telephony path a live phone
call takes:

    tenant AI-Options  ->  build_telephony_session_config(gateway_type="browser")
                       ->  VoiceOrchestrator.create_voice_session(config)
                       ->  BrowserMediaGateway  ->  this WebSocket

Because config is resolved through ``get_tenant_ai_config_resolver()`` (which is
cache-bypassed) and ``build_telephony_session_config``, whatever the tenant
picked in AI Options — cascaded vs realtime (gpt-realtime) pipeline, LLM, STT,
TTS provider/voice, persona, knowledge — is honored here identically to a real
call, and a change to AI Options takes effect on the very next connection.

The only per-call knob is first-speaker (``?first_speaker=agent|user``), the
same choice a real Start offers: agent-first greets immediately (realtime:
greet_on_start; cascaded: streamed greeting), caller-first waits for the user.

No ``calls`` row is created and ``event_logging_enabled`` stays False, so a test
session is NOT billed against plan minutes.
"""

import json
import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Campaign Test"])

# Small concurrency cap so a stuck test tab can't pin an unbounded number of
# realtime/TTS provider sockets. RFC 6455 close 1013 = "Try Again Later".
_MAX_CONCURRENT_TEST = 8
_test_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _test_semaphore
    if _test_semaphore is None:
        _test_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TEST)
    return _test_semaphore


# ---------------------------------------------------------------------------
# Auth helpers — mirror assistant_ws.py: cookie first, first-frame fallback.
# ---------------------------------------------------------------------------

def _read_cookie_token(websocket: WebSocket) -> Optional[str]:
    """Read the ``talky_at`` HttpOnly cookie from the WS handshake."""
    raw = websocket.cookies.get("talky_at")
    if not raw:
        return None
    stripped = raw.strip()
    return stripped or None


async def _resolve_ws_token(websocket: WebSocket) -> Optional[str]:
    """Resolve the auth token without exposing it in the URL.

    1. ``talky_at`` HttpOnly cookie (preferred — same surface as REST).
    2. First frame ``{"type":"auth","token":"…"}`` for clients that can't
       carry the cookie (bearer-fallback mode). 5s wait after accept().
    """
    cookie_token = _read_cookie_token(websocket)
    if cookie_token:
        return cookie_token

    try:
        first_frame = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.info("campaign_test_ws: no auth frame within 5s")
        return None
    except WebSocketDisconnect:
        return None
    except Exception as e:  # noqa: BLE001
        logger.info("campaign_test_ws: failed to parse first frame: %s", e)
        return None

    if not isinstance(first_frame, dict) or first_frame.get("type") != "auth":
        return None
    token = first_frame.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    return token


def _is_origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-origin browser upgrades. Non-browser clients (no Origin)
    are allowed — they don't carry the browser cookie."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    from app.core.config import get_settings

    return origin in get_settings().allowed_origins


async def _record_test_call(
    container, tenant_id, campaign_id, voice_session, config
) -> Optional[str]:
    """Write a flagged ``calls`` row for a campaign test session.

    Why a row at all: recordings, transcripts, the prompt version on the call,
    feedback voice notes and conversation reviews all reference ``calls(id)``.
    Without one, none of them can be exercised from the test button, which
    defeats the purpose of having a test button.

    Why it is safe: ``is_test`` is TRUE, and every query that counts money,
    dial capacity or abuse excludes it explicitly (Alembic 0017 lists them).
    A test session is billed nothing, consumes no dial slot, burns no lead
    attempt and cannot trip abuse detection.

    Never raises. A test call that cannot be recorded is still a test call.
    """
    pool = getattr(container, "db_pool", None)
    if pool is None:
        return None
    try:
        from app.core.db_utils import acquire_with_tenant

        call_uuid = str(uuid.uuid4())
        talklee_id = getattr(voice_session, "talklee_call_id", None) or call_uuid
        async with acquire_with_tenant(pool, str(tenant_id)) as conn:
            await conn.execute(
                """
                INSERT INTO calls (
                    id, tenant_id, campaign_id, phone_number, status,
                    talklee_call_id, is_test,
                    prompt_template, prompt_version, prompt_hash, created_at
                ) VALUES ($1,$2,$3,$4,'in_progress',$5,TRUE,$6,$7,$8,NOW())
                """,
                uuid.UUID(call_uuid),
                uuid.UUID(str(tenant_id)),
                uuid.UUID(str(campaign_id)),
                "browser-test",
                str(talklee_id),
                getattr(config, "prompt_template", None),
                getattr(config, "prompt_version", None),
                getattr(config, "prompt_hash", None),
            )
        logger.info(
            "campaign_test_call_recorded call=%s campaign=%s prompt=%s — flagged "
            "is_test, excluded from billing, concurrency and abuse",
            call_uuid[:8], str(campaign_id)[:8],
            getattr(config, "prompt_version", None),
        )
        return call_uuid
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("campaign_test_call_record_failed campaign=%s",
                       str(campaign_id)[:8], exc_info=True)
        return None


async def _finalise_test_call(container, tenant_id, call_id, started_at) -> None:
    """Close the test row out so it looks like a finished call in the UI.

    Duration is recorded because the call detail page shows it — it is NOT
    billed, because minutes_quota sums only rows where NOT is_test.
    """
    if not call_id:
        return
    pool = getattr(container, "db_pool", None)
    if pool is None:
        return
    try:
        from app.core.db_utils import acquire_with_tenant

        seconds = max(0, int(time.time() - started_at))
        async with acquire_with_tenant(pool, str(tenant_id)) as conn:
            await conn.execute(
                """UPDATE calls
                      SET status = 'completed',
                          duration_seconds = $2,
                          ended_at = NOW()
                    WHERE id = $1""",
                uuid.UUID(str(call_id)), seconds,
            )
        logger.info("campaign_test_call_finalised call=%s seconds=%s (not billed)",
                    str(call_id)[:8], seconds)
    except Exception:  # noqa: BLE001
        logger.warning("campaign_test_call_finalise_failed call=%s",
                       str(call_id)[:8], exc_info=True)


@router.websocket("/ws/campaign-test/{campaign_id}")
async def campaign_test_websocket(
    websocket: WebSocket,
    campaign_id: str,
    first_speaker: str = Query(
        "agent",
        description="'agent' (agent greets first) or 'user' (caller speaks first)",
    ),
):
    """Browser WebSocket that runs the real agent for ``campaign_id``.

    Same transport contract as ``/ws/ask-ai`` (binary PCM16 both ways + JSON
    control frames), but the agent is the tenant's live campaign agent.
    """
    # Reject cross-origin upgrades BEFORE accepting.
    if not _is_origin_allowed(websocket):
        origin = websocket.headers.get("origin")
        logger.warning("campaign_test_ws: rejecting cross-origin upgrade from %r", origin)
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()

    sem = _get_semaphore()
    if sem._value == 0:
        await websocket.send_json({"type": "error", "message": "Server at capacity, please retry shortly"})
        await websocket.close(code=1013)
        return

    # ── Auth: resolve token → user → tenant ─────────────────────────────
    resolved_token = await _resolve_ws_token(websocket)
    if not resolved_token:
        await websocket.send_json({"type": "error", "message": "Authentication required."})
        await websocket.close(code=1008, reason="Missing auth")
        return

    from app.core.jwt_security import JWTValidationError, decode_and_validate_token

    try:
        payload = decode_and_validate_token(resolved_token)
    except JWTValidationError as jwt_err:
        logger.info("campaign_test_ws: token verification failed: %s", jwt_err.detail)
        await websocket.send_json({"type": "error", "message": "Invalid or expired token."})
        await websocket.close(code=1008, reason="Invalid token")
        return

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        await websocket.send_json({"type": "error", "message": "Invalid token: missing subject."})
        await websocket.close(code=1008, reason="Invalid token")
        return

    from app.api.v1.dependencies import get_db_client

    db_client = get_db_client()
    try:
        profile = db_client.table("user_profiles").select(
            "tenant_id"
        ).eq("id", user_id).single().execute()
        tenant_id = (
            str(profile.data.get("tenant_id"))
            if profile.data and profile.data.get("tenant_id")
            else None
        )
    except Exception as profile_err:  # noqa: BLE001
        logger.error("campaign_test_ws: profile lookup failed: %s", profile_err)
        tenant_id = None

    if not tenant_id:
        await websocket.send_json({"type": "error", "message": "User profile not found."})
        await websocket.close(code=1008, reason="No tenant")
        return

    # RLS tenant context for any .table() lookups on this task.
    from app.core.security.tenant_isolation import set_current_tenant_id

    set_current_tenant_id(tenant_id)

    # ── Fetch the campaign row, scoped to this tenant (IDOR guard) ───────
    from app.core.container import get_container
    from app.domain.services.telephony.lifecycle import _fetch_campaign_row

    container = get_container()
    if not container.is_initialized:
        await websocket.send_json({"type": "error", "message": "Backend not ready."})
        await websocket.close(code=1011, reason="Container not initialized")
        return

    campaign_row = await _fetch_campaign_row(container.db_pool, tenant_id, campaign_id)
    if campaign_row is None:
        await websocket.send_json({"type": "error", "message": "Campaign not found."})
        await websocket.close(code=1008, reason="Campaign not found")
        return

    # ── First-speaker → Direction (chosen BEFORE create_voice_session so the
    #    realtime bridge is built with the correct greet_on_start) ─────────
    from app.domain.services.voice_orchestrator import Direction
    from app.domain.services.telephony_session_config import (
        build_telephony_session_config,
    )
    from app.domain.services.tenant_ai_config_resolver import (
        get_tenant_ai_config_resolver,
    )
    from app.domain.services.voice_tuning import get_voice_tuning_resolver

    fs = "user" if (first_speaker or "").strip().lower() == "user" else "agent"
    direction = Direction.from_first_speaker(fs)

    logger.info(
        "campaign_test_ws start campaign=%s tenant=%s first_speaker=%s",
        str(campaign_id)[:8], str(tenant_id)[:8], fs,
    )

    voice_session = None
    receiver_task: Optional[asyncio.Task] = None
    test_call_id = None
    test_started_at = time.time()

    async with sem:
        try:
            # Resolve the tenant's LIVE AI Options exactly like a phone call.
            # These resolvers are cache-bypassed, so an AI-Options edit takes
            # effect on the next connection (requirement: test agent reacts to
            # AI Options).
            ai_cfg = await get_tenant_ai_config_resolver().for_tenant_async(tenant_id)
            vt = await get_voice_tuning_resolver().for_tenant_async(tenant_id)

            config = build_telephony_session_config(
                gateway_type="browser",
                campaign=campaign_row,
                direction=direction,
                ai_config_override=ai_cfg,
                voice_tuning_override=vt,
            )

            orchestrator = container.voice_orchestrator
            voice_session = await orchestrator.create_voice_session(config)

            # ── Give the test call a real row (Alembic 0017) ──────────────
            # Without one, nothing that hangs off a call could be tested from
            # here: recordings, transcripts, the prompt version on the row,
            # feedback voice notes and conversation reviews all reference
            # calls(id). The row is flagged is_test, and every query that
            # counts money, dial capacity or abuse excludes it explicitly —
            # minutes_quota, billing, dialer concurrency, the per-lead daily
            # cap, all five abuse checks and telephony observability.
            #
            # Created AFTER the session exists and wrapped: failing to record a
            # test call must never stop you testing the agent.
            test_call_id = await _record_test_call(
                container, tenant_id, campaign_id, voice_session, config
            )

            # Per-call first-speaker on the session (phone path sets both).
            # Also opt this test call into the natural silence handling so it
            # behaves like a real call: a gentle "Hello?" after ~10s of caller
            # silence, and auto-close after 60s. (Real phone calls get this by
            # gateway_type; the browser test call opts in explicitly.)
            try:
                voice_session._first_speaker = fs
                voice_session._enable_silence_monitor = True
                _cs = getattr(voice_session, "call_session", None)
                if _cs is not None:
                    _cs._first_speaker = fs
                    _cs._enable_silence_monitor = True
            except Exception:  # noqa: BLE001
                pass

            call_id = voice_session.call_id
            gateway = voice_session.media_gateway
            is_realtime = getattr(voice_session, "realtime_bridge", None) is not None

            # Caller-first cascaded: re-frame the prompt as an inbound receiver.
            # (direction=INBOUND already composes inbound, so this is an
            # idempotent no-op belt-and-braces — matches the phone path.)
            if not is_realtime and fs == "user":
                try:
                    from app.domain.services.telephony.modes.caller_first import (
                        select_inbound_base_prompt,
                    )

                    select_inbound_base_prompt(voice_session)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("campaign_test_ws inbound prompt swap failed: %s", exc)

            # Rates come off the gateway AFTER create — realtime forces 8 kHz.
            out_rate = getattr(gateway, "_sample_rate", config.gateway_sample_rate)
            in_rate = getattr(gateway, "_input_sample_rate", out_rate)

            await websocket.send_json(
                {
                    "type": "ready",
                    "call_id": call_id,
                    "campaign_id": str(campaign_id),
                    "sample_rate": out_rate,
                    "input_sample_rate": in_rate,
                    "audio_format": "s16le",
                    "pipeline_mode": "realtime" if is_realtime else "cascaded",
                    "first_speaker": fs,
                }
            )

            async def _receive_messages() -> None:
                while gateway.is_session_active(call_id):
                    try:
                        message = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                        message_type = message.get("type")

                        if message_type == "websocket.disconnect":
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
                            continue
                        if data.get("type") == "end_call":
                            await gateway.on_call_ended(call_id, "user_ended")
                            break
                        if data.get("type") == "playback_complete":
                            mark = getattr(gateway, "mark_playback_complete", None)
                            if callable(mark):
                                mark(call_id)
                            continue
                        # {"type":"auth"} frames (sent by cookie-mode clients that
                        # also send a bearer frame) are ignored here.

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
                            break
                        raise

            # ── Run the leg — the ONE branch that differs from Ask AI ───────
            if is_realtime:
                # Realtime: wire the browser transport, then let the bridge pump
                # caller audio -> model and model audio -> gateway. greet_on_start
                # (set at bridge-build time from Direction) owns the greeting.
                await gateway.on_call_started(call_id, {"websocket": websocket})
                voice_session.pipeline_task = asyncio.create_task(
                    voice_session.realtime_bridge.run()
                )
                receiver_task = asyncio.create_task(_receive_messages())
            else:
                # Cascaded: start_pipeline calls on_call_started + runs STT/LLM/TTS.
                await orchestrator.start_pipeline(voice_session, websocket)
                receiver_task = asyncio.create_task(_receive_messages())
                if fs == "agent":
                    from app.domain.services.telephony.config import (
                        _build_outbound_greeting,
                    )
                    from app.domain.models.conversation import Message, MessageRole

                    greeting = _build_outbound_greeting(voice_session)
                    await orchestrator.send_greeting(voice_session, greeting, websocket)
                    # Seed history so the LLM doesn't re-greet on the first turn.
                    try:
                        voice_session.call_session.conversation_history.append(
                            Message(role=MessageRole.ASSISTANT, content=greeting)
                        )
                    except Exception:  # noqa: BLE001
                        pass
                # caller-first: send nothing; the pipeline reacts to the first turn.

            await receiver_task

        except WebSocketDisconnect:
            logger.info("campaign_test_ws disconnected campaign=%s", str(campaign_id)[:8])
        except Exception as e:  # noqa: BLE001
            logger.error("campaign_test_ws error: %s", e, exc_info=True)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
                await websocket.close(code=1011, reason="server error")
            except Exception:  # noqa: BLE001
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
            # Close the flagged row out so the call detail page shows a finished
            # call you can play, review and leave a voice note on. The duration
            # is recorded for display only — minutes_quota sums NOT is_test.
            await _finalise_test_call(
                container, tenant_id, test_call_id, test_started_at
            )
            logger.info("campaign_test_ws session ended campaign=%s", str(campaign_id)[:8])
