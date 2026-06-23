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
from uuid import uuid4
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService
from app.api.v1.schemas.telephony_bridge import TransferPayload

# ---------------------------------------------------------------------------
# Backward-compat re-exports — implementations live in the telephony package.
# ---------------------------------------------------------------------------
from app.domain.services.telephony.config import (  # noqa: E402, F401
    _outbound_first_speaker,  # re-exported; consumed by tests + _apply wrapper
)
from app.domain.services.telephony.modes.caller_first import (  # noqa: E402
    select_inbound_base_prompt,
)
from app.domain.services.telephony.state_backend import (  # noqa: E402
    get_state_backend,
)
from app.domain.services.telephony.caller_id_guard import (  # noqa: E402
    check_caller_id_ownership,
)
from app.domain.services.telephony.prewarm import (  # noqa: E402
    prepare_prewarmed_session,
)
from app.domain.services.telephony.failure_reasons import (  # noqa: E402
    humanize_failure,
)
from app.domain.services.event_emitter import emit_event_via_pool  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/telephony", tags=["Telephony Bridge (generic)"])


def _apply_caller_first_inbound_prompt(voice_session) -> None:
    """Wrapper kept for the existing call site. Swaps the system prompt
    for the dedicated inbound base when in caller-speaks-first mode."""
    select_inbound_base_prompt(voice_session)

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


def _alias_ringing_call_id(original_call_id: str, actual_call_id: str) -> None:
    """
    Move pre-originate warmup state when Asterisk replaces our planned channel
    ID with a trunk-created channel ID.

    This keeps caller-speaks-first isolated from the default ringing warmup:
    the first real PBX channel consumes the exact prewarmed session whose
    prompt and _first_speaker were prepared before dialing.
    """
    moved = get_state_backend().alias_ringing_call(original_call_id, actual_call_id)
    if moved:
        logger.info(
            "ringing_warmup_alias_moved original_call_id=%s actual_call_id=%s",
            original_call_id[:12],
            actual_call_id[:12],
        )




# ---------------------------------------------------------------------------
# Helpers / lifecycle (implementations live in the telephony package)
# ---------------------------------------------------------------------------
from app.domain.services.telephony.lifecycle import (  # noqa: E402
    _get_orchestrator,
    _session_watchdog,
    _SESSION_INACTIVITY_TIMEOUT_S,
    _on_ringing,
    _on_new_call,
    _on_audio_received,
    _on_call_ended,
    _on_ws_session_start,
)

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

def ensure_session_management_started() -> None:
    """Arm the session inactivity watchdog + pod-capacity readiness wiring.

    Idempotent. Must be called by EVERY path that brings telephony up — both
    the REST ``/start`` endpoint and the boot-time auto-connect in the
    ``main.py`` lifespan. Previously this logic lived only inside
    ``start_telephony``, so a normal systemd deploy (which boots via the
    lifespan, not the REST endpoint) left the pod-capacity drain gate and the
    zombie-session watchdog permanently disarmed. (audit #9)
    """
    global _watchdog_task
    # GAP 5 — session inactivity watchdog (also does zombie reconcile,
    # ringing-warmup GC, global-concurrency lease refresh, dead-pod recovery).
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(_session_watchdog())
        logger.info("telephony_watchdog: started (inactivity=%ds)", _SESSION_INACTIVITY_TIMEOUT_S)
    # Phase 1.4 — wire pod capacity into the readiness probe so the k8s/LB
    # readiness gate can drain a saturated pod without touching internals.
    from app.core import readiness as _readiness
    _readiness.set_capacity_providers(
        active_count=lambda: get_state_backend().voice_session_count(),
        max_capacity=lambda: _MAX_TELEPHONY_SESSIONS,
    )


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
        if hasattr(_adapter, "set_outbound_channel_alias_callback"):
            _adapter.set_outbound_channel_alias_callback(_alias_ringing_call_id)

        await _adapter.connect()

        # Arm the inactivity watchdog + pod-capacity readiness wiring
        # (idempotent; now shared with the boot-time auto-connect path).
        ensure_session_management_started()

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

    # Read the per-pod active count once through the state backend so the
    # three capacity fields below stay consistent within this response.
    _active_session_count = get_state_backend().voice_session_count()

    return JSONResponse({
        "status": "running" if healthy else "degraded",
        "connected": _adapter.connected,
        "adapter": _adapter.name,
        "active_sessions": _active_session_count,
        "healthy": healthy,
        "capacity": {
            # Per-pod count stays so single-pod dashboards don't break.
            "current": _active_session_count,
            "max": _MAX_TELEPHONY_SESSIONS,
            "pct_used": round(_active_session_count / max(_MAX_TELEPHONY_SESSIONS, 1) * 100, 1),
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


class MakeCallRequest(BaseModel):
    """Body for POST /sip/telephony/call.

    This endpoint is an INTERNAL service-to-service entrypoint — the
    dialer worker (a separate process) calls it to originate through the
    API process that owns the persistent ARI adapter. It used to take
    query-string params; a JSON body avoids the ``+``-as-space E.164
    encoding foot-gun that query strings have, and pairs with the
    ``X-Internal-Service-Token`` CSRF exemption (see core/security/csrf).
    """
    destination: str
    caller_id: str = "1001"
    campaign_id: Optional[str] = None
    tenant_id: Optional[str] = None
    first_speaker: Optional[Literal["agent", "user"]] = None
    agent_name: Optional[str] = None


@router.post("/call")
async def make_call(request: Request, body: MakeCallRequest):
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
    # Unpack the request body into the local names the rest of this
    # handler uses (kept identical so the originate/guard/warmup logic
    # below is untouched by the query-string → JSON-body migration).
    destination = body.destination
    caller_id = body.caller_id
    campaign_id = body.campaign_id
    tenant_id = body.tenant_id
    first_speaker = body.first_speaker
    agent_name = body.agent_name

    # Single-owner guard. Only the process holding the ARI owner lock may
    # originate — its the only one with a live Asterisk connection and the
    # per-call state lives in its memory. A non-owner (stray worker / bad
    # deploy / --workers >1) returns a RETRYABLE 503 so the dialer bounces
    # the job to the owner, rather than the 400 the adapter check below
    # would give (which the dialer treats as a permanent failure). On the
    # in-memory backend is_telephony_owner() is always True — no change to
    # single-worker behaviour.
    _sb = get_state_backend()
    if not _sb.is_telephony_owner():
        owner = await _sb.telephony_owner_id()
        logger.warning(
            "make_call_not_owner dest=%s owner=%s — refusing on non-owner process",
            destination, owner or "?",
        )
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "2"},
            detail={"error": "telephony_not_active_on_node", "owner": owner},
        )

    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")

    # Phase 1.4 — refuse new calls EARLY when the pod is full or draining.
    # 503 + Retry-After is the contract the LB / dialer worker reads to
    # bounce the request to another pod. We do this before invoking
    # CallGuard so a saturated pod doesn't burn DB / Redis cycles.
    from app.core import readiness as _readiness
    if _readiness.is_draining():
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": str(_readiness.retry_after_seconds_for_capacity())},
            detail={"error": "pod_draining"},
        )
    if _readiness.is_pod_at_capacity():
        _active_session_count = get_state_backend().voice_session_count()
        logger.warning(
            "make_call_pod_at_capacity active=%d cap=%d dest=%s",
            _active_session_count, _MAX_TELEPHONY_SESSIONS, destination,
        )
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": str(_readiness.retry_after_seconds_for_capacity())},
            detail={
                "error": "pod_at_capacity",
                "active_sessions": _active_session_count,
                "max_sessions": _MAX_TELEPHONY_SESSIONS,
            },
        )

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

    # T0.1 — Caller-ID ownership enforcement. The check itself (env-mode
    # resolution, DID verification, fail-closed lookup) lives in the
    # telephony package; the endpoint only translates a denial into the
    # 403. See caller_id_guard.check_caller_id_ownership for the ramp-in
    # knob (CALLER_ID_ENFORCEMENT_MODE = enforce | log | off).
    caller_id_decision = await check_caller_id_ownership(
        container.db_pool,
        tenant_id=str(effective_tenant_id),
        caller_id=caller_id,
        environment=environment,
    )
    if not caller_id_decision.allowed:
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
                "require_attestation": caller_id_decision.require_attestation,
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

    # Guard passed — pre-warm the full voice pipeline BEFORE originating so
    # the greeting audio + STT/TTS/LLM connections are hot even when the
    # callee answers instantly (local PBX loop). The warmup logic lives in
    # the telephony package; the endpoint only resolves the result and maps
    # a cold pipeline to its 503.
    prewarm = await prepare_prewarmed_session(
        first_speaker=first_speaker,
        campaign_id=campaign_id,
        agent_name=agent_name,
        container=container,
    )
    effective_first_speaker = prewarm.effective_first_speaker
    pre_warm_session = prewarm.session

    # Strict gate: do not ring the bell unless the pipeline is fully ready.
    # Agent-first mode: greeting buffered. User-first mode: STT + TTS WS open
    # so Flux is ready to listen the instant the callee picks up.
    if pre_warm_session is None:
        # Surface WHY the call couldn't start in the live activity feed so the
        # operator sees it in the dashboard (e.g. "TTS out of credits") instead
        # of only a server log. Best-effort — never blocks the 503.
        failure_category, human_msg = humanize_failure(prewarm.failure_reason)
        if effective_tenant_id:
            await emit_event_via_pool(
                container.db_pool,
                tenant_id=str(effective_tenant_id),
                category="call",
                title="Call could not start",
                description=human_msg,
                severity="critical",
                related_campaign_id=str(campaign_id) if campaign_id else None,
                metadata={
                    "failure_category": failure_category,
                    "failure_reason": prewarm.failure_reason,
                    "destination": destination,
                },
            )
        detail_msg = (
            "Voice pipeline is not ready. Refusing to originate the call "
            "to avoid silence on pickup. Check TTS/STT provider health."
        )
        if prewarm.failure_reason:
            detail_msg = f"{detail_msg} (cause: {prewarm.failure_reason})"
        raise HTTPException(status_code=503, detail=detail_msg)

    planned_call_id = (
        f"talky-out-{uuid4()}"
        if getattr(_adapter, "name", "") == "asterisk"
        else None
    )
    stored_call_id: Optional[str] = None

    try:
        # Store the pre-warmed session BEFORE dialing when the adapter supports
        # caller-supplied channel IDs. Asterisk fires _on_ringing from ARI
        # StasisStart, which can happen before originate_call() returns. If the
        # store happens after dialing, _on_ringing creates a second default
        # agent-first session and caller-first turn 0 can overlap with greeting.
        if pre_warm_session is not None and planned_call_id is not None:
            done_future: asyncio.Future = asyncio.get_event_loop().create_future()
            done_future.set_result(None)
            evt = asyncio.Event()
            evt.set()
            _sb = get_state_backend()
            _sb.set_ringing_warmup(
                planned_call_id, pre_warm_session, done_future,
                first_speaker=effective_first_speaker,
            )
            _sb.set_ringing_started_at(planned_call_id, asyncio.get_event_loop().time())
            _sb.set_ringing_event(planned_call_id, evt)
            _sb.set_first_speaker(planned_call_id, effective_first_speaker)
            stored_call_id = planned_call_id
            logger.info(
                "pre_originate_session_prestored call_id=%s first_speaker=%s",
                planned_call_id[:12], effective_first_speaker,
            )

        if planned_call_id is not None:
            call_id = await _adapter.originate_call(
                destination=destination,
                caller_id=caller_id,
                channel_id=planned_call_id,
            )
        else:
            call_id = await _adapter.originate_call(
                destination=destination,
                caller_id=caller_id,
            )

        # Non-Asterisk adapters do not expose a pre-generated channel ID, so we
        # keep the legacy post-originate store. Also reconcile defensively if an
        # adapter returns a different ID despite accepting planned_call_id.
        if pre_warm_session is not None and stored_call_id != call_id:
            done_future: asyncio.Future = asyncio.get_event_loop().create_future()
            done_future.set_result(None)
            evt = asyncio.Event()
            evt.set()
            _sb = get_state_backend()
            _sb.set_ringing_warmup(
                call_id, pre_warm_session, done_future,
                first_speaker=effective_first_speaker,
            )
            _sb.set_ringing_started_at(call_id, asyncio.get_event_loop().time())
            _sb.set_ringing_event(call_id, evt)
            _sb.set_first_speaker(call_id, effective_first_speaker)
            if stored_call_id is not None:
                _sb.pop_ringing_warmup(stored_call_id)
                _sb.clear_ringing_started_at(stored_call_id)
                _sb.pop_ringing_event(stored_call_id)
                _sb.clear_first_speaker(stored_call_id)
            stored_call_id = call_id
            logger.info(
                "pre_originate_session_stored call_id=%s first_speaker=%s",
                call_id[:12], effective_first_speaker,
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
        if stored_call_id is not None:
            _sb = get_state_backend()
            _sb.pop_ringing_warmup(stored_call_id)
            _sb.clear_ringing_started_at(stored_call_id)
            _sb.pop_ringing_event(stored_call_id)
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


async def hangup_calls_for_campaign(campaign_id: str) -> int:
    """Best-effort hangup of every active call belonging to ``campaign_id``.

    Used by the stop-campaign flow so that hitting Stop in the UI actually
    drops in-progress calls instead of letting them complete on their own.
    Reads the live ``calls`` table — survives even if a call's in-memory
    session is on a different worker process — and asks the telephony
    adapter to hang up each one. Failures per call_id are swallowed (the
    adapter may already have ended the channel); we still attempt the rest.

    Returns the number of hangup attempts dispatched.
    """
    if not _adapter:
        return 0
    try:
        from app.core.container import get_container
        pool = get_container().db_pool
    except Exception:
        return 0
    if pool is None:
        return 0
    # Every NON-terminal call state the dialer/telephony can leave a call in.
    # MUST include "dialing" (originate accepted, channel ringing out) and
    # "in_call" (media flowing / AI talking) — these are the live CallState
    # values (see call_status.CallState); omitting them was why Stop left calls
    # stuck dialing or still talking. Legacy aliases (initiated/in_progress)
    # kept so old rows are still swept.
    active = (
        "queued", "dialing", "ringing", "answered", "in_call",
        "initiated", "in_progress",
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute("SET LOCAL app.bypass_rls = 'on'")
            rows = await conn.fetch(
                "SELECT external_call_uuid FROM calls "
                "WHERE campaign_id = $1::uuid AND status = ANY($2::text[])",
                campaign_id, list(active),
            )
    except Exception as exc:
        logger.error("hangup_calls_for_campaign db lookup failed: %s", exc)
        return 0
    attempts = 0
    for row in rows:
        call_id = row["external_call_uuid"]
        if not call_id:
            continue
        try:
            await _adapter.hangup(call_id)
            attempts += 1
        except Exception as exc:
            logger.warning(
                "hangup_calls_for_campaign hangup failed call_id=%s err=%s",
                call_id, exc,
            )
    if attempts:
        logger.info(
            "hangup_calls_for_campaign campaign=%s hung_up=%d",
            campaign_id, attempts,
        )
    return attempts


# ---------------------------------------------------------------------------
# Transfer endpoints
# ---------------------------------------------------------------------------

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

    _sb = get_state_backend()

    # Direct lookup first (fast path — registered in _on_new_call).
    matched_call_id: Optional[str] = _sb.get_call_id_for_gateway_session(session_id)

    # Fallback: prefix match for race conditions before mapping is registered.
    if not matched_call_id:
        for call_id, _vs in _sb.iter_voice_session_items():
            if session_id.startswith(f"asterisk-{call_id[:12]}"):
                matched_call_id = call_id
                # Cache it to speed up future lookups.
                _sb.set_call_id_for_gateway_session(session_id, call_id)
                break

    if matched_call_id:
        await _on_audio_received(matched_call_id, audio_bytes)
    else:
        # Session not registered yet — buffer for later drain in _on_new_call.
        # This covers the race where the C++ gateway starts POSTing audio before
        # _on_new_call (fired as create_task) has populated the lookup tables.
        new_len = _sb.append_early_audio(session_id, audio_bytes)
        if new_len == 1:
            logger.info(
                "early_audio_buffering session_id=%s — "
                "audio arrived before session registration",
                session_id,
            )

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
