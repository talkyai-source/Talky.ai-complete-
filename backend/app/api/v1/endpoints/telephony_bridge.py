"""
Generic Telephony Bridge Endpoint  (PBX-agnostic)

Routes: /api/v1/sip/telephony/...

This endpoint is the single entry-point for ALL SIP B2BUA integrations.
It uses CallControlAdapterFactory to obtain the active PBX adapter
(Asterisk or FreeSWITCH) — the caller never needs to know which one is live.

All call initiation routes through CallGuard (Day 7) for security validation.

Endpoints
---------
  POST   /api/v1/sip/telephony/start            — connect to active B2BUA
  POST   /api/v1/sip/telephony/stop             — disconnect adapter
  GET    /api/v1/sip/telephony/status           — health + active calls
  POST   /api/v1/sip/telephony/call             — originate outbound call (CallGuard protected)
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
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService

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

    # System prompt — campaign-aware, no hardcoded greeting text.
    # The LLM will generate the opening line itself based on the campaign
    # context (agent name, company, goal) it receives here.
    # When a campaign provides its own system_prompt via the dialer worker,
    # that value will override this default at session creation time.
    system_prompt = """You are an outbound phone agent for a campaign.

IMPORTANT INSTRUCTIONS:
- Speak naturally like a real person on the phone — warm, friendly, professional
- Keep every response short and conversational (1-2 sentences)
- Never mention you are an AI or reference any technology
- Do NOT use filler openers like "Certainly", "Absolutely", "Sure", "Of course"
- Listen carefully and respond specifically to what the person says
- If your previous response shows [interrupted by caller], the caller spoke over you — do NOT repeat what you already said, just respond naturally to what they are saying now

OPENING THE CALL:
- Greet the person by saying hello and introduce yourself using your agent name and company name
- Then in 1 sentence explain the reason for the call based on your campaign goal
- End with a short open question to invite them to respond
- Example structure: "Hi [name], this is [agent] from [company]. [one-sentence reason for calling]. [question]?"

Your agent name, company, and campaign goal are defined in your configuration."""

    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=tts_provider_type,
        stt_model="flux-general-en",
        stt_sample_rate=8000,
        stt_encoding="linear16",
        stt_eot_threshold=0.85,
        stt_eot_timeout_ms=500,            # was 800 — Flux p50 EndOfTurn fires at ~260ms; 500ms is a safe ceiling
        stt_eager_eot_threshold=0.4,       # enable EagerEndOfTurn: fires 150–250ms before standard EOT
        llm_model=global_config.llm_model,
        llm_temperature=global_config.llm_temperature,
        llm_max_tokens=global_config.llm_max_tokens,
        voice_id=tts_voice_id,
        tts_model=global_config.tts_model,
        tts_sample_rate=8000,
        gateway_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="telephony",
        campaign_id="telephony",
        lead_id="sip-caller",
        agent_config=agent_config,
        system_prompt=system_prompt,
    )


# ---------------------------------------------------------------------------
# Audio pipeline lifecycle (called when a new call arrives on any B2BUA)
# ---------------------------------------------------------------------------

async def _send_outbound_greeting(voice_session) -> None:
    """
    Generate and speak the AI's opening line after an outbound call is answered.

    The greeting is generated dynamically by the LLM using the campaign's
    system_prompt (agent name, company, goal) — no hardcoded text.
    Short delay for async task scheduling; the gateway session is already
    initialized by _on_new_call before this task is created.
    """
    from app.domain.models.conversation import Message, MessageRole

    await asyncio.sleep(0.05)
    call_id = voice_session.call_id
    session = voice_session.call_session

    # Mark LLM as active so handle_turn_end in the pipeline skips any early
    # caller speech ("Hello?") that arrives before the greeting plays.
    if session.llm_active:
        logger.debug(f"Greeting skipped — LLM already active for {call_id[:12]}")
        return
    session.llm_active = True

    try:
        logger.info(f"Generating dynamic greeting for call {call_id[:12]}")

        # Empty user_input triggers the LLM to produce an opening line
        # using the campaign system_prompt ("OPENING THE CALL" instructions).
        greeting = await voice_session.pipeline.get_llm_response(session, "")

        if not greeting:
            logger.warning(f"LLM returned empty greeting for {call_id[:12]}, skipping")
            return

        # Persist the greeting in conversation_history so the LLM has context
        # about what the AI already said.  Without this the next turn sees an
        # empty history, the LLM re-reads "OPENING THE CALL" instructions,
        # and generates a duplicate greeting.
        session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=greeting)
        )

        # Clear any barge_in_event set by the callee answering ("Hello?") before
        # TTS starts — without this, synthesize_and_send_audio would see the event
        # already set and skip the greeting entirely.
        voice_session.pipeline.clear_barge_in_event(session)

        logger.info(f"Outbound greeting ({len(greeting)} chars): {greeting[:80]!r}")
        await voice_session.pipeline.synthesize_and_send_audio(
            session,
            greeting,
            websocket=None,
            track_latency=False,
        )
    except Exception as exc:
        logger.warning(f"Outbound greeting failed for {call_id[:12]}: {exc}")
    finally:
        session.llm_active = False


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

            # Start the voice pipeline (STT → LLM → TTS loop).
            voice_session.pipeline_task = asyncio.create_task(
                voice_session.pipeline.start_pipeline(voice_session.call_session, None)
            )
            logger.info(f"Voice pipeline started for {call_id[:12]}")

            # For outbound (campaign) calls the AI is the caller, so it must
            # speak first.  Wait briefly for Deepgram to connect, then send the
            # opening line.  synthesize_and_send_audio routes directly through
            # TelephonyMediaGateway → C++ gateway → callee — no WebSocket needed.
            asyncio.create_task(_send_outbound_greeting(voice_session))

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
        # --- Save recording BEFORE session teardown ---
        try:
            await _save_call_recording(voice_session, call_id)
        except Exception as rec_err:
            logger.warning(f"Recording save failed for {call_id[:12]}: {rec_err}")

        try:
            await _get_orchestrator().end_session(voice_session)
        except Exception:
            pass
    # Clean up gateway session mapping
    keys_to_remove = [k for k, v in _gateway_session_to_call_id.items() if v == call_id]
    for k in keys_to_remove:
        _gateway_session_to_call_id.pop(k, None)


async def _save_call_recording(voice_session, call_id: str) -> None:
    """
    Extract the recording buffer from the media gateway, convert to WAV,
    and persist to local storage + DB.

    Must be called BEFORE end_session() destroys the gateway session.

    Parameters
    ----------
    voice_session : VoiceSession
        The active voice session (still holds providers / gateway).
    call_id : str
        The PBX channel_id (key in _telephony_sessions).  This is NOT the
        same as calls.id — we must look up the internal UUID from
        calls.external_call_uuid.
    """
    from app.domain.services.recording_service import RecordingService, RecordingBuffer, mix_stereo_recording
    from app.core.container import get_container

    gateway = voice_session.media_gateway
    if not gateway:
        return

    caller_chunks = gateway.get_recording_buffer(voice_session.call_id)
    agent_chunks = getattr(gateway, "get_tts_recording_buffer", lambda _: None)(voice_session.call_id)

    if not caller_chunks and not agent_chunks:
        logger.debug(f"No recording data for {call_id[:12]}")
        return

    # Mix caller (left) + agent (right) into a stereo WAV
    wav_bytes = mix_stereo_recording(
        caller_chunks=caller_chunks or [],
        agent_chunks=agent_chunks or [],
        sample_rate=8000,
    )

    # Calculate duration from caller side (continuous timeline reference)
    caller_bytes = sum(len(c) for c in (caller_chunks or []))
    agent_bytes = sum(len(chunk) for _, chunk in (agent_chunks or []))
    bytes_per_sec = 8000 * 2  # 8kHz, 16-bit mono (per channel)
    duration = caller_bytes / bytes_per_sec if bytes_per_sec else 0.0

    if duration < 0.5:
        logger.debug(f"Recording too short ({duration:.1f}s) for {call_id[:12]}, skipping")
        return

    logger.info(
        f"Saving stereo recording for {call_id[:12]}: {duration:.1f}s, "
        f"caller={caller_bytes}B, agent={agent_bytes}B, wav={len(wav_bytes)}B"
    )

    # Build a RecordingBuffer to carry the pre-mixed WAV through the save pipeline
    buf = RecordingBuffer(
        call_id=call_id,
        sample_rate=8000,
        channels=2,        # stereo
        bit_depth=16,
    )
    buf._wav_bytes_override = wav_bytes  # pre-mixed WAV, skip re-encoding
    buf.total_bytes = len(wav_bytes)

    container = get_container()
    if not container.is_initialized:
        logger.warning("Cannot save recording: container not initialized")
        return

    db_client = container.db_client
    recording_svc = RecordingService(db_client.pool)  # pool, not Client wrapper

    # --- Resolve the internal calls.id from the PBX channel_id ----------
    # The dialer worker stores the PBX channel_id as external_call_uuid.
    internal_call_id = None
    tenant_id = "default"
    campaign_id = "unknown"

    try:
        result = (
            db_client.table("calls")
            .select("id, tenant_id, campaign_id")
            .eq("external_call_uuid", call_id)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0] if isinstance(result.data, list) else result.data
            internal_call_id = str(row.get("id"))
            tenant_id = str(row.get("tenant_id") or "default")
            campaign_id = str(row.get("campaign_id") or "unknown")
            logger.info(
                f"Resolved PBX channel {call_id[:12]} → calls.id={internal_call_id}, "
                f"tenant={tenant_id[:8]}, campaign={campaign_id[:8]}"
            )
    except Exception as lookup_err:
        logger.warning(f"Failed to look up calls record for {call_id[:12]}: {lookup_err}")

    # If we couldn't resolve the internal_call_id, save the file to disk
    # anyway but skip the DB operations (FK would fail).
    if not internal_call_id:
        logger.warning(
            f"No calls record found for channel {call_id[:12]}. "
            "Saving WAV to disk only (no DB recording entry)."
        )
        # Still save the WAV file to local storage for manual recovery
        try:
            storage_path = await recording_svc.save_recording(
                call_id=call_id,
                buffer=buf,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
            )
            if storage_path:
                logger.info(f"WAV saved to disk: {storage_path}")
        except Exception as save_err:
            logger.warning(f"WAV save to disk failed: {save_err}")
        gateway.clear_recording_buffer(voice_session.call_id)
        return

    # --- Full save: file + DB record + call update ----------------------
    recording_id = await recording_svc.save_and_link(
        call_id=internal_call_id,
        buffer=buf,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    )

    if recording_id:
        logger.info(f"Recording saved: {recording_id} for call {internal_call_id}")
    else:
        logger.warning(f"Recording save_and_link returned None for {call_id[:12]}")

    # Free memory
    gateway.clear_recording_buffer(voice_session.call_id)





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
    request: Request,
    destination: str = Query(..., description="Destination extension or phone number (E.164)"),
    caller_id: str = Query(default="1001", description="Caller ID to display"),
    campaign_id: Optional[str] = Query(None, description="Campaign context"),
    tenant_id: Optional[str] = Query(None, description="Tenant ID (optional, defaults from auth)"),
):
    """
    Originate an outbound call via the active B2BUA adapter.

    This endpoint is protected by CallGuard (Day 7) which validates:
    - Tenant/partner status
    - Rate limits
    - Concurrency limits
    - Geographic restrictions
    - DNC list
    - Business hours
    - Abuse patterns

    Returns 429 if call is blocked/throttled, 202 if queued.
    """
    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")

    # Get tenant from request context or query param
    # In production, get from auth/JWT token
    effective_tenant_id = tenant_id or getattr(request.state, "tenant_id", None)
    if not effective_tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID required")

    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    allow_dev_guard_bypass = (
        environment != "production"
        and os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "true").strip().lower()
        not in {"0", "false", "no"}
    )

    # Initialize CallGuard
    from app.core.container import get_container
    container = get_container()

    guard = CallGuard(
        db_pool=container.db_pool,
        redis_client=getattr(container, "redis", None),
    )

    # Evaluate call through guard
    guard_result = await guard.evaluate(
        tenant_id=effective_tenant_id,
        phone_number=destination,
        campaign_id=campaign_id,
        call_type="outbound",
    )

    failed_reasons = [
        check.reason for check in guard_result.check_results if not check.passed and check.reason
    ]
    bypassable_guard_error = (
        guard_result.decision == GuardDecision.BLOCK
        and bool(failed_reasons)
        and all(
            reason == "configuration_load_error" or reason.startswith("check_error:")
            for reason in failed_reasons
        )
    )
    if allow_dev_guard_bypass and bypassable_guard_error:
        logger.warning(
            "Bypassing CallGuard block in %s due to local guard configuration/schema errors: "
            "tenant=%s dest=%s reasons=%s",
            environment,
            effective_tenant_id,
            destination,
            failed_reasons,
        )
        guard_result = GuardResult(
            decision=GuardDecision.ALLOW,
            tenant_id=guard_result.tenant_id,
            phone_number=guard_result.phone_number,
            check_results=guard_result.check_results,
            failed_checks=[],
            total_latency_ms=guard_result.total_latency_ms,
            call_id=guard_result.call_id,
        )

    # Handle guard decisions
    if guard_result.decision == GuardDecision.BLOCK:
        logger.warning(
            f"Call blocked by guard: tenant={effective_tenant_id}, "
            f"dest={destination}, reasons={guard_result.failed_checks}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "call_blocked",
                "reasons": [
                    c.reason for c in guard_result.check_results if not c.passed
                ],
                "guard_latency_ms": guard_result.total_latency_ms,
            },
        )

    if guard_result.decision == GuardDecision.THROTTLE:
        logger.warning(
            f"Call throttled by guard: tenant={effective_tenant_id}, "
            f"dest={destination}"
        )
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(guard_result.retry_after_seconds or 60)},
            detail={
                "error": "call_throttled",
                "retry_after_seconds": guard_result.retry_after_seconds or 60,
                "guard_latency_ms": guard_result.total_latency_ms,
            },
        )

    if guard_result.decision == GuardDecision.QUEUE:
        logger.info(
            f"Call queued by guard: tenant={effective_tenant_id}, "
            f"dest={destination}, position={guard_result.queue_position}"
        )
        return JSONResponse(
            status_code=202,  # Accepted
            content={
                "status": "queued",
                "queue_position": guard_result.queue_position,
                "estimated_wait_seconds": guard_result.retry_after_seconds,
                "guard_latency_ms": guard_result.total_latency_ms,
            },
        )

    # Guard passed - proceed with call
    try:
        call_id = await _adapter.originate_call(
            destination=destination,
            caller_id=caller_id,
        )

        # Trigger post-call abuse detection (async)
        try:
            detector = AbuseDetectionService(
                db_pool=container.db_pool,
                redis_client=getattr(container, "redis", None),
            )
            # Note: analyze_call_initiated is for pre-call checks
            # Post-call analysis happens in call completion handler
        except Exception as e:
            logger.warning(f"Failed to initialize abuse detector: {e}")

        return JSONResponse({
            "status": "calling",
            "call_id": call_id,
            "destination": destination,
            "adapter": _adapter.name,
            "guard_latency_ms": guard_result.total_latency_ms,
        })

    except Exception as exc:
        logger.error(f"Failed to originate call: {exc}")
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
