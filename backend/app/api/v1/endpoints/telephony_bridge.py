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
import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain.interfaces.call_control_adapter import CallControlAdapter
from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
from app.domain.services.call_guard import CallGuard, GuardDecision, GuardResult
from app.domain.services.abuse_detection import AbuseDetectionService
from app.domain.services.call_status import TERMINAL_CALL_STATUSES
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
from app.domain.services.telephony.termination import (  # noqa: E402
    HangupProof,
    finalize_proven_inbound_termination,
    mark_termination_pending_and_load_context,
    request_confirmed_hangup,
)
from app.domain.services.telephony.config import (  # noqa: E402
    _MAX_TELEPHONY_SESSIONS,
    _RINGING_MAX_AGE_S,
)
from app.domain.services.telephony.adapter_registry import (  # noqa: E402
    register_adapter_getter,
)
from app.domain.services.event_emitter import emit_event_via_pool  # noqa: E402
from app.core.security.internal_auth import (  # noqa: E402
    CallerContext,
    is_internal_service_request,
    require_internal_or_tenant,
    resolve_call_tenant,
)
from app.api.v1.dependencies import CurrentUser, get_optional_user  # noqa: E402
from app.core.security.rbac import (  # noqa: E402
    ROLE_DEFAULT_PERMISSIONS,
    Permission,
    UserRole,
    _warn_unseeded_fallback_once,
    check_permission,
    get_effective_permissions,
    normalize_role,
    rbac_data_is_seeded,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/telephony", tags=["Telephony Bridge (generic)"])


async def _require_telephony_control(
    request: Request,
    current_user: Optional[CurrentUser] = Depends(get_optional_user),
) -> None:
    """Authorize process-wide adapter start/stop controls.

    These routes affect every tenant and every live call, so ordinary tenant
    authentication is insufficient. They are limited to the internal service
    token used by deployment automation or a real platform administrator.
    """

    if is_internal_service_request(request):
        return
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Platform-admin or internal-service authentication required",
        )
    if normalize_role(current_user.role) is not UserRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Platform admin access required",
        )


def _apply_caller_first_inbound_prompt(voice_session) -> None:
    """Wrapper kept for the existing call site. Swaps the system prompt
    for the dedicated inbound base when in caller-speaks-first mode."""
    select_inbound_base_prompt(voice_session)


# ---------------------------------------------------------------------------
# Module-level adapter instance (one per process)
# ---------------------------------------------------------------------------
_adapter: Optional[CallControlAdapter] = None

# Hand the domain layer a live view of ``_adapter`` (see adapter_registry.py
# for why this indirection exists instead of the domain layer importing this
# module directly). The lambda closes over the module global by name, so it
# keeps working after ``_adapter`` is reassigned below / in app/main.py.
register_adapter_getter(lambda: _adapter)

# Active voice sessions keyed by PBX call_id (channel_id / call UUID)
_telephony_sessions: dict[str, object] = {}  # VoiceSession objects

# _MAX_TELEPHONY_SESSIONS is now defined in
# app.domain.services.telephony.config (imported above) — the domain layer
# (lifecycle.py's watchdog/capacity checks) needs it and must not import
# this API module to get it. Kept as a module-level name here too since
# call sites in this file reference it unqualified.

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

# _RINGING_MAX_AGE_S is now defined in app.domain.services.telephony.config
# (imported above) for the same reason as _MAX_TELEPHONY_SESSIONS.


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
    _on_early_ringing,
    _on_ringing,
    _on_new_call,
    _on_audio_received,
    _on_call_ended,
    _on_transfer_answered_persisted,
    _on_transfer_connected,
    _on_transfer_cleanup_confirmed,
    _on_transfer_provider_identity_persisted,
    _on_ws_session_start,
    _admit_inbound_call,
    _persist_inbound_answered,
    _finalize_inbound_admission,
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
    _authorized: None = Depends(_require_telephony_control),
):
    """
    Connect to the active B2BUA and start handling calls.
    Use adapter_type='auto' to let the system choose based on health checks.
    """
    global _adapter, _watchdog_task

    from app.core.container import get_container
    from app.core.inbound_startup import (
        platform_inbound_enabled,
        validate_live_production_inbound_adapter,
        validate_production_inbound_adapter,
        validate_production_inbound_database_role,
        validate_production_inbound_state_backend,
    )

    environment = os.getenv("ENVIRONMENT", "development")
    inbound_enabled = await platform_inbound_enabled(
        getattr(get_container(), "db_pool", None), environment=environment
    )
    await validate_production_inbound_database_role(
        getattr(get_container(), "db_pool", None),
        environment=environment,
        inbound_enabled=inbound_enabled,
    )

    state_backend = get_state_backend()
    if environment.strip().lower() == "production" and inbound_enabled:
        validate_production_inbound_state_backend(
            environment=environment,
            inbound_enabled=inbound_enabled,
            configured_backend=os.getenv("TELEPHONY_STATE_BACKEND", "memory"),
            state_backend=state_backend,
        )

    # Ownership protects the PBX event stream itself, not only inbound
    # admission. Enforce it for every start attempt and every environment;
    # the local in-memory backend deliberately reports itself as owner.
    owns_control_plane = bool(state_backend.is_telephony_owner())
    if not owns_control_plane:
        # A stale connection on a non-owner is worse than a failed start: it
        # can consume events and reject calls belonging to the real owner.
        # Fence synchronously, then make a bounded forced handoff.
        if _adapter is not None and getattr(_adapter, "connected", False):
            fence = getattr(_adapter, "fence_ownership_loss", None)
            if callable(fence):
                fence()
            try:
                await asyncio.wait_for(
                    _adapter.disconnect(force_handoff=True),
                    timeout=10.0,
                )
            except Exception as exc:  # noqa: BLE001 - safety cleanup boundary
                logger.critical(
                    "telephony_start_nonowner_disconnect_failed err=%s",
                    exc,
                )
        raise HTTPException(
            status_code=503,
            detail=("This worker does not hold exclusive telephony ownership; " "start is refused"),
        )

    if _adapter and _adapter.connected:
        validate_live_production_inbound_adapter(
            environment=environment,
            inbound_enabled=inbound_enabled,
            configured_adapter=os.getenv("TELEPHONY_ADAPTER", "auto"),
            adapter=_adapter,
        )
        return JSONResponse(
            {
                "status": "already_connected",
                "adapter": _adapter.name,
            }
        )

    try:
        _adapter = await CallControlAdapterFactory.create(adapter_type)
        validate_production_inbound_adapter(
            environment=environment,
            inbound_enabled=inbound_enabled,
            configured_adapter=adapter_type,
            adapter=_adapter,
        )

        # Register call event handlers via the generic interface.
        # Every adapter (Asterisk, FreeSWITCH, future PBXes) implements
        # register_call_event_handlers() so the bridge never needs to
        # check adapter.name or access internal fields like _esl.
        _adapter.register_call_event_handlers(
            on_new_call=_on_new_call,
            on_call_ended=_on_call_ended,
            on_audio_received=_on_audio_received,
        )
        # Asterisk true-inbound is fail-closed: the channel remains
        # unanswered until the admission service commits the durable call and
        # returns its pinned route/config snapshot.
        if hasattr(_adapter, "set_inbound_admission_callback"):
            _adapter.set_inbound_admission_callback(_admit_inbound_call)
        if hasattr(_adapter, "set_inbound_answered_persist_callback"):
            _adapter.set_inbound_answered_persist_callback(_persist_inbound_answered)
        if hasattr(_adapter, "set_inbound_admission_finalizer"):
            _adapter.set_inbound_admission_finalizer(_finalize_inbound_admission)
        if hasattr(_adapter, "set_transfer_connected_callback"):
            _adapter.set_transfer_connected_callback(_on_transfer_connected)
        if hasattr(_adapter, "set_transfer_answered_persist_callback"):
            _adapter.set_transfer_answered_persist_callback(_on_transfer_answered_persisted)
        if hasattr(_adapter, "set_transfer_provider_identity_persist_callback"):
            _adapter.set_transfer_provider_identity_persist_callback(
                _on_transfer_provider_identity_persisted
            )
        if hasattr(_adapter, "set_transfer_cleanup_confirmed_callback"):
            _adapter.set_transfer_cleanup_confirmed_callback(_on_transfer_cleanup_confirmed)

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
        # Early-ringing (carrier 180) hook — live-status only, so the UI can
        # advance "Dialing" → "Ringing" the moment the callee's phone rings.
        if hasattr(_adapter, "set_early_ringing_callback"):
            _adapter.set_early_ringing_callback(_on_early_ringing)
        if hasattr(_adapter, "set_outbound_channel_alias_callback"):
            _adapter.set_outbound_channel_alias_callback(_alias_ringing_call_id)

        await _adapter.connect()
        validate_live_production_inbound_adapter(
            environment=environment,
            inbound_enabled=inbound_enabled,
            configured_adapter=adapter_type,
            adapter=_adapter,
        )

        # Arm the inactivity watchdog + pod-capacity readiness wiring
        # (idempotent; now shared with the boot-time auto-connect path).
        ensure_session_management_started()

        return JSONResponse(
            {
                "status": "connected",
                "adapter": _adapter.name,
                "message": f"Connected to {_adapter.name} B2BUA",
            }
        )

    except Exception as exc:
        logger.error(f"Failed to start telephony adapter: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/stop")
async def stop_telephony(
    _authorized: None = Depends(_require_telephony_control),
):
    """Proof-drain every owned call before disconnecting from the B2BUA."""
    global _adapter, _watchdog_task

    if not _adapter:
        return JSONResponse({"status": "not_running"})

    from app.domain.services.telephony.state_backend import get_state_backend
    from app.main import _terminate_active_telephony_sessions_for_shutdown

    summary = await _terminate_active_telephony_sessions_for_shutdown(
        get_state_backend(),
        _adapter,
    )
    if int(summary["deferred"]):
        # Keep the adapter and watchdog running so this process remains the
        # cleanup owner. A control-plane acknowledgement is not a hangup proof.
        return JSONResponse(
            status_code=503,
            content={
                "status": "termination_deferred",
                "message": "Telephony remains running until every PBX leg is absent",
                **summary,
            },
        )

    try:
        disconnect_summary = await _adapter.disconnect(
            drain_timeout_s=5.5,
            force_handoff=False,
        )
    except Exception as exc:
        logger.warning("telephony_stop_cleanup_deferred err=%s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "cleanup_deferred",
                "message": "Call cleanup is still active; telephony remains running",
                **summary,
            },
        )

    # Cancel the inactivity watchdog only after both PBX proof and adapter
    # cleanup have completed. A failed stop must keep normal recovery alive.
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None
    _adapter = None
    return JSONResponse(
        {
            "status": "stopped",
            **summary,
            "adapter_cleanup": disconnect_summary,
        }
    )


@router.get("/status")
async def telephony_status():
    """Return health and active call information for the current adapter."""
    if not _adapter:
        return JSONResponse(
            {
                "status": "not_started",
                "connected": False,
                "adapter": None,
            }
        )

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

    return JSONResponse(
        {
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
                    if global_current is not None
                    else None
                ),
            },
            "provider_health": provider_health,
        }
    )


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
    # Lead identity for the "who you're calling" prompt block. Optional — the
    # dialer worker only sends these when the lead has them. Absent = blind
    # dial (unchanged behaviour). Never used for auth/routing.
    lead_first_name: Optional[str] = None
    lead_last_name: Optional[str] = None
    lead_company: Optional[str] = None
    # The richer contact context (job title, best time to call, calling notes)
    # is loaded HERE from the lead row rather than forwarded in the body.
    # Sending eight more attacker-controlled strings over the wire for the
    # prompt to interpolate is a worse trade than one id and a scoped read —
    # and it keeps the dialer from having to know which fields the agent is
    # allowed to see. See contact_fields.agent_usable.
    lead_id: Optional[str] = None


async def _load_agent_lead_context(lead_id, tenant_id) -> Optional[dict]:
    """The contact fields the agent is allowed to know, for one lead.

    Filtered through ``agent_context_fields`` so do_not_call, timezone and
    contact preference can never reach the prompt regardless of what is in the
    row — the filter lives with the field definitions rather than being
    re-stated in this query, so adding a field cannot accidentally leak it.

    Never raises. A call must not fail because the extra context could not be
    loaded; it degrades to today's name-and-company behaviour.
    """
    if not lead_id or not tenant_id:
        return None
    try:
        from app.core.container import get_container
        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.contact_fields import agent_context_fields

        container = get_container()
        if not container.is_initialized:
            return None
        async with acquire_with_tenant(container.db_pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(
                """
                SELECT first_name, last_name, company_name, job_title,
                       best_time_to_call, calling_notes, email
                  FROM leads
                 WHERE id = $1::uuid
                """,
                str(lead_id),
            )
        if not row:
            return None
        ctx = agent_context_fields(dict(row))
        if ctx:
            logger.info(
                "lead_context_loaded lead=%s fields=%s",
                str(lead_id)[:8],
                sorted(ctx.keys()),
            )
        return ctx or None
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning(
            "lead_context_load_failed lead=%s — dialling with name only",
            str(lead_id)[:8],
            exc_info=True,
        )
        return None


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
    # ── Auth gate (SECURITY) ────────────────────────────────────────────
    # This endpoint has TWO legitimate callers: the internal dialer worker
    # (trusted via X-Internal-Service-Token) and a logged-in user (JWT →
    # request.state.tenant_id). Reject anyone who is neither BEFORE doing
    # any adapter/guard work. On the user path the effective tenant is
    # ALWAYS derived from the JWT — a client-supplied body.tenant_id can
    # never override it (that was the cross-tenant origination hole); a
    # mismatching body tenant is a 403. Only the trusted internal path may
    # name an arbitrary tenant in the body. Resolve the effective tenant
    # here (401/403 enforced up-front, before any adapter/guard work).
    caller_ctx = require_internal_or_tenant(request)
    effective_tenant_id = resolve_call_tenant(request, body.tenant_id, ctx=caller_ctx)

    # Unpack the request body into the local names the rest of this
    # handler uses (kept identical so the originate/guard/warmup logic
    # below is untouched by the query-string → JSON-body migration).
    destination = body.destination
    caller_id = body.caller_id
    campaign_id = body.campaign_id
    # NB: no local ``tenant_id = body.tenant_id`` — the tenant is authoritative
    # via effective_tenant_id above; never re-derive it from the raw body.
    first_speaker = body.first_speaker
    agent_name = body.agent_name
    lead_first_name = body.lead_first_name
    lead_last_name = body.lead_last_name
    lead_company = body.lead_company
    lead_context = await _load_agent_lead_context(body.lead_id, effective_tenant_id)

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
            destination,
            owner or "?",
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
            _active_session_count,
            _MAX_TELEPHONY_SESSIONS,
            destination,
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

    # effective_tenant_id was resolved up-front by resolve_call_tenant (the
    # previous ``tenant_id or request.state.tenant_id`` let the client body
    # OVERRIDE the JWT tenant — cross-tenant origination). A trusted
    # internal caller that named no tenant still lands here as None.
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
    bypass_flag = os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "false").strip().lower() not in {
        "0",
        "false",
        "no",
        "",
    }
    allow_dev_guard_bypass = environment == "development" and local_dev and bypass_flag

    # Initialize CallGuard
    from app.core.container import get_container

    container = get_container()

    # Shared-pool allotment (resolved up-front). If this tenant is allotted a
    # pool account, that account's DID is inherently trusted — the pool trunk is
    # registered with the carrier and OWNS the number — so it satisfies caller-ID
    # ownership WITHOUT a per-tenant verified DID (the pool DID is globally unique
    # and can't be verified per-tenant anyway). Resolving it here lets us (a) skip
    # the ownership gate for a pool route and (b) reuse the route below without a
    # second lookup. Fail-safe: any error → no pool route → normal path.
    _pool_route = None
    if getattr(_adapter, "name", "") == "asterisk" and effective_tenant_id:
        try:
            from app.domain.services.telephony.trunk_resolver import (
                _resolve_campaign_trunk,
                _resolve_pool_assignment,
            )

            # Campaign-level trunk override first: two campaigns of the same
            # tenant may be allotted different PBX accounts / caller-IDs.
            # Same trust model as the pool route (operator-assigned snapshot),
            # so it also satisfies the caller-ID ownership gate below.
            if campaign_id:
                _pool_route = await _resolve_campaign_trunk(
                    container.db_pool,
                    campaign_id=str(campaign_id),
                    tenant_id=str(effective_tenant_id),
                )
            if _pool_route is None:
                _pool_route = await _resolve_pool_assignment(
                    container.db_pool,
                    tenant_id=str(effective_tenant_id),
                    is_production=(environment == "production"),
                )
        except Exception:  # noqa: BLE001 — never block a call on this
            _pool_route = None

    # T0.1 — Caller-ID ownership enforcement. The check itself (env-mode
    # resolution, DID verification, fail-closed lookup) lives in the
    # telephony package; the endpoint only translates a denial into the
    # 403. See caller_id_guard.check_caller_id_ownership for the ramp-in
    # knob (CALLER_ID_ENFORCEMENT_MODE = enforce | log | off).
    # Skipped for a pool route — the pool account is the trusted caller-ID owner.
    if _pool_route is None:
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
        # Without this the per-lead ``leads.do_not_call`` check cannot run on
        # this path: CallGuard only performs it when handed a lead id, so a
        # caller reaching the bridge directly (rather than through the dialer)
        # would get the tenant DNC-list check alone and dial a contact the
        # customer explicitly flagged do-not-call.
        lead_id=body.lead_id,
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
                "reasons": [c.reason for c in guard_result.check_results if not c.passed],
                "guard_latency_ms": guard_result.total_latency_ms,
            },
        )

    if guard_result.decision == GuardDecision.THROTTLE:
        logger.warning(
            f"Call throttled by guard: tenant={effective_tenant_id}, " f"dest={destination}"
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
        lead_first_name=lead_first_name,
        lead_last_name=lead_last_name,
        lead_company=lead_company,
        lead_context=lead_context,
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

    # Per-tenant SIP-trunk resolution (isolation). Resolve which PJSIP
    # endpoint this tenant's outbound leg must go through and, for an
    # own/BYO trunk, which of their verified numbers to present as caller-ID.
    # Fail-safe: on any resolver issue this returns the platform default
    # (env endpoint, caller-ID unchanged) so today's default-trunk tenants
    # are byte-for-byte identical. Only Asterisk consumes trunk_endpoint;
    # other adapters keep their existing signature.
    trunk_endpoint: Optional[str] = None
    if getattr(_adapter, "name", "") == "asterisk":
        try:
            # Reuse the pool route resolved up-front (for the ownership-gate
            # skip); only hit the full resolver when there's no pool allotment.
            route = _pool_route
            if route is None:
                from app.domain.services.telephony.trunk_resolver import (
                    resolve_outbound_trunk,
                )

                route = await resolve_outbound_trunk(
                    container.db_pool,
                    tenant_id=str(effective_tenant_id),
                    environment=environment,
                )
            if route.refused:
                # Own-trunk-only production model: the tenant has no usable
                # own trunk / caller-ID and there is NO shared upstream to
                # fall back on. Refuse cleanly (permanent 4xx — the dialer
                # treats non-503 as a permanent failure and surfaces the
                # structured error) rather than silently mis-routing.
                logger.warning(
                    "outbound_refused_no_pbx tenant=%s dest=%s reason=%s",
                    str(effective_tenant_id)[:8],
                    destination,
                    route.reason,
                )
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "tenant_pbx_required",
                        "reason": route.reason,
                        "message": (
                            "This tenant has no active SIP trunk / verified "
                            "caller-ID. Set up your PBX (add + activate a SIP "
                            "trunk and verify a phone number) before dialing."
                        ),
                    },
                )
            if not route.is_default:
                trunk_endpoint = route.endpoint
                # Own-trunk routes carry the tenant's own dialable number
                # (or the trunk's configured caller-ID); present it. Default
                # routes leave caller_id untouched (back-compat).
                if route.caller_id:
                    caller_id = route.caller_id
                logger.info(
                    "outbound_trunk_route dest=%s tenant=%s endpoint=%s reason=%s",
                    destination,
                    str(effective_tenant_id)[:8],
                    route.endpoint,
                    route.reason,
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — never block a call on this
            logger.error(
                "outbound_trunk_route_failed tenant=%s err=%s — using default endpoint",
                str(effective_tenant_id)[:8],
                exc,
            )
            trunk_endpoint = None

    planned_call_id = (
        f"talky-out-{uuid4()}" if getattr(_adapter, "name", "") == "asterisk" else None
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
                planned_call_id,
                pre_warm_session,
                done_future,
                first_speaker=effective_first_speaker,
            )
            _sb.set_ringing_started_at(planned_call_id, asyncio.get_event_loop().time())
            _sb.set_ringing_event(planned_call_id, evt)
            _sb.set_first_speaker(planned_call_id, effective_first_speaker)
            stored_call_id = planned_call_id
            logger.info(
                "pre_originate_session_prestored call_id=%s first_speaker=%s",
                planned_call_id[:12],
                effective_first_speaker,
            )

        if planned_call_id is not None:
            call_id = await _adapter.originate_call(
                destination=destination,
                caller_id=caller_id,
                channel_id=planned_call_id,
                trunk_endpoint=trunk_endpoint,
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
                call_id,
                pre_warm_session,
                done_future,
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
                call_id[:12],
                effective_first_speaker,
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

        return JSONResponse(
            {
                "status": "calling",
                "call_id": call_id,
                "destination": destination,
                "adapter": _adapter.name,
                "guard_latency_ms": guard_result.total_latency_ms,
            }
        )

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


async def _verify_call_ownership(ctx: CallerContext, call_id: str) -> None:
    """Ensure a JWT-authenticated caller only controls their OWN tenant's call.

    Internal-token callers (the dialer / system) are trusted and skip this — they
    legitimately act on any tenant's calls. For the USER path we look the call up
    by its provider channel id (``calls.external_call_uuid``) and compare the
    owning tenant to the caller's. Fail-CLOSED: a call not on record, or one owned
    by another tenant, is a 403 — a tenant may not hang up or transfer/redirect
    another tenant's live call (the P0-6 IDOR).
    """
    if ctx.is_internal:
        return
    from app.core.container import get_container

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise HTTPException(status_code=503, detail="Call ownership check unavailable")
    try:
        async with container.db_pool.acquire() as conn:
            # The transaction is load-bearing, not decoration: SET LOCAL outside
            # an explicit transaction applies only to the implicit single-statement
            # transaction it runs in, and is discarded before the fetchrow below.
            # Without this wrapper the bypass never reaches the query — and this is
            # an ownership check, so it must not depend on inherited session state.
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                row = await conn.fetchrow(
                    "SELECT tenant_id FROM calls "
                    "WHERE external_call_uuid = $1 OR provider_call_id = $1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    call_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.error("call ownership lookup failed call_id=%s err=%s", call_id, exc)
        raise HTTPException(status_code=503, detail="Call ownership check failed")

    owner = str(row["tenant_id"]) if row and row["tenant_id"] is not None else None
    if owner is None or owner != ctx.tenant_id:
        logger.warning(
            "IDOR blocked: tenant=%s tried to control call_id=%s (owner=%s)",
            ctx.tenant_id,
            call_id,
            owner,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "call_not_owned",
                "message": "You may only control your own tenant's calls.",
            },
        )


async def _resolve_call_permissions(db_pool, user_id: str, tenant_id, role: str):
    """Current DB grants, with the unseeded-deployment fallback only.

    Same three-state contract as ``require_permission``
    (app/core/security/rbac.py): a non-empty set is authoritative and a
    revocation still denies; an EMPTY set on a deployment with no RBAC rows at
    all falls back to that role's defaults and warns once; any query error
    propagates so the caller fails closed. Production has 0 rows in
    ``role_permissions`` and 0 in ``tenant_users``, so without this every
    tenant caller loses live-call control.
    """

    permissions = await get_effective_permissions(db_pool, user_id, tenant_id)
    if permissions or await rbac_data_is_seeded(db_pool):
        return permissions
    _warn_unseeded_fallback_once()
    return ROLE_DEFAULT_PERMISSIONS.get(normalize_role(role), set())


async def _require_call_control(request: Request, *, db_pool=None) -> CallerContext:
    """Authorize destructive tenant call controls while preserving services.

    Internal service-token callers remain trusted for lifecycle cleanup and
    worker operations. A tenant session must additionally hold the existing
    ``calls:delete`` permission, matching the user-facing call hangup route.
    The permission is resolved from current database role/direct grants on
    every request; tenant identity or a stale JWT role alone is insufficient.
    """

    ctx = require_internal_or_tenant(request)
    if ctx.is_internal:
        return ctx

    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        logger.warning(
            "call_control_permission_denied tenant=%s reason=missing_user",
            ctx.tenant_id,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "permission_denied",
                "required": Permission.CALLS_DELETE.value,
            },
        )
    try:
        if db_pool is None:
            from app.core.container import get_db_pool_from_container

            db_pool = get_db_pool_from_container()
        permissions = await _resolve_call_permissions(
            db_pool,
            user_id,
            ctx.tenant_id,
            getattr(request.state, "user_role", None) or "user",
        )
    except Exception as exc:  # noqa: BLE001 - authorization fails closed
        logger.error(
            "call_control_permission_lookup_failed tenant=%s user=%s err_type=%s",
            ctx.tenant_id,
            user_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={"error": "authorization_unavailable"},
        ) from exc

    if not check_permission(permissions, Permission.CALLS_DELETE):
        logger.warning(
            "call_control_permission_denied tenant=%s user=%s",
            ctx.tenant_id,
            user_id,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "permission_denied",
                "required": Permission.CALLS_DELETE.value,
            },
        )
    return ctx


async def _require_call_read(request: Request, *, db_pool=None) -> CallerContext:
    """Authorize tenant reads of a durable transfer-attempt resource."""

    ctx = require_internal_or_tenant(request)
    if ctx.is_internal:
        return ctx
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "permission_denied",
                "required": Permission.CALLS_READ.value,
            },
        )
    try:
        if db_pool is None:
            from app.core.container import get_db_pool_from_container

            db_pool = get_db_pool_from_container()
        permissions = await _resolve_call_permissions(
            db_pool,
            user_id,
            ctx.tenant_id,
            getattr(request.state, "user_role", None) or "user",
        )
    except Exception as exc:  # noqa: BLE001 - authorization fails closed
        logger.error(
            "call_read_permission_lookup_failed tenant=%s user=%s err_type=%s",
            ctx.tenant_id,
            user_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={"error": "authorization_unavailable"},
        ) from exc
    if not check_permission(permissions, Permission.CALLS_READ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "permission_denied",
                "required": Permission.CALLS_READ.value,
            },
        )
    return ctx


@router.post("/hangup/{call_id}")
async def hangup_call(call_id: str, request: Request):
    """Hang up a specific call and report success only after PBX proof."""
    # Auth gate: internal service token OR authenticated user, THEN (user path)
    # verify the call_id belongs to the caller's tenant — closes the IDOR where
    # any authenticated tenant could hang up another tenant's live call.
    ctx = await _require_call_control(request)
    await _verify_call_ownership(ctx, call_id)
    try:
        from app.core.container import get_container

        container = get_container()
        pool = container.db_pool
        if pool is None:
            raise RuntimeError("database pool unavailable")
        termination_context = await mark_termination_pending_and_load_context(
            pool,
            call_reference=call_id,
            tenant_id=None if ctx.is_internal else ctx.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - control must fail closed
        logger.error(
            "raw_call_termination_leg_lookup_failed call=%s err_type=%s",
            call_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "termination_context_unavailable",
                "reason": "linked_leg_lookup_failed",
                "call_id": call_id,
                "provider_hangup_requested": False,
                "provider_hangup_confirmed": False,
                "provider_hangup_error": "linked_leg_lookup_failed",
            },
        ) from exc

    proof = await request_confirmed_hangup(
        _adapter,
        str(termination_context.provider_call_id or call_id),
        provider_leg_ids=termination_context.provider_leg_ids,
    )
    if not proof.confirmed:
        raise HTTPException(
            status_code=(
                504 if proof.code in {"confirmation_timeout", "hangup_unconfirmed"} else 503
            ),
            detail={
                "error": "termination_unconfirmed",
                "reason": proof.code,
                "call_id": call_id,
                "call_status": (
                    "termination_pending"
                    if termination_context.previous_status not in TERMINAL_CALL_STATUSES
                    else termination_context.previous_status
                ),
                "termination_status": (
                    "requested"
                    if termination_context.previous_status not in TERMINAL_CALL_STATUSES
                    else "failed"
                ),
                "provider_hangup_requested": proof.requested,
                "provider_hangup_confirmed": False,
                "provider_hangup_error": proof.error or proof.code,
            },
        )

    settlement_deferred = termination_context.direction != "inbound"
    if termination_context.direction == "inbound":
        try:
            answered_at = termination_context.answered_at
            if answered_at:
                now = (
                    datetime.now(timezone.utc)
                    if getattr(answered_at, "tzinfo", None)
                    else datetime.now()
                )
                duration = max(0, int((now - answered_at).total_seconds()))
            else:
                duration = 0
            await finalize_proven_inbound_termination(
                pool,
                provider_call_id=str(termination_context.provider_call_id or call_id),
                durable_call_id=termination_context.call_id,
                tenant_id=termination_context.tenant_id,
                provider=termination_context.provider or "asterisk",
                terminal_status="ended",
                duration_seconds=duration,
                reason=(
                    "raw_operator_hangup" if answered_at else "raw_operator_hangup_before_answer"
                ),
                release_only=not bool(answered_at),
                redis_client=getattr(container, "redis", None),
                campaign_id=termination_context.campaign_id,
            )
            settlement_deferred = False
        except Exception as exc:  # noqa: BLE001 - ledger/pending row retries
            settlement_deferred = True
            logger.error(
                "raw_call_termination_settlement_deferred call=%s err_type=%s",
                call_id,
                type(exc).__name__,
            )
    return JSONResponse(
        {
            "status": "confirmed",
            "call_id": call_id,
            "call_status": (
                termination_context.previous_status
                if termination_context.previous_status in TERMINAL_CALL_STATUSES
                else "termination_pending" if settlement_deferred else "ended"
            ),
            "termination_status": "confirmed",
            "provider_hangup_requested": proof.requested,
            "provider_hangup_confirmed": True,
            "provider_hangup_error": None,
            "settlement_deferred": settlement_deferred,
        }
    )


def _empty_campaign_termination_summary(
    *,
    status: str,
    error: str | None = None,
) -> dict[str, object]:
    """Return one stable, truthful campaign-termination response shape."""

    return {
        "status": status,
        "total_selected": 0,
        "requested": 0,
        "attempted": 0,  # backward-compatible internal metrics alias
        "confirmed": 0,
        "deferred": 0,
        "unconfirmed": 0,  # backward-compatible alias for ``deferred``
        "missing_identity": 0,
        "reasons": {},
        "lookup_error": error,
    }


async def hangup_calls_for_campaign(campaign_id: str) -> dict[str, object]:
    """Fence, request, and prove every active campaign-call hangup.

    Selected calls are atomically moved to non-terminal
    ``termination_pending`` before PBX requests. That is a retry marker, not a
    terminal/billing transition: the <=30-second recovery loop retains leases
    and retries until absence is proved. Persisted transfer legs are included
    so a worker restart cannot make a parent-only proof look complete.

    Lookup failures are explicit and missing provider identities remain in the
    selected/deferred totals; neither case can masquerade as zero live calls.
    """

    try:
        from app.core.container import get_container

        pool = get_container().db_pool
    except Exception as exc:
        logger.error(
            "hangup_calls_for_campaign container lookup failed: %s",
            type(exc).__name__,
        )
        return _empty_campaign_termination_summary(
            status="lookup_failed",
            error="database_unavailable",
        )
    if pool is None:
        return _empty_campaign_termination_summary(
            status="lookup_failed",
            error="database_unavailable",
        )

    active = (
        "queued",
        "dialing",
        "ringing",
        "answered",
        "in_call",
        "initiated",
        "in_progress",
        "termination_pending",
    )
    active_leg_statuses = (
        "initiated",
        "ringing",
        "answered",
        "in_progress",
        "in_call",
        "active",
    )
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                call_rows = list(
                    await conn.fetch(
                        """
                        SELECT c.id::text AS durable_call_id,
                               COALESCE(c.provider_call_id, c.external_call_uuid)
                                   AS provider_call_id
                        FROM calls c
                        WHERE c.campaign_id=$1::uuid
                          AND c.status=ANY($2::text[])
                        ORDER BY c.id
                        FOR UPDATE OF c
                        """,
                        campaign_id,
                        list(active),
                    )
                )
                durable_ids = [str(row["durable_call_id"]) for row in call_rows]
                leg_rows = (
                    list(
                        await conn.fetch(
                            """
                            SELECT call_id::text AS durable_call_id,
                                   provider_leg_id
                            FROM call_legs
                            WHERE call_id::text=ANY($1::text[])
                              AND status=ANY($2::text[])
                              AND provider_leg_id IS NOT NULL
                              AND BTRIM(provider_leg_id) <> ''
                              AND provider_leg_id NOT LIKE 'transfer-%'
                            ORDER BY call_id, created_at, id
                            """,
                            durable_ids,
                            list(active_leg_statuses),
                        )
                    )
                    if durable_ids
                    else []
                )
                legs_by_call: dict[str, list[str]] = {durable_id: [] for durable_id in durable_ids}
                for leg_row in leg_rows:
                    legs_by_call[str(leg_row["durable_call_id"])].append(
                        str(leg_row["provider_leg_id"])
                    )
                rows = [
                    {
                        "durable_call_id": str(row["durable_call_id"]),
                        "provider_call_id": row["provider_call_id"],
                        "provider_leg_ids": legs_by_call[str(row["durable_call_id"])],
                    }
                    for row in call_rows
                ]
                if durable_ids:
                    await conn.execute(
                        """
                        UPDATE calls
                           SET status='termination_pending', updated_at=NOW()
                         WHERE id::text=ANY($1::text[])
                           AND status=ANY($2::text[])
                        """,
                        durable_ids,
                        list(active),
                    )
    except Exception as exc:
        logger.error("hangup_calls_for_campaign db lookup failed: %s", exc)
        return _empty_campaign_termination_summary(
            status="lookup_failed",
            error="database_lookup_failed",
        )

    semaphore = asyncio.Semaphore(8)

    async def terminate_one(row) -> HangupProof:
        async with semaphore:
            provider_call_id = str(row["provider_call_id"] or "").strip()
            provider_leg_ids = tuple(
                dict.fromkeys(
                    normalized
                    for value in (row["provider_leg_ids"] or ())
                    if (normalized := str(value or "").strip())
                )
            )
            if provider_call_id:
                return await request_confirmed_hangup(
                    _adapter,
                    provider_call_id,
                    provider_leg_ids=provider_leg_ids,
                )
            if provider_leg_ids:
                # Clean up what we can, but without a parent identity complete
                # absence is unknowable and the durable row must stay pending.
                child_proof = await request_confirmed_hangup(
                    _adapter,
                    provider_leg_ids[0],
                    provider_leg_ids=provider_leg_ids[1:],
                )
                return HangupProof(
                    requested=child_proof.requested,
                    confirmed=False,
                    code="missing_provider_call_id",
                    error=child_proof.error,
                )
            return HangupProof(False, False, "missing_provider_call_id")

    proofs = await asyncio.gather(*(terminate_one(row) for row in rows))
    reasons: dict[str, int] = {}
    for proof in proofs:
        if not proof.confirmed:
            reasons[proof.code] = reasons.get(proof.code, 0) + 1

    total_selected = len(rows)
    requested = sum(1 for proof in proofs if proof.requested)
    confirmed = sum(1 for proof in proofs if proof.confirmed)
    deferred = total_selected - confirmed
    missing_identity = reasons.get("missing_provider_call_id", 0)
    result: dict[str, object] = {
        "status": "confirmed" if deferred == 0 else "partial",
        "total_selected": total_selected,
        "requested": requested,
        "attempted": requested,
        "confirmed": confirmed,
        "deferred": deferred,
        "unconfirmed": deferred,
        "missing_identity": missing_identity,
        "reasons": reasons,
        "lookup_error": None,
    }
    logger.info(
        "hangup_calls_for_campaign campaign=%s selected=%d requested=%d "
        "confirmed=%d deferred=%d missing_identity=%d status=%s",
        campaign_id,
        total_selected,
        requested,
        confirmed,
        deferred,
        missing_identity,
        result["status"],
    )
    return result


# ---------------------------------------------------------------------------
# Transfer endpoints
# ---------------------------------------------------------------------------


async def _enforce_inbound_transfer_policy(
    call_id: str,
    destination: str,
    *,
    mode: str = "blind",
    source: str = "api",
    idempotency_key: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    actor_type: Optional[str] = None,
):
    """Authorize and reserve an inbound transfer; outbound stays unchanged."""
    from app.core.container import get_container
    from app.domain.services.telephony.inbound_transfer import (
        InboundTransferError,
        authorize_inbound_transfer,
    )

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise HTTPException(status_code=503, detail="Transfer policy check unavailable")
    try:
        return await authorize_inbound_transfer(
            container.db_pool,
            call_reference=call_id,
            destination=destination,
            mode=mode,
            source=source,
            redis_client=getattr(container, "redis", None),
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            actor_role=actor_role,
            actor_type=actor_type,
        )
    except InboundTransferError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error": exc.code, "message": exc.message},
        ) from exc
    except Exception as exc:  # noqa: BLE001 - uncertain policy fails closed
        logger.error(
            "inbound_transfer_policy_unavailable call=%s err_type=%s",
            call_id[:12],
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="Transfer policy check unavailable")


async def _complete_transfer_attempt(attempt, result) -> None:
    if attempt is None or not getattr(attempt, "inbound", False):
        return
    from app.core.container import get_container
    from app.domain.services.telephony.inbound_transfer import (
        complete_inbound_transfer,
    )
    from app.domain.services.telephony.transfer_provider_identity import (
        load_persisted_transfer_provider_identity,
    )

    container = get_container()
    # Completion must use the exact DB-confirmed identity, including when the
    # request was cancelled after an ARI returned-ID rebind committed but
    # before the adapter could return its result payload.
    identity = await load_persisted_transfer_provider_identity(
        container.db_pool,
        tenant_id=str(getattr(attempt, "tenant_id", None) or ""),
        durable_call_id=str(getattr(attempt, "call_id", None) or ""),
        leg_id=str(getattr(attempt, "leg_id", None) or ""),
        expected_original_provider_leg_id=str(getattr(attempt, "provider_leg_id", None) or ""),
    )
    if identity.rebound:
        attempt = replace(attempt, provider_leg_id=identity.provider_leg_id)
        if isinstance(result, dict):
            observed = str(result.get("provider_leg_id") or "").strip()
            if observed and observed not in {
                identity.original_provider_leg_id,
                identity.provider_leg_id,
            }:
                raise RuntimeError("transfer_provider_identity_result_mismatch")
            result["planned_provider_leg_id"] = identity.original_provider_leg_id
            result["provider_leg_id"] = identity.provider_leg_id
            result["provider_leg_id_rebound"] = True
    status = str((result or {}).get("status") or "").strip().lower()
    succeeded = status in {"success", "completed", "transferred", "ok"}
    await complete_inbound_transfer(
        container.db_pool,
        attempt=attempt,
        succeeded=succeeded,
        result=result if isinstance(result, dict) else {"status": status},
        redis_client=getattr(container, "redis", None),
    )


async def _apply_inbound_transfer_failure_action(attempt, result: dict) -> dict:
    """Apply the pinned fallback only after provider terminal proof.

    ``cleanup_pending`` is deliberately excluded: an ambiguous target must
    retain its lease/reservation and cannot safely return to AI or start a
    second termination workflow.
    """

    if attempt is None or not getattr(attempt, "inbound", False):
        return result
    status = str(result.get("status") or "").strip().lower()
    if status in {"success", "completed", "transferred", "ok"}:
        return result
    if status in {"cleanup_pending", "unconfirmed", "termination_unconfirmed"}:
        return result

    action = str(getattr(attempt, "failure_action", "hangup") or "hangup")
    if action == "return_to_agent":
        applied = bool(
            result.get("target_termination_confirmed") and result.get("caller_media_retained")
        )
        result["fallback_action"] = action
        result["fallback_status"] = "applied" if applied else "unconfirmed"
        return result

    # True voicemail is intentionally unavailable in this release. Treat a
    # legacy voicemail fallback as fail-closed termination and report that
    # exact behavior instead of pretending a message inbox exists.
    requested_action = action
    if action not in {"hangup", "voicemail"}:
        action = "hangup"
    from app.core.container import get_container

    container = get_container()
    try:
        termination_context = await mark_termination_pending_and_load_context(
            container.db_pool,
            call_reference=str(getattr(attempt, "call_id", None) or ""),
            tenant_id=getattr(attempt, "tenant_id", None),
        )
        proof = await request_confirmed_hangup(
            _adapter,
            str(termination_context.provider_call_id or result.get("call_id") or ""),
            # Use the row-locked durable snapshot, not only the current
            # attempt object. A restart or prior retry may have left another
            # provider leg attached to the same parent, and proving only the
            # parent/current target would manufacture a terminal state while
            # that linked channel can still bill.
            provider_leg_ids=termination_context.provider_leg_ids,
        )
        result["fallback_action"] = requested_action
        result["fallback_effective_action"] = "hangup"
        result["fallback_status"] = "confirmed" if proof.confirmed else "termination_pending"
        result["fallback_reason"] = (
            "voicemail_runtime_unavailable"
            if requested_action == "voicemail"
            else proof.error or proof.code
        )
        if proof.confirmed:
            answered_at = termination_context.answered_at
            if answered_at:
                now = (
                    datetime.now(timezone.utc)
                    if getattr(answered_at, "tzinfo", None)
                    else datetime.now()
                )
                duration = max(0, int((now - answered_at).total_seconds()))
            else:
                duration = 0
            await finalize_proven_inbound_termination(
                container.db_pool,
                provider_call_id=str(
                    termination_context.provider_call_id or result.get("call_id") or ""
                ),
                durable_call_id=termination_context.call_id,
                tenant_id=termination_context.tenant_id,
                provider=termination_context.provider or "asterisk",
                terminal_status="ended",
                duration_seconds=duration,
                reason=f"transfer_failure_action_{requested_action}",
                release_only=not bool(answered_at),
                redis_client=getattr(container, "redis", None),
                campaign_id=termination_context.campaign_id,
            )
    except Exception as exc:  # noqa: BLE001 - never claim an unproved fallback
        logger.error(
            "inbound_transfer_failure_action_unconfirmed call=%s action=%s err_type=%s",
            str(getattr(attempt, "call_id", ""))[:12],
            requested_action,
            type(exc).__name__,
        )
        result["fallback_action"] = requested_action
        result["fallback_effective_action"] = "hangup"
        result["fallback_status"] = "unconfirmed"
        result["fallback_reason"] = type(exc).__name__
    return result


async def _execute_transfer(
    call_id: str,
    destination: str,
    mode: str,
    *,
    idempotency_key: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    actor_type: Optional[str] = None,
):
    """Run one policy-authorized transfer and always settle its reservation."""

    from app.domain.services.telephony.transfer_validation import (
        canonicalize_transfer_destination,
        validate_transfer_call_id,
    )

    try:
        call_id = validate_transfer_call_id(call_id)
        destination = canonicalize_transfer_destination(destination, mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not _adapter or not _adapter.connected:
        raise HTTPException(status_code=400, detail="Telephony adapter not connected")
    attempt = await _enforce_inbound_transfer_policy(
        call_id,
        destination,
        mode=mode,
        idempotency_key=idempotency_key,
        actor_id=actor_id,
        actor_role=actor_role,
        actor_type=actor_type,
    )
    if getattr(attempt, "is_replay", False):
        replay_result = getattr(attempt, "replay_result", None)
        if isinstance(replay_result, dict):
            return dict(replay_result)
        return {
            "status": "in_progress",
            "attempt_id": getattr(attempt, "leg_id", None),
            "idempotent_replay": True,
        }
    try:
        if getattr(attempt, "inbound", False):
            provider_leg_id = str(getattr(attempt, "provider_leg_id", None) or "").strip()
            if not provider_leg_id:
                raise RuntimeError("inbound transfer target identity was not persisted")
            result = await _adapter.transfer(
                call_id,
                # Execute exactly the canonical E.164 value authorized and
                # persisted by the pinned inbound policy. Never pass the raw
                # request string (which may contain SIP URI/routing syntax).
                canonicalize_transfer_destination(attempt.destination, mode),
                mode,
                provider_leg_id=provider_leg_id,
            )
        else:
            result = await _adapter.transfer(call_id, destination, mode)
        result = dict(result or {})
        if getattr(attempt, "inbound", False):
            result.setdefault("attempt_id", getattr(attempt, "leg_id", None))
            result.setdefault("idempotency_key", idempotency_key)
            result = await _apply_inbound_transfer_failure_action(attempt, result)
    except BaseException as exc:
        # Request cancellation, provider exceptions, and timeouts must not
        # strand the transfer leg or its concurrency lease in ``initiated``.
        try:
            await _complete_transfer_attempt(
                attempt,
                {
                    "status": "cleanup_pending",
                    "reason": "adapter_exception",
                    "error_type": type(exc).__name__,
                    # Repeat the exact pre-persisted target identity in the
                    # result. The domain service also falls back to the
                    # attempt, but recovery must never lose this PBX leg if a
                    # future result-shape refactor removes that fallback.
                    "provider_leg_id": getattr(
                        attempt,
                        "provider_leg_id",
                        None,
                    ),
                },
            )
        except Exception as settlement_exc:  # noqa: BLE001
            logger.exception(
                "transfer_failure_settlement_failed call=%s mode=%s err_type=%s",
                call_id[:12],
                mode,
                type(settlement_exc).__name__,
            )
        raise
    await _complete_transfer_attempt(attempt, result)
    return result


def _transfer_request_metadata(request: Request) -> dict[str, Optional[str]]:
    from app.domain.services.telephony.transfer_validation import (
        validate_transfer_idempotency_key,
    )

    try:
        idempotency_key = validate_transfer_idempotency_key(request.headers.get("idempotency-key"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    internal = is_internal_service_request(request)
    return {
        "idempotency_key": idempotency_key,
        "actor_id": None if internal else getattr(request.state, "user_id", None),
        "actor_role": (
            "internal_service" if internal else str(getattr(request.state, "user_role", "unknown"))
        ),
        "actor_type": "service" if internal else "user",
    }


def _transfer_http_response(result: dict) -> JSONResponse:
    status = str(result.get("status") or "").lower()
    response_status = (
        202
        if status
        in {
            "in_progress",
            "cleanup_pending",
            "reconciliation_required",
        }
        or result.get("fallback_status") in {"termination_pending", "unconfirmed"}
        else 200
    )
    attempt_id = result.get("attempt_id")
    headers = {}
    if attempt_id:
        headers["Location"] = f"/api/v1/sip/telephony/transfer/attempts/{attempt_id}"
    return JSONResponse(result, status_code=response_status, headers=headers)


@router.post("/transfer/blind")
async def transfer_blind(payload: TransferPayload, request: Request):
    ctx = await _require_call_control(request)
    if payload.mode not in (None, "blind"):
        raise HTTPException(status_code=422, detail="payload mode does not match route")
    await _verify_call_ownership(ctx, payload.call_id)
    result = await _execute_transfer(
        payload.call_id,
        payload.destination,
        "blind",
        **_transfer_request_metadata(request),
    )
    return _transfer_http_response(result)


@router.post("/transfer/attended")
async def transfer_attended(payload: TransferPayload, request: Request):
    ctx = await _require_call_control(request)
    if payload.mode not in (None, "attended"):
        raise HTTPException(status_code=422, detail="payload mode does not match route")
    await _verify_call_ownership(ctx, payload.call_id)
    result = await _execute_transfer(
        payload.call_id,
        payload.destination,
        "attended",
        **_transfer_request_metadata(request),
    )
    return _transfer_http_response(result)


@router.post("/transfer/deflect")
async def transfer_deflect(payload: TransferPayload, request: Request):
    ctx = await _require_call_control(request)
    if payload.mode not in (None, "deflect"):
        raise HTTPException(status_code=422, detail="payload mode does not match route")
    await _verify_call_ownership(ctx, payload.call_id)
    result = await _execute_transfer(
        payload.call_id,
        payload.destination,
        "deflect",
        **_transfer_request_metadata(request),
    )
    return _transfer_http_response(result)


@router.get("/transfer/attempts/{attempt_id}")
async def get_transfer_attempt(attempt_id: str, request: Request):
    """Return the tenant-scoped durable state of one transfer attempt."""

    ctx = await _require_call_read(request)
    from app.core.container import get_container
    from app.domain.services.telephony.inbound_transfer import (
        get_inbound_transfer_attempt,
    )

    container = get_container()
    if container.db_pool is None:
        raise HTTPException(status_code=503, detail="Transfer status unavailable")
    try:
        result = await get_inbound_transfer_attempt(
            container.db_pool,
            attempt_id=attempt_id,
            tenant_id=None if ctx.is_internal else ctx.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Transfer attempt not found")
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

    # Caller audio is an internal, state-mutating media callback.  It must never
    # be exposed as a compatibility fallback: an unset INTERNAL_SERVICE_TOKEN
    # deliberately rejects every request, and the gateway deployment must be
    # upgraded atomically with this backend contract.
    if not is_internal_service_request(request):
        logger.warning(
            "gateway_audio_rejected session_id=%s reason=missing_internal_token",
            session_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Internal-service authentication required",
        )

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

    # EXACT lookup only (registered in _on_new_call). We intentionally do NOT
    # fall back to a truncated-prefix match here: session ids are
    # ``asterisk-<call_id[:12]>-<port>`` and for outbound ids of the form
    # ``talky-out-<uuid>`` the first 12 chars are ``talky-out-`` (10 fixed
    # chars) + 2 hex — only ~8 bits of entropy. A prefix fallback would
    # therefore let two concurrent calls whose ids collide in those bytes cross
    # audio (one prospect's words entering another call's STT + recording), and
    # it CACHED the first prefix hit permanently, making the mis-route stick for
    # the rest of the call. Instead, audio that arrives before the exact mapping
    # exists is buffered by its full session_id (below) and replayed by
    # _on_new_call once the session registers — so early audio can never bind to
    # the wrong session; at worst a few opening packets are briefly queued.
    matched_call_id: Optional[str] = _sb.get_call_id_for_gateway_session(session_id)

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
