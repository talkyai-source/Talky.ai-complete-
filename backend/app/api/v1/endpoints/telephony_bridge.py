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
from uuid import UUID
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService
from app.services.scripts import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)

# ---------------------------------------------------------------------------
# Backward-compat re-exports — implementations live in the telephony package.
# ---------------------------------------------------------------------------
from app.domain.services.telephony.config import (  # noqa: E402
    _outbound_first_speaker,
    _build_telephony_session_config,
    _build_outbound_greeting,
)
from app.domain.services.telephony.modes import resolve_first_speaker  # noqa: E402
from app.domain.services.telephony.modes.user_first import (  # noqa: E402
    _user_first_open_seconds,
    _user_first_fallback_enabled,
    _handle_user_first_silence,
)
from app.domain.services.telephony.modes.agent_first import (  # noqa: E402
    _send_outbound_greeting,
    prepare_pre_originate_greeting,
    warm_tts_inference_path,
    warm_llm_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/telephony", tags=["Telephony Bridge (generic)"])

# ---------------------------------------------------------------------------
# Module-level adapter instance (one per process)
# ---------------------------------------------------------------------------
_adapter: Optional[CallControlAdapter] = None

# Active voice sessions keyed by PBX call_id (channel_id / call UUID)
_telephony_sessions: dict[str, object] = {}  # VoiceSession objects

# Max concurrent telephony sessions; override with MAX_TELEPHONY_SESSIONS env var.
# Each session holds 1 Deepgram WS + 1 Groq connection + audio buffers (~60KB–57MB).
# Groq free-tier llama-3.1-8b-instant hits 30K TPM at ~28-40 concurrent calls.
_MAX_TELEPHONY_SESSIONS = int(os.getenv("MAX_TELEPHONY_SESSIONS", "50"))

# Watchdog task handle — started when the adapter connects, cancelled on stop.
_watchdog_task: Optional[asyncio.Task] = None

# Maps C++ gateway session_id → PBX call_id for the audio callback path.
# Populated in _on_new_call when the AsteriskAdapter registers a gateway session.
_gateway_session_to_call_id: dict[str, str] = {}

# Early audio buffer: audio chunks from the C++ gateway that arrive BEFORE
# _on_new_call has registered the session mapping.  Without this, the callee's
# first utterance ("Hello?") is silently dropped because _on_outbound_answered
# starts the C++ gateway (→ audio POSTs begin immediately) and then fires
# _on_new_call as create_task (→ runs later).  All audio in that gap is lost.
# Keyed by gateway session_id (e.g. "asterisk-talky-out-07-32000").
# Each value is a list[bytes] of raw audio chunks (PCMU from gateway).
# Capped at _EARLY_AUDIO_MAX_CHUNKS to bound memory (~10s of 40ms batches).
_early_audio_buffers: dict[str, list[bytes]] = {}
_EARLY_AUDIO_MAX_CHUNKS: int = 250  # ~10s at 40ms per batch

# Pre-warmed voice sessions created during the ringing phase of outbound calls.
# Populated by _on_ringing when the Asterisk adapter parks an outbound channel
# (callee is still hearing the ring tone); drained by _on_new_call once the
# callee answers.  Each value is (VoiceSession, connect_task | None) where the
# task is a background asyncio.gather of STT + TTS handshake coroutines.
# LLM warmup runs as a separate fire-and-forget task and is not tracked here.
_ringing_warmups: dict[str, tuple[object, Optional[asyncio.Task]]] = {}

# Parallel monotonic-time timestamps for _ringing_warmups entries — used by the
# session watchdog to garbage-collect orphaned warmups when a callee never
# answers and no terminal event ever fires for the channel. Without this sweep
# the open Deepgram + TTS WebSockets leak per unanswered call.
_ringing_warmup_created_at: dict[str, float] = {}

# Maximum age (seconds) for an entry in _ringing_warmups / _ringing_events
# before the watchdog drops it. Outbound calls almost always connect or fail
# within ~60s; 180s is a conservative safety net for genuinely-slow carriers.
_RINGING_MAX_AGE_S: int = 180


# Coordination events for ringing-phase warmup.  When _on_ringing starts, it
# inserts an unset asyncio.Event for the call_id.  When the warmup completes
# (or fails), the event is set.  _on_new_call awaits this event instead of
# polling _ringing_warmups — this eliminates the race condition where the
# answer path (7ms ARI setup) finishes long before the warmup (~1s for
# create_voice_session + provider init).
_ringing_events: dict[str, asyncio.Event] = {}




# ---------------------------------------------------------------------------
# Helpers / lifecycle (implementations live in the telephony package)
# ---------------------------------------------------------------------------
from app.domain.services.telephony.recording import (  # noqa: E402
    _save_call_recording,
)
from app.domain.services.telephony.lifecycle import (  # noqa: E402
    _pop_ringing_warmup,
    _get_orchestrator,
    _session_watchdog,
    _SESSION_INACTIVITY_TIMEOUT_S,
    _pipeline_done_cb,
    _on_ringing,
    _reject_overcap_call,
    _on_new_call,
    _on_audio_received,
    _on_call_ended,
    _on_ws_session_start,
)

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
    global _adapter, _watchdog_task

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

        # For adapters that expose a ringing-phase hook (Asterisk), wire up
        # _on_ringing so providers are warmed during ring time.  Keeps
        # first-turn latency matched to subsequent turns (~<500 ms).
        if hasattr(_adapter, "set_ringing_callback"):
            _adapter.set_ringing_callback(_on_ringing)

        await _adapter.connect()

        # GAP 5 — Start session inactivity watchdog (cancels itself on stop).
        if _watchdog_task is None or _watchdog_task.done():
            _watchdog_task = asyncio.create_task(_session_watchdog())
            logger.info("telephony_watchdog: started (inactivity=%ds)", _SESSION_INACTIVITY_TIMEOUT_S)

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
    global _adapter, _watchdog_task

    if not _adapter:
        return JSONResponse({"status": "not_running"})

    # Cancel the inactivity watchdog before disconnecting.
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None

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

    # FIX 7 — Expose capacity utilisation and Groq circuit-breaker state so
    # operators can see pressure before callers start hearing apology messages.
    # All checks are local (no network calls) — zero added latency to this endpoint.
    provider_health: dict = {}
    try:
        container = _get_orchestrator().__class__  # just a way to import lazily
        from app.core.container import get_container
        llm = getattr(get_container(), "llm_provider", None)
        cb = getattr(llm, "_circuit_breaker", None)
        if cb is not None:
            provider_health["groq_circuit"] = "open" if cb.is_open else "closed"
    except Exception:
        pass

    # T1.2 — expose the cluster-wide count alongside the per-pod one
    # so operators can see fleet saturation at a glance.
    from app.domain.services.global_concurrency import (
        current_count as _global_current,
        resolve_global_cap as _resolve_cap,
    )
    from app.core.container import get_container as _gc
    _c = _gc()
    _redis = getattr(_c, "redis", None) if _c.is_initialized else None
    global_current = await _global_current(_redis)
    global_cap = _resolve_cap()

    return JSONResponse({
        "status": "running" if healthy else "degraded",
        "connected": _adapter.connected,
        "adapter": _adapter.name,
        "active_sessions": len(_telephony_sessions),
        "healthy": healthy,
        "capacity": {
            # Per-pod count stays so single-pod dashboards don't break.
            "current": len(_telephony_sessions),
            "max": _MAX_TELEPHONY_SESSIONS,
            "pct_used": round(len(_telephony_sessions) / max(_MAX_TELEPHONY_SESSIONS, 1) * 100, 1),
            # Cluster-wide view (null when Redis is unavailable).
            "global_current": global_current,
            "global_max": global_cap,
            "global_pct_used": (
                round((global_current or 0) / max(global_cap, 1) * 100, 1)
                if global_current is not None else None
            ),
        },
        "provider_health": provider_health,
    })


@router.post("/call")
async def make_call(
    request: Request,
    destination: str = Query(..., description="Destination extension or phone number (E.164)"),
    caller_id: str = Query(default="1001", description="Caller ID to display"),
    campaign_id: Optional[str] = Query(None, description="Campaign context"),
    tenant_id: Optional[str] = Query(None, description="Tenant ID (optional, defaults from auth)"),
    first_speaker: Optional[Literal["agent", "user"]] = Query(
        None,
        description="Per-call override for who speaks first. Falls back to TELEPHONY_FIRST_SPEAKER env.",
    ),
    agent_name: Optional[str] = Query(
        None,
        description="Per-call agent name picked from the campaign's name pool. Stays stable for the whole call.",
    ),
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

    # Fail-closed bypass policy (T0.2). Historically ANY non-"production"
    # environment plus a truthy TELEPHONY_DEV_BYPASS_GUARD_ERRORS flag would
    # allow guard errors through. That silently disabled every safety check on
    # staging / blank / misspelled env values and left a footgun pointed at
    # prod. New rule: bypass is honoured ONLY when BOTH are explicitly set —
    #   ENVIRONMENT == "development"  AND  TELEPHONY_LOCAL_DEV == "1"
    # Any other value — blank, "staging", "prod", "production" — never bypass.
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    local_dev = os.getenv("TELEPHONY_LOCAL_DEV", "").strip().lower() in {"1", "true", "yes"}
    bypass_flag = os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "false").strip().lower() \
        not in {"0", "false", "no", ""}
    allow_dev_guard_bypass = environment == "development" and local_dev and bypass_flag

    # Initialize CallGuard
    from app.core.container import get_container
    container = get_container()

    # T0.1 — Caller-ID ownership enforcement.
    # Before any guard/originate work, refuse the call unless `caller_id`
    # is registered AND verified under this tenant. In prod we also
    # require a STIR/SHAKEN attestation token on the DID row (test-only
    # numbers cannot dial real carriers).
    #
    # Ramp-in: CALLER_ID_ENFORCEMENT_MODE = enforce | log | off.
    #   - enforce (default in prod): violation → HTTP 403.
    #   - log    (default in dev/staging): violation → WARN + allow.
    #   - off    : disabled entirely. Use only for first-time bring-up.
    #
    # This knob lets operators roll the change out per-environment
    # without tripping every existing dev/CI workflow on day one.
    from app.domain.services.tenant_phone_number_service import (
        TenantPhoneNumberService,
    )
    did_svc = TenantPhoneNumberService(container.db_pool)
    require_attestation = environment == "production"
    default_mode = "enforce" if environment == "production" else "log"
    enforcement_mode = (
        os.getenv("CALLER_ID_ENFORCEMENT_MODE", default_mode).strip().lower()
    )
    if enforcement_mode not in {"enforce", "log", "off"}:
        enforcement_mode = default_mode

    if enforcement_mode != "off":
        try:
            caller_id_ok = await did_svc.is_verified_for_tenant(
                tenant_id=str(effective_tenant_id),
                e164=caller_id,
                require_attestation=require_attestation,
            )
        except Exception as did_exc:
            logger.error(
                "caller_id_verification_lookup_failed tenant=%s caller_id=%s err=%s",
                effective_tenant_id, caller_id, did_exc,
            )
            caller_id_ok = False

        if not caller_id_ok:
            logger.warning(
                "caller_id_unauthorized tenant=%s caller_id=%s mode=%s "
                "environment=%s require_attestation=%s",
                effective_tenant_id, caller_id, enforcement_mode,
                environment, require_attestation,
            )
            if enforcement_mode == "enforce":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "caller_id_not_verified",
                        "message": (
                            "The caller_id is not registered and verified under "
                            "this tenant. Register it at POST /api/v1/"
                            "tenant-phone-numbers and verify before dialing."
                        ),
                        "caller_id": caller_id,
                        "require_attestation": require_attestation,
                    },
                )

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

    # Guard passed — pre-warm BEFORE originating so the greeting audio is
    # ready even when the callee answers instantly (local PBX loop).
    #
    # Timeline:
    #   1. Create VoiceSession + TTS/STT connections + synthesize greeting
    #   2. Originate the SIP call
    #   3. When _on_new_call fires (even 0 ms later), the pre-warmed session
    #      and pre-synthesized audio are already in _ringing_warmups.
    # Resolve per-call first-speaker choice (query param > env default).
    # The answer path reads this back off the pre-warm session in _on_new_call,
    # falling back to _outbound_first_speaker() when no per-call value is set.
    effective_first_speaker = (first_speaker or _outbound_first_speaker()).strip().lower()
    if effective_first_speaker not in ("agent", "user"):
        effective_first_speaker = "agent"

    # Look up the campaign row so the session builder can route through
    # the layered prompt composer when script_config.persona_type is set.
    # Failure here is non-fatal — we fall back to the legacy prompt.
    campaign_row = None
    if campaign_id:
        try:
            db_client = getattr(container, "db_client", None)
            if db_client is not None:
                row = (
                    db_client.table("campaigns")
                    .select("*")
                    .eq("id", campaign_id)
                    .limit(1)
                    .execute()
                )
                if getattr(row, "data", None):
                    campaign_row = row.data[0]
        except Exception as cexc:
            logger.warning(
                "campaign_lookup_failed campaign_id=%s err=%s — using legacy prompt",
                campaign_id, cexc,
            )

    pre_warm_session = None
    try:
        orchestrator = _get_orchestrator()
        config = _build_telephony_session_config(
            gateway_type="telephony",
            campaign=campaign_row,
            agent_name=agent_name,
        )
        # User-first only: relax the Flux end-of-turn timeout from 500ms
        # to 1000ms. The 500ms default is aggressive and was the cause of
        # the StartOfTurn → EndOfTurn → TurnResumed → EndOfTurn fragment
        # pattern observed on the very first utterance: a natural rising
        # "Hello?" with even a tiny breath gap was endpointed too eagerly,
        # firing a speculative LLM that got cancelled when the callee kept
        # talking, and the *real* turn-0 LLM call paid full streaming
        # setup again. 1000ms is still well below conversational latency
        # but lets a short opener finish cleanly. Agent-first keeps the
        # tighter 500ms because that mode's first turn is the agent's
        # greeting and the callee's reply is short and back-and-forth.
        if effective_first_speaker == "user":
            config.stt_eot_timeout_ms = 1000
        pre_warm_session = await orchestrator.create_voice_session(config)
        pre_warm_session._first_speaker = effective_first_speaker
        if effective_first_speaker == "user":
            logger.info(
                "stt_eot_timeout_user_first_relaxed call_id=%s timeout_ms=1000",
                pre_warm_session.call_id[:12],
            )
        if agent_name:
            pre_warm_session._agent_name = agent_name

        # ───────────────────────────────────────────────────────────────
        # Strict warmup gate — racer-in-starting-blocks model.
        #
        # Every layer of the pipeline (STT, TTS WebSocket, TTS voice-model
        # inference path, LLM connection + KV-cache prime) must be ready
        # before we ring the callee's bell. The callee's pickup MUST land
        # on a fully-hot pipeline regardless of which mode the campaign
        # owner picked. If any warmup fails or hangs past the timeout, we
        # refuse to originate rather than letting the callee pick up to a
        # half-cold pipeline.
        #
        # This is intentionally stricter than before — `llm_warm()` used
        # to be `asyncio.create_task(...)` (fire-and-forget) and the TTS
        # voice model only loaded as a side effect of greeting pre-synth
        # in agent-first mode. User-first calls were therefore picking up
        # to a cold TTS inference path, costing ~2s on the first turn.
        # ───────────────────────────────────────────────────────────────
        warmup_coros = []

        # 1. STT WebSocket (Deepgram Flux ready to listen)
        if hasattr(pre_warm_session.stt_provider, "pre_connect"):
            warmup_coros.append(
                pre_warm_session.stt_provider.pre_connect(
                    pre_warm_session.call_session.call_id
                )
            )

        # 2. TTS WebSocket (auth handshake done)
        _tts_connect = getattr(pre_warm_session.tts_provider, "connect_for_call", None)
        if _tts_connect is not None:
            warmup_coros.append(_tts_connect(pre_warm_session.call_id))

        # 3. LLM connection + KV-cache prime (was fire-and-forget — now strict)
        llm_warm = getattr(pre_warm_session.llm_provider, "warm_up", None)
        if llm_warm is not None:
            warmup_coros.append(llm_warm())

        # 4. TTS voice-model load (forces the inference worker to load the
        #    voice so turn 0 doesn't pay model-load latency).
        warmup_coros.append(warm_tts_inference_path(pre_warm_session))

        # 5. LLM streaming inference path warmup. `warm_up()` above opens the
        #    connection but doesn't run a real streaming generation — most
        #    providers' first stream after warm_up still pays a one-time
        #    setup cost. This drains a tiny "hi" stream so turn 0 in BOTH
        #    modes lands on a fully-streamed-end-to-end LLM path.
        warmup_coros.append(warm_llm_stream(pre_warm_session))

        prewarm_timeout_s = float(
            os.getenv("TELEPHONY_PREWARM_TIMEOUT_S", "5.0")
        )
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*warmup_coros, return_exceptions=True),
                timeout=prewarm_timeout_s,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                "pre_originate_warmup_timeout: pipeline not ready within "
                f"{prewarm_timeout_s}s — refusing to ring"
            )
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            raise RuntimeError(
                f"pre_originate_warmup_handshake_failed: {failed[0]!r}"
            )

        # Greeting pre-synth (only when audio will actually be played).
        # By now the TTS voice model is already loaded by step 4 above,
        # so this synth is fast even on the very first call of the process.
        await prepare_pre_originate_greeting(pre_warm_session, effective_first_speaker)

    except Exception as warm_exc:
        logger.error(
            "pre_originate_warmup_failed: %s — refusing to ring with cold pipeline",
            warm_exc,
        )
        if pre_warm_session is not None:
            try:
                await _get_orchestrator().end_session(pre_warm_session)
            except Exception:
                pass
            pre_warm_session = None

    # Strict gate: do not ring the bell unless the pipeline is fully ready.
    # Agent-first mode: greeting buffered. User-first mode: STT + TTS WS open
    # so Flux is ready to listen the instant the callee picks up.
    if pre_warm_session is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice pipeline is not ready. Refusing to originate the call "
                "to avoid silence on pickup. Check TTS/STT provider health."
            ),
        )

    try:
        call_id = await _adapter.originate_call(
            destination=destination,
            caller_id=caller_id,
        )

        # Store the pre-warmed session so _on_new_call (or _on_ringing) can
        # consume it.  The key is the PBX channel ID returned by originate.
        if pre_warm_session is not None:
            # Mark the combined task as already-done so _on_new_call's
            # await connect_task completes instantly.
            done_future: asyncio.Future = asyncio.get_event_loop().create_future()
            done_future.set_result(None)
            _ringing_warmups[call_id] = (pre_warm_session, done_future)
            _ringing_warmup_created_at[call_id] = asyncio.get_event_loop().time()
            # Also set the ringing event so _on_new_call doesn't wait.
            evt = asyncio.Event()
            evt.set()
            _ringing_events[call_id] = evt
            logger.info(
                "pre_originate_session_stored call_id=%s", call_id[:12],
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
        # Clean up the pre-warmed session on originate failure
        if pre_warm_session is not None:
            try:
                await _get_orchestrator().end_session(pre_warm_session)
            except Exception:
                pass
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
    else:
        # Session not registered yet — buffer for later drain in _on_new_call.
        # This covers the race where the C++ gateway starts POSTing audio before
        # _on_new_call (fired as create_task) has populated the lookup tables.
        buf = _early_audio_buffers.get(session_id)
        if buf is None:
            buf = []
            _early_audio_buffers[session_id] = buf
            logger.info(
                "early_audio_buffering session_id=%s — "
                "audio arrived before session registration",
                session_id,
            )
        if len(buf) < _EARLY_AUDIO_MAX_CHUNKS:
            buf.append(audio_bytes)

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
