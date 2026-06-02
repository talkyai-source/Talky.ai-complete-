"""Telephony call-lifecycle orchestration.

Owns the per-call event handlers that the active call-control adapter
invokes: ringing-phase pre-warm, on-answer session creation, audio
routing, call-end teardown, and the orphan-session watchdog. The
endpoint module (``telephony_bridge.py``) registers these as adapter
callbacks during ``start_telephony``.

**State ownership note.** The module state singletons (``_adapter``,
``_telephony_sessions``, ``_watchdog_task``, the ringing warmup cache,
the early-audio buffer, the gateway-session map) live on
``telephony_bridge.py``, not here. ``app/main.py`` writes the adapter
directly via ``_tb._adapter = ...`` at startup, so the bridge has to
remain the canonical owner of those names. Functions in this module
read and mutate that state through the ``_bridge()`` helper below.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

# Imports from already-extracted telephony submodules
from app.domain.services.telephony.config import (
    _build_telephony_session_config,
    _build_outbound_greeting,
)
from app.domain.services.telephony.modes import resolve_first_speaker
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting
from app.domain.services.telephony.recording import _save_call_recording
from app.services.scripts import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)
from app.domain.services.call_service import CallService
from app.domain.services.telephony.outcome_resolver import resolve_call_outcome

logger = logging.getLogger(__name__)


def _bridge():
    """Lazy import of the bridge module to access shared state.

    The bridge owns the telephony module state singletons; lifecycle
    functions reach them through this indirection so the bridge module
    stays the single point of write (``app/main.py`` assigns
    ``_tb._adapter = ...`` at startup, which would not work through a
    ``__getattr__`` shim).
    """
    from app.api.v1.endpoints import telephony_bridge
    return telephony_bridge


def _state():
    """The telephony state backend (Phase 1, item 1 of the architecture
    roadmap). All per-call state reads/writes go through this so the
    Redis-backed backend can mirror them for restart recovery. Lazy
    import keeps the module-load graph acyclic — same rationale as
    ``_bridge()`` above. ``_adapter`` is NOT state-backend-managed (it's
    a live ARI/ESL connection); it stays accessed via ``_bridge()``.
    """
    from app.domain.services.telephony.state_backend import get_state_backend
    return get_state_backend()


# Watchdog timeouts (read once at import time — match prior bridge behaviour).
_SESSION_INACTIVITY_TIMEOUT_S = int(os.getenv("TELEPHONY_INACTIVITY_TIMEOUT_S", "300"))
_SESSION_MAX_DURATION_S = int(os.getenv("TELEPHONY_MAX_CALL_DURATION_S", "3600"))


def _pop_ringing_warmup(call_id: str):
    """
    Atomically pop a ringing-phase warmup entry and its parallel timestamp.

    Returns the (VoiceSession, connect_task) tuple if present, else None.
    Callers are responsible for cancelling the task and ending the session.
    """
    _sb = _state()
    _sb.clear_ringing_started_at(call_id)
    return _sb.pop_ringing_warmup(call_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


# ---------------------------------------------------------------------------
# Audio pipeline lifecycle (called when a new call arrives on any B2BUA)
# ---------------------------------------------------------------------------


async def _session_watchdog() -> None:
    """
    Periodically scan active sessions and tear down any that have been silent
    for longer than _SESSION_INACTIVITY_TIMEOUT_S.

    Also sweeps orphaned ringing-phase pre-warm entries — outbound calls whose
    callee never answered and whose terminal Asterisk event never fired (rare
    but possible on carrier-side glitches). Without this sweep the open
    Deepgram + TTS WebSockets leak per unanswered call and exhaust API quota
    over a long campaign.

    Prevents resource leaks when a PBX crashes or drops the control connection
    without sending a hangup event (so _on_call_ended never fires).
    """
    while True:
        try:
            await asyncio.sleep(30)
            now = asyncio.get_event_loop().time()
            _sb = _state()

            # ----- Active session inactivity sweep -----
            stale = []
            for call_id, vs in _sb.iter_voice_session_items():
                # Refresh the Redis ledger TTL for every live call each
                # tick (debounced in the backend) so a call that's up but
                # momentarily silent — caller on hold, long agent turn —
                # doesn't let its ledger entry expire and become an
                # un-recoverable zombie. No-op on the memory backend.
                _sb.touch_call(call_id)
                # FIX 1 — last_activity_at lives on CallSession (vs.call_session),
                # not VoiceSession (vs).  Use the pre-built is_stale() method which
                # compares datetime correctly instead of mixing monotonic time + datetime.
                call_session = getattr(vs, "call_session", None)
                if call_session and call_session.is_stale(_SESSION_INACTIVITY_TIMEOUT_S):
                    stale.append(call_id)
            for call_id in stale:
                logger.warning(
                    "telephony_watchdog: stale session %s (inactive >%ds) — forcing end",
                    call_id[:12], _SESSION_INACTIVITY_TIMEOUT_S,
                )
                await _on_call_ended(call_id)

            # ----- Orphaned ringing-warmup sweep (bug #3 / #7) -----
            stale_ringing = [
                cid for cid, created_at in _sb.iter_ringing_started_at_items()
                if (now - created_at) > _bridge()._RINGING_MAX_AGE_S
            ]
            for cid in stale_ringing:
                ringing = _pop_ringing_warmup(cid)
                _sb.pop_ringing_event(cid)
                logger.warning(
                    "telephony_watchdog: orphaned ringing_warmup %s "
                    "(age >%ds) — releasing STT/TTS sockets",
                    cid[:12], _bridge()._RINGING_MAX_AGE_S,
                    extra={"call_id": cid, "alert": "ringing_warmup_orphan"},
                )
                if ringing is not None:
                    ringing_session, ringing_connect_task = ringing
                    if ringing_connect_task is not None and not ringing_connect_task.done():
                        ringing_connect_task.cancel()
                    try:
                        await _get_orchestrator().end_session(ringing_session)
                    except Exception as exc:
                        logger.debug(
                            "Watchdog end_session failed for %s: %s", cid[:12], exc,
                        )

            # ----- Orphaned ringing_events sweep -----
            # Events without a matching warmup entry are pure leakage; drop on
            # the same age policy. (Events with a matching warmup entry get
            # cleaned up by the warmup sweep above.)
            _warmup_keys = {cid for cid, _ in _sb.iter_ringing_started_at_items()}
            _session_keys = {cid for cid, _ in _sb.iter_voice_session_items()}
            stale_events = [
                cid for cid in _sb.iter_ringing_event_keys()
                if cid not in _warmup_keys and cid not in _session_keys
            ]
            for cid in stale_events:
                _sb.pop_ringing_event(cid)

            # ----- Phase 1.3: orphan sweep across remaining session-keyed maps
            # Anything keyed by gateway_session_id whose call_id is no longer
            # an active or ringing-warming session is leakage from a crashed
            # call path that never called _on_call_ended. Drop on the same age
            # policy as ringing warmups.
            active_call_ids = _session_keys | _warmup_keys
            orphan_gw = [
                gw_id for gw_id, cid in _sb.iter_gateway_session_items()
                if cid not in active_call_ids
            ]
            for gw_id in orphan_gw:
                _sb.remove_gateway_session(gw_id)
                buf = _sb.drain_early_audio(gw_id)
                if buf:
                    logger.warning(
                        "telephony_watchdog: dropping orphan early_audio_buffer "
                        "gateway_session_id=%s chunks=%d",
                        gw_id, len(buf),
                    )

            # Buffers that exist without any gateway-session mapping at all are
            # dead audio from calls that never registered. Cap their age too.
            _gw_keys = {gw for gw, _ in _sb.iter_gateway_session_items()}
            for gw_id in _sb.iter_early_audio_keys():
                if gw_id not in _gw_keys:
                    _sb.discard_early_audio(gw_id)

            # ----- Cartesia per-call WS sweep -----
            # cartesia.py keeps a per-call WS in _call_ws / _call_ws_locks /
            # _call_keys. The on-end path (_on_call_ended → end_session →
            # tts_provider.disconnect_for_call) handles the happy case, but a
            # crashed call path can leave entries behind. Reconcile against the
            # active session list every cycle.
            try:
                from app.core.container import get_container as _gc
                _c = _gc()
                if _c.is_initialized:
                    # The Cartesia provider is held inside live VoiceSessions, so
                    # iterate every still-live one and ask it to evict any
                    # internal call_id state that no longer matches an active
                    # session. This is a no-op when state is already clean.
                    cartesia_singletons = set()
                    _live_sessions = [vs for _, vs in _sb.iter_voice_session_items()]
                    for vs in _live_sessions:
                        tts = getattr(vs, "tts_provider", None)
                        if tts is None or getattr(tts, "name", None) != "cartesia":
                            continue
                        cartesia_singletons.add(id(tts))
                        live_ids = {
                            getattr(v, "call_id", None)
                            for v in _live_sessions
                        }
                        live_ids.discard(None)
                        for cid in list(getattr(tts, "_call_ws", {}).keys()):
                            if cid not in live_ids:
                                logger.warning(
                                    "telephony_watchdog: evicting orphan cartesia WS "
                                    "call_id=%s", str(cid)[:12],
                                )
                                try:
                                    await tts.disconnect_for_call(cid)
                                except Exception as exc:
                                    logger.debug(
                                        "cartesia evict failed call_id=%s: %s",
                                        str(cid)[:12], exc,
                                    )
            except Exception as exc:
                logger.debug("cartesia_orphan_sweep_failed err=%s", exc)

            # ----- T1.2 global-concurrency maintenance -----
            # Refresh a lease for every live call on this pod, then
            # reconcile the cluster-wide set to drop orphans from
            # crashed peers. Best-effort — failures don't touch local
            # state.
            try:
                from app.domain.services.global_concurrency import (
                    reconcile_orphans,
                    refresh_lease,
                )
                from app.core.container import get_container as _gc
                _c = _gc()
                _redis = getattr(_c, "redis", None) if _c.is_initialized else None
                if _redis is not None:
                    for live_id, _ in _sb.iter_voice_session_items():
                        await refresh_lease(_redis, call_id=live_id)
                    await reconcile_orphans(_redis)
            except Exception as exc:
                logger.debug("global_concurrency_watchdog_step_failed err=%s", exc)

            # ----- Phase 1 item 1: dead-process call recovery -----
            # Reclaim calls left behind by a crashed peer incarnation
            # whose heartbeat has now expired. On a graceful restart the
            # successor handles these at startup; this watchdog pass
            # catches the hard-crash case once the dead heartbeat TTLs
            # out (~60s). No-op on the memory backend.
            try:
                await recover_orphaned_calls()
            except Exception as exc:
                logger.debug("orphan_recovery_watchdog_step_failed err=%s", exc)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("telephony_watchdog error: %s", exc)


async def recover_orphaned_calls() -> int:
    """Hang up and record calls abandoned by a dead process incarnation.

    ``state_backend.recover_orphans()`` returns ledger entries whose
    owning incarnation's heartbeat is gone (and atomically claims them so
    they're processed once). For each, we:

      1. Best-effort hang up the PBX channel — a no-op if Asterisk
         already tore it down when the owning process died, but it
         releases the channel if Asterisk parked it.
      2. Emit a Track-B call-ended stream event (via the provider call
         id → ``calls`` row resolver) so the dashboard shows the call as
         ended-on-restart instead of stuck "in call" forever.

    Returns the number of calls recovered. No-op (returns 0) on the
    in-memory backend, which has no cross-process ledger.
    """
    sb = _state()
    try:
        orphans = await sb.recover_orphans()
    except Exception as exc:
        logger.warning("recover_orphaned_calls: recover_orphans failed: %s", exc)
        return 0
    if not orphans:
        return 0

    from app.core.container import get_container as _gc
    from app.domain.services.call_status import (
        CallState, record_call_state_by_provider_id,
    )
    container = _gc()
    db_pool = getattr(container, "db_pool", None) if container.is_initialized else None
    adapter = _bridge()._adapter

    recovered = 0
    for entry in orphans:
        call_id = entry.get("call_id")
        if not call_id:
            continue
        # 1. Best-effort PBX hangup.
        if adapter is not None:
            try:
                await adapter.hangup(call_id)
            except Exception as exc:
                logger.debug("orphan_recovery hangup_noop call=%s err=%s", call_id[:12], exc)
        # 2. Record the terminal state so the UI/outcome is accurate.
        if db_pool is not None:
            try:
                await record_call_state_by_provider_id(
                    db_pool,
                    provider_call_id=call_id,
                    new_state=CallState.ENDED,
                    metadata={
                        "description": "Call ended — recovered after a process restart",
                        "reason": "process_restart_recovery",
                        "prior_owner": entry.get("pod_id"),
                    },
                )
            except Exception as exc:
                logger.debug("orphan_recovery state_emit_failed call=%s err=%s", call_id[:12], exc)
        recovered += 1
        logger.info(
            "orphan_recovery reclaimed call=%s prior_owner=%s tenant=%s",
            call_id[:12], entry.get("pod_id"), entry.get("tenant_id") or "-",
        )
    return recovered


def _pipeline_done_cb(task: asyncio.Task, call_id: str) -> None:
    """
    FIX 3 — Done-callback attached to Asterisk pipeline tasks.

    If start_pipeline() raises an unhandled exception after being fire-and-forgot
    via create_task(), Python logs to stderr but the session stays in
    _telephony_sessions forever.  This callback detects the failure and triggers
    _on_call_ended so the session is cleaned up and the PBX hangs up the channel.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(
            "pipeline_task crashed for %s — triggering session teardown: %s",
            call_id[:12], exc,
        )
        # Flag the session so the outcome resolver classifies this as
        # CallOutcome.FAILED (rather than the default ANSWERED) when
        # _on_call_ended runs the call_service chain.
        try:
            vs = _state().get_voice_session(call_id)
            if vs is not None:
                vs._pipeline_failed = True
        except Exception:
            pass
        asyncio.create_task(_on_call_ended(call_id))


async def _on_ringing(call_id: str) -> None:
    """
    Fired when the Asterisk adapter has parked an outbound channel in its
    mixing bridge and is waiting for the callee to answer.

    Pre-creates the VoiceSession and fires STT + TTS WebSocket handshakes plus
    a fire-and-forget LLM HTTP/2 pool warm-up so that by the time the callee
    picks up, every provider connection is already hot.  Subsequent answer
    handling (in `_on_new_call`) just has to register the media gateway and
    start the pipeline — no blocking warmup sits on the user's critical path.

    All errors are swallowed: if ringing-phase warmup fails, `_on_new_call`
    detects the missing entry and falls back to the normal answer-phase
    warmup path so the call still works (just with the old ~2 s penalty).
    """
    if _bridge()._adapter is None or getattr(_bridge()._adapter, "name", "") != "asterisk":
        return
    _sb = _state()
    if (
        _sb.has_ringing_warmup(call_id)
        or _sb.get_ringing_event(call_id) is not None
        or _sb.get_voice_session(call_id) is not None
    ):
        return  # idempotent/reserved — never create a second warmup for a call
    if _sb.voice_session_count() + _sb.ringing_warmup_count() >= _bridge()._MAX_TELEPHONY_SESSIONS:
        logger.warning(
            "ringing_warmup_skipped_at_capacity call_id=%s", call_id[:12],
        )
        return

    # Signal to _on_new_call that a ringing warmup is in progress.
    # This MUST be set before any await so the event is visible immediately
    # when the answer path checks for it (even if create_voice_session
    # takes ~1 s).
    evt = asyncio.Event()
    _sb.set_ringing_event(call_id, evt)

    _t0 = asyncio.get_event_loop().time()
    logger.info(f"WARMUP ringing_warmup_start {call_id[:12]}")
    try:
        orchestrator = _get_orchestrator()
        config = _build_telephony_session_config(gateway_type="telephony")
        voice_session = await orchestrator.create_voice_session(config)

        # STT + TTS: persistent per-call WebSockets.  We await these (via the
        # gathered task below) in `_on_new_call` so caller audio can flow into
        # an already-open socket on the first turn.
        warmup_coros = []
        _tts_connect = getattr(voice_session.tts_provider, "connect_for_call", None)
        if _tts_connect is not None:
            warmup_coros.append(_tts_connect(voice_session.call_id))
        if hasattr(voice_session.stt_provider, "pre_connect"):
            warmup_coros.append(
                voice_session.stt_provider.pre_connect(
                    voice_session.call_session.call_id
                )
            )
        connect_task: Optional[asyncio.Task] = None
        if warmup_coros:
            connect_task = asyncio.create_task(
                asyncio.gather(*warmup_coros, return_exceptions=True)
            )

        # LLM: tiny max_tokens=1 completion that seeds the httpx HTTP/2+TLS
        # pool.  Fire-and-forget — unlike the old answer-phase placement, the
        # ring window is long enough (>=1 s, typically 2–10 s) that the
        # bounded 1.5-s warmup is guaranteed to finish before the first real
        # turn's stream request, so there is no HTTP/2 stream contention.
        llm_warm = getattr(voice_session.llm_provider, "warm_up", None)
        if llm_warm is not None:
            asyncio.create_task(llm_warm())

        # ── Pre-synthesize greeting audio during the ring window ────────
        # After TTS connect completes, synthesize the greeting and buffer all
        # PCM chunks.  When the callee answers, _send_outbound_greeting pumps
        # these chunks directly into the media gateway — first audio arrives
        # within ~5ms instead of waiting 1–3s for real-time TTS synthesis.
        #
        # We create a combined task that:
        #   1. Awaits the TTS/STT connect handshakes
        #   2. Synthesizes the greeting and stores the audio on voice_session
        # This combined task replaces connect_task in _ringing_warmups.
        async def _warmup_and_presynth():
            """Await provider connections, then pre-synthesize greeting."""
            # Step 1: Wait for TTS + STT WebSocket handshakes
            if connect_task is not None:
                results = await connect_task
                if isinstance(results, list):
                    for i, r in enumerate(results):
                        if isinstance(r, Exception):
                            logger.warning(
                                "ringing_warmup_coro[%d] failed: %s", i, r,
                            )

            # Step 2: Build greeting text from session config
            greeting_text = _build_outbound_greeting(
                voice_session.call_session
            )

            # Step 3: Synthesize greeting and buffer all audio chunks
            chunks: list[bytes] = []
            _synth_t0 = asyncio.get_event_loop().time()
            try:
                tts_config = voice_session.config
                async for audio_chunk in voice_session.tts_provider.stream_synthesize(
                    text=greeting_text,
                    voice_id=tts_config.voice_id if tts_config else "default",
                    sample_rate=(
                        tts_config.tts_sample_rate if tts_config else 16000
                    ),
                    call_id=voice_session.call_id,
                ):
                    raw = (
                        audio_chunk.data
                        if hasattr(audio_chunk, "data")
                        else audio_chunk
                    )
                    if raw:
                        # Ensure Int16 alignment (2 bytes per sample)
                        if len(raw) % 2 != 0:
                            raw = raw[:-1]
                        if raw:
                            chunks.append(raw)

                _synth_ms = (asyncio.get_event_loop().time() - _synth_t0) * 1000.0
                total_bytes = sum(len(c) for c in chunks)
                logger.info(
                    "WARMUP greeting_presynth_done call_id=%s "
                    "chunks=%d bytes=%d synth_ms=%.0f",
                    call_id[:12], len(chunks), total_bytes, _synth_ms,
                )

                # Store on the voice_session so _send_outbound_greeting can
                # grab them without any dict lookup.
                voice_session._presynth_greeting_audio = chunks
                voice_session._presynth_greeting_text = greeting_text

            except Exception as synth_exc:
                logger.warning(
                    "WARMUP greeting_presynth_failed call_id=%s: %s",
                    call_id[:12], synth_exc,
                )
                # Pre-synth failure is non-fatal — _send_outbound_greeting
                # will fall back to real-time TTS.

        combined_task = asyncio.create_task(_warmup_and_presynth())

        _sb = _state()
        _sb.set_ringing_warmup(call_id, voice_session, combined_task)
        _sb.set_ringing_started_at(call_id, asyncio.get_event_loop().time())
        elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000.0
        logger.info(
            "WARMUP ringing_warmup_ready call_id=%s warmups=%d setup_ms=%.0f",
            call_id[:12], len(warmup_coros), elapsed_ms,
        )
    except Exception as exc:
        logger.error(
            f"Ringing warmup failed for {call_id[:12]}: {exc}", exc_info=True
        )
        # Clean up partial state so `_on_new_call` takes the slow path.
        _pop_ringing_warmup(call_id)
    finally:
        # Always signal the event so _on_new_call never waits forever.
        evt.set()
        # Don't remove the event here — _on_new_call will clean it up.


async def _reject_overcap_call(call_id: str) -> None:
    """Shared teardown when a call is refused at the cap gate (per-pod
    or global). Frees any ringing-phase pre-warm so the STT/TTS
    WebSockets don't leak, then hangs the channel up so the caller
    doesn't hear silence."""
    ringing = _pop_ringing_warmup(call_id)
    if ringing is not None:
        ringing_session, ringing_connect_task = ringing
        if ringing_connect_task is not None and not ringing_connect_task.done():
            ringing_connect_task.cancel()
        try:
            await _get_orchestrator().end_session(ringing_session)
        except Exception:
            pass
    if _bridge()._adapter:
        try:
            await _bridge()._adapter.hangup(call_id)
        except Exception:
            pass


async def _on_new_call(call_id: str) -> None:
    """Initialize AI pipeline when a new SIP call arrives."""
    # Track B (live call transparency): the remote side just picked up.
    # Emit ANSWERED so the live-calls panel flips from "ringing" to a
    # green "in-call" pill. Pre-pipeline start so the UI updates fast.
    # Best-effort — never block call setup on a status emit.
    try:
        from app.domain.services.call_status import (
            CallState, record_call_state_by_provider_id,
        )
        from app.core.container import get_container as _gc
        _c = _gc()
        await record_call_state_by_provider_id(
            _c.db_pool,
            provider_call_id=call_id,
            new_state=CallState.ANSWERED,
            metadata={"description": "Call answered"},
        )
    except Exception as exc:
        logger.debug("call_status.answered_emit_raised call=%s err=%s", call_id[:12], exc)

    # Per-pod cap (existing, kept as a backstop so a single pod never
    # exceeds its MAX_TELEPHONY_SESSIONS memory budget). The global cap
    # below is the new cluster-wide check (T1.2).
    _pod_session_count = _state().voice_session_count()
    if _pod_session_count >= _bridge()._MAX_TELEPHONY_SESSIONS:
        logger.error(
            "telephony_at_pod_capacity sessions=%d call_id=%s — rejecting",
            _pod_session_count, call_id[:12],
        )
        await _reject_overcap_call(call_id)
        return

    # T1.2 — cluster-wide concurrency cap. Redis-backed lease keyed on
    # call_id. Idempotent — safe to call on every _on_new_call for the
    # same id.  Refuses when the cluster SCARD exceeds the global cap.
    # Falls through to allow when Redis is unavailable so a degraded
    # Redis doesn't kill origination — the per-pod cap above is the
    # backstop.
    from app.domain.services.global_concurrency import (
        acquire_lease,
        resolve_global_cap,
    )
    from app.core.container import get_container
    container = get_container()
    redis_client = getattr(container, "redis", None)
    lease = await acquire_lease(
        redis_client,
        call_id=call_id,
        pod_id=os.getenv("POD_ID") or os.uname().nodename,
        cap=resolve_global_cap(),
    )
    if not lease:
        logger.error(
            "telephony_at_global_capacity call_id=%s current=%s — rejecting",
            call_id[:12], lease.current,
        )
        await _reject_overcap_call(call_id)
        return

    _new_call_t0 = asyncio.get_event_loop().time()
    _sb = _state()
    logger.info(f"BRIDGE new_call {call_id[:12]} (ringing_warmup_available={_sb.has_ringing_warmup(call_id)})")
    try:
        orchestrator = _get_orchestrator()

        # Select the correct media gateway based on the active PBX adapter:
        #   - Asterisk path: TelephonyMediaGateway (HTTP callbacks, no WebSocket)
        #   - FreeSWITCH path: BrowserMediaGateway (mod_audio_fork WebSocket)
        is_asterisk = bool(_bridge()._adapter and _bridge()._adapter.name == "asterisk")
        gateway_type = "telephony" if is_asterisk else "browser"

        # ── Fast path: consume the session pre-warmed in _on_ringing ─────
        # For Asterisk outbound calls, _on_ringing created the VoiceSession
        # and fired STT/TTS/LLM handshakes while the callee was still hearing
        # the ring tone.  At this point the WebSockets are already open and
        # the httpx HTTP/2 pool is warm, so we skip the answer-phase warmup
        # gather entirely.  For inbound / FreeSWITCH / ringing-failed calls
        # `pre` is None and we fall through to the slow path below.
        #
        # Event-based coordination: when preemptive Up fires, _on_ringing
        # and _on_outbound_answered run as concurrent tasks.  _on_ringing
        # takes ~1s (create_voice_session + provider init) while the answer
        # ARI setup takes ~7ms.  Instead of polling, we await an
        # asyncio.Event that _on_ringing sets when its warmup completes.
        pre = _pop_ringing_warmup(call_id)
        if pre is None and is_asterisk:
            ringing_evt = _sb.get_ringing_event(call_id)
            if ringing_evt is not None:
                logger.info(
                    "BRIDGE waiting_for_ringing_warmup call_id=%s",
                    call_id[:12],
                )
                try:
                    await asyncio.wait_for(ringing_evt.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "BRIDGE ringing_warmup_timeout call_id=%s — "
                        "falling back to answer-path warmup",
                        call_id[:12],
                    )
                pre = _pop_ringing_warmup(call_id)
                if pre is not None:
                    _wait_ms = (asyncio.get_event_loop().time() - _new_call_t0) * 1000.0
                    logger.info(
                        "BRIDGE ringing_warmup_consumed call_id=%s wait_ms=%.0f",
                        call_id[:12], _wait_ms,
                    )
            _sb.pop_ringing_event(call_id)  # clean up event
        connect_task: Optional[asyncio.Task] = None
        if pre is not None:
            voice_session, connect_task = pre  # type: ignore[assignment]
        else:
            config = _build_telephony_session_config(gateway_type=gateway_type)
            voice_session = await orchestrator.create_voice_session(config)

        _sb.set_voice_session(call_id, voice_session)

        # ── Bind dialer calls.id for campaign transcript persist ────────
        # Non-destructive: stashes _dialer_call_id on voice_session without
        # touching voice_session.call_id (STT/TTS connection maps are keyed
        # on that). Logs and returns None for non-campaign/test calls.
        try:
            from app.core.container import get_container as _gc
            _c = _gc()
            if _c.is_initialized:
                await bind_telephony_call(
                    voice_session=voice_session,
                    pbx_channel_id=call_id,
                    db_client=_c.db_client,
                )
        except Exception as _bind_exc:
            logger.debug(f"bind_telephony_call wrapper: {_bind_exc}")

        # ── Register media gateway BEFORE any further awaiting ──────────
        # The C++ gateway session was started in AsteriskAdapter._on_outbound_answered
        # and is already POSTing caller audio to /api/v1/sip/telephony/audio/{id}
        # within ~40-100 ms of callee answering.  If media_gateway.on_call_started()
        # is deferred, those early audio callbacks are silently dropped at
        # TelephonyMediaGateway.on_audio_received (session-not-registered
        # early return) — so a callee who says "Hello?" right after picking
        # up has their opening utterance completely lost.  Registering the
        # gateway first lets input_queue buffer the audio; the pipeline
        # drains it as soon as it starts.
        if is_asterisk:
            gateway_session_id = getattr(_bridge()._adapter, "_gateway_sessions", {}).get(call_id)
            if gateway_session_id:
                _sb.set_call_id_for_gateway_session(gateway_session_id, call_id)

            await voice_session.media_gateway.on_call_started(
                voice_session.call_id,
                {"adapter": _bridge()._adapter, "pbx_call_id": call_id},
            )

            # ── Caller-first 2-second greeting timer (parallel with setup) ──
            # The pre-synthesized greeting audio was prepared during the
            # ringing phase (prepare_pre_originate_greeting). The media
            # gateway is now registered, which is the only thing the
            # audio pump needs — pipeline_task / connect_task / etc. can
            # start in parallel.
            #
            # Anchoring the sleep on `_new_call_t0` AND spawning the task
            # here (instead of after pipeline_task creation) ensures the
            # greeting fires at exactly t=2.0s from answer, regardless of
            # any variance in downstream setup. The previous late-spawn
            # gave 2s + setup_time perceived delay, with jitter equal to
            # the variance in setup duration.
            _early_first_speaker = resolve_first_speaker(voice_session)
            if _early_first_speaker == "user":
                async def _delayed_greeting_early(_vs=voice_session, _t0=_new_call_t0):
                    sess = _vs.call_session
                    sess.llm_active = True
                    try:
                        try:
                            elapsed = asyncio.get_event_loop().time() - _t0
                            remaining = max(0.0, 2.0 - elapsed)
                            logger.info(
                                "caller_first_greeting_armed call=%s elapsed_ms=%.0f sleep_ms=%.0f",
                                _vs.call_id[:12], elapsed * 1000.0, remaining * 1000.0,
                            )
                            await asyncio.sleep(remaining)
                        except asyncio.CancelledError:
                            return
                        try:
                            sess.current_user_input = ""
                        except AttributeError:
                            pass
                        barge_ev = getattr(sess, "barge_in_event", None)
                        if barge_ev is not None:
                            barge_ev.clear()
                        elapsed_at_fire = (
                            asyncio.get_event_loop().time() - _t0
                        ) * 1000.0
                        logger.info(
                            "caller_first_greeting_fire call=%s elapsed_ms=%.0f",
                            _vs.call_id[:12], elapsed_at_fire,
                        )
                    finally:
                        sess.llm_active = False
                    await _send_outbound_greeting(_vs)

                voice_session._greeting_task = asyncio.create_task(
                    _delayed_greeting_early()
                )

            # ── Drain early audio buffer ────────────────────────────────
            # Audio from the C++ gateway arrives within ~40ms of callee
            # answering, but _on_new_call runs as create_task and hasn't
            # populated _gateway_session_to_call_id yet.  receive_gateway_audio
            # buffers those orphan chunks.  Now that the media gateway is
            # registered, replay them so Flux sees the callee's first words.
            if gateway_session_id:
                early_chunks = _sb.drain_early_audio(gateway_session_id)
                if early_chunks:
                    logger.info(
                        "early_audio_drain call_id=%s chunks=%d — "
                        "replaying callee audio that arrived before session registration",
                        call_id[:12], len(early_chunks),
                    )
                    for chunk in early_chunks:
                        try:
                            await voice_session.media_gateway.on_audio_received(
                                voice_session.call_id, chunk
                            )
                        except Exception:
                            break  # gateway not ready — stop draining

        # ── Provider warmup ─────────────────────────────────────────────
        # Fast path (pre-warm succeeded): await the ringing-phase handshake
        # task with a short bound.  It should already be complete — the ring
        # window is at least 1 s and handshakes take ~200–600 ms — but we
        # cap the wait so a single stuck socket can't delay pipeline start.
        #
        # Slow path (no pre-warm): run STT + TTS handshakes in parallel now.
        # LLM warmup is EXCLUDED here (unlike the ringing path): on the slow
        # path there are only tens of ms before the first real LLM request,
        # and a concurrent warmup + stream on the same httpx HTTP/2
        # connection causes ~4 s of contention.  A cold LLM handshake adds
        # only ~100-200 ms, which is acceptable for the fallback path.
        if connect_task is not None:
            try:
                results = await asyncio.wait_for(connect_task, timeout=1.0)
                if isinstance(results, list):
                    for i, r in enumerate(results):
                        if isinstance(r, Exception):
                            logger.warning(
                                "telephony_ringing_warmup[%d] failed (non-fatal): %s",
                                i, r,
                            )
                _warmup_ms = (asyncio.get_event_loop().time() - _new_call_t0) * 1000.0
                logger.info(
                    "BRIDGE telephony_warmup_done call_id=%s source=ringing await_ms=%.0f",
                    call_id[:12], _warmup_ms,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "telephony_ringing_warmup_slow call_id=%s — providers will "
                    "complete handshake on first use", call_id[:12],
                )
        else:
            warmup_coros = []
            _tts_connect = getattr(voice_session.tts_provider, "connect_for_call", None)
            if _tts_connect is not None:
                warmup_coros.append(_tts_connect(voice_session.call_id))
            if hasattr(voice_session.stt_provider, "pre_connect"):
                warmup_coros.append(
                    voice_session.stt_provider.pre_connect(
                        voice_session.call_session.call_id
                    )
                )
            # LLM pool prewarm: ringing skipped this branch, so without it
            # the first user turn pays the cold-pool cost (~100-200ms TLS +
            # first-token). Runs concurrently with STT/TTS connects so it
            # adds zero wall-clock time on the slow path.
            from app.domain.services.telephony.modes.user_first import (
                prewarm_llm_pool,
            )
            warmup_coros.append(prewarm_llm_pool(voice_session))

            if warmup_coros:
                results = await asyncio.gather(*warmup_coros, return_exceptions=True)
                for i, r in enumerate(results):
                    if isinstance(r, Exception):
                        logger.warning("telephony_warmup[%d] failed (non-fatal): %s", i, r)
                _warmup_ms = (asyncio.get_event_loop().time() - _new_call_t0) * 1000.0
                logger.info(
                    "BRIDGE telephony_warmup_done call_id=%s source=answer warmups=%d warmup_ms=%.0f",
                    call_id[:12], len(warmup_coros), _warmup_ms,
                )

        if is_asterisk:
            # Start the voice pipeline (STT → LLM → TTS loop).  The media gateway
            # and gateway_session mapping were already registered above, so any
            # caller audio that arrived during warmup is waiting in input_queue
            # and will be drained into Flux immediately.
            voice_session.pipeline_task = asyncio.create_task(
                voice_session.pipeline.start_pipeline(voice_session.call_session, None)
            )
            # FIX 3 — attach done-callback so a crash inside start_pipeline triggers
            # _on_call_ended rather than leaving a silent dead session.
            voice_session.pipeline_task.add_done_callback(
                lambda t: _pipeline_done_cb(t, call_id)
            )
            _pipeline_start_ms = (asyncio.get_event_loop().time() - _new_call_t0) * 1000.0
            logger.info(
                "BRIDGE pipeline_started call_id=%s total_setup_ms=%.0f source=%s",
                call_id[:12], _pipeline_start_ms,
                "ringing" if pre is not None else "answer",
            )

            # Who speaks first on outbound?  Default is "agent" — the estimation
            # agent greets the callee immediately so they never hear dead silence.
            # Set TELEPHONY_FIRST_SPEAKER=user to wait for the callee to speak
            # first (useful for inbound-style testing).
            # In "user" mode we stay silent and let handle_turn_end react to the
            # callee's first utterance — avoids the AI talking over a "Hello?".
            # Prefer the per-call first_speaker stashed on the pre-warm session
            # (set by make_call's `first_speaker` query param) so each Start button
            # click wins over the global TELEPHONY_FIRST_SPEAKER env default.
            first_speaker = resolve_first_speaker(voice_session)
            # Track on the session so _on_call_ended can cancel the
            # greeting task on hangup. Without this, hanging up
            # mid-greeting leaves the task draining audio chunks into
            # a gateway session that has been torn down — visible as
            # "send_tts_audio: no gateway session" warning storms in
            # the logs and a small CPU/log leak per dropped call.
            #
            # Both modes follow the SAME greeting path. The only
            # difference: caller-first waits 2 seconds before speaking
            # so the callee has a moment to compose themselves before
            # the AI greets. Agent-first speaks immediately on pickup.
            if first_speaker == "agent":
                voice_session._greeting_task = asyncio.create_task(
                    _send_outbound_greeting(voice_session)
                )
            # caller-first ("user") greeting task was already spawned
            # right after media_gateway.on_call_started so its 2s timer
            # runs in parallel with pipeline setup — see the
            # `_delayed_greeting_early` block above.

        # Tell the adapter to start streaming audio.
        # For Asterisk this is a no-op (audio_callback_url handles it via C++ gateway).
        # For FreeSWITCH this triggers mod_audio_fork which connects the WebSocket,
        # which then triggers _on_ws_session_start to complete the FS pipeline setup.
        if _bridge()._adapter:
            await _bridge()._adapter.start_audio_stream(call_id)

        _total_init_ms = (asyncio.get_event_loop().time() - _new_call_t0) * 1000.0
        logger.info(
            "BRIDGE ai_pipeline_initialized call_id=%s total_init_ms=%.0f",
            call_id[:12], _total_init_ms,
        )
    except Exception as exc:
        logger.error(f"Failed to initialize AI pipeline for {call_id[:12]}: {exc}", exc_info=True)
        # GAP 3 — Error-path hangup: tell the PBX to release the channel so
        # the caller doesn't hear silence indefinitely.  Tear down the
        # half-initialised session (pre-warmed or otherwise) directly — the
        # PBX hangup will fire _on_call_ended, but end_session() is idempotent
        # and running it here guards against cases where the hangup path
        # silently drops the StasisEnd event.
        orphan = _state().pop_voice_session(call_id)
        if orphan is not None:
            try:
                await _get_orchestrator().end_session(orphan)
            except Exception:
                pass
        if _bridge()._adapter:
            try:
                await _bridge()._adapter.hangup(call_id)
            except Exception:
                pass


async def _on_audio_received(call_id: str, audio_bytes: bytes) -> None:
    """Route incoming audio from the PBX into the media gateway (STT input)."""
    # Hot path (per RTP packet). get_voice_session is a process-local dict
    # read in both backends — Redis is never touched here.
    sb = _state()
    voice_session = sb.get_voice_session(call_id)
    if not voice_session:
        return
    # Refresh the Redis ledger TTL so a long call's entry never expires
    # underneath it. Debounced inside the backend (>=30s), so calling it
    # on every audio frame is cheap — a dict lookup, no Redis per packet.
    sb.touch_call(call_id)
    try:
        await voice_session.media_gateway.on_audio_received(
            voice_session.call_id, audio_bytes
        )
    except Exception as exc:
        logger.debug(f"Audio route error {call_id[:12]}: {exc}")


async def _on_call_ended(call_id: str) -> None:
    """Clean up voice session when the call hangs up."""
    logger.info(f"Telephony bridge: call ended {call_id[:12]}")

    # Track B (live call transparency): mark the call ENDED in calls.status
    # and emit a stream_events row so the live-calls panel removes it from
    # the in-flight list and shows the final outcome. Best-effort — never
    # block teardown on a status emit.
    try:
        from app.domain.services.call_status import (
            CallState, record_call_state_by_provider_id,
        )
        from app.core.container import get_container as _gc
        _c = _gc()
        await record_call_state_by_provider_id(
            _c.db_pool,
            provider_call_id=call_id,
            new_state=CallState.ENDED,
            metadata={"description": "Call ended"},
        )
    except Exception as exc:
        logger.debug("call_status.ended_emit_raised call=%s err=%s", call_id[:12], exc)

    # T1.2 — release the global concurrency lease FIRST, even if the
    # rest of the teardown fails. The lease TTL would reap it in 10
    # minutes regardless, but releasing eagerly keeps the cluster count
    # accurate so the next caller isn't falsely rejected.
    try:
        from app.domain.services.global_concurrency import release_lease
        from app.core.container import get_container as _gc
        _c = _gc()
        await release_lease(
            getattr(_c, "redis", None) if _c.is_initialized else None,
            call_id=call_id,
        )
    except Exception as exc:
        logger.debug("global_concurrency_release_raised call=%s err=%s", call_id[:12], exc)

    # Abandoned-ring path: if the callee never answered, the session was
    # pre-warmed during the ring but never promoted into _telephony_sessions.
    # Tear it down here so the STT/TTS WebSockets opened in _on_ringing
    # don't leak.  AsteriskAdapter._cleanup_pending_outbound fires this
    # callback when StasisEnd/ChannelDestroyed arrives for a _pending_outbound
    # channel.
    ringing = _pop_ringing_warmup(call_id)
    if ringing is not None:
        ringing_session, ringing_connect_task = ringing
        if ringing_connect_task is not None and not ringing_connect_task.done():
            ringing_connect_task.cancel()
        try:
            await _get_orchestrator().end_session(ringing_session)
        except Exception as exc:
            logger.debug(f"Ringing session end_session failed for {call_id[:12]}: {exc}")

    voice_session = _state().pop_voice_session(call_id)
    if voice_session:
        # Cancel any per-call task that's still running. Without this,
        # tasks spawned during the call (silence handler, greeting,
        # presynth warm-ups) keep firing into a torn-down gateway and
        # produce log storms or zombie work for the rest of their
        # natural lifetime. Pattern follows Pipecat's session-cleanup
        # contract: hangup is authoritative, all per-session work cancels.
        for _attr in (
            "_greeting_task",
        ):
            _t = getattr(voice_session, _attr, None)
            if _t is not None and not _t.done():
                _t.cancel()

        # --- Persist transcript + terminal metrics to dialer's calls row ---
        # Two writes that have to happen before teardown:
        #   1. Transcript text/json (read from in-memory buffer keyed on
        #      voice_session.call_id; persisted by uuid).
        #   2. Terminal metrics (status='completed', ended_at, duration_seconds).
        #      Without #2 the dashboard's minutes-used SQL — which sums
        #      `duration_seconds` for completed/answered calls in the current
        #      month — returns zero forever and minutes_remaining never
        #      decrements regardless of activity.
        # Both run inside try/except blocks so a transient DB issue on one
        # never blocks the other or torpedoes the rest of teardown.
        try:
            pipeline = getattr(voice_session, "pipeline", None)
            transcript_service = getattr(pipeline, "transcript_service", None)
            from app.core.container import get_container as _gc
            _c = _gc()
            _pool = _c.db_pool if _c.is_initialized else None
            if transcript_service is not None:
                await save_call_transcript_on_hangup(
                    voice_session=voice_session,
                    transcript_service=transcript_service,
                    db_pool=_pool,
                )
        except Exception as tx_err:
            logger.warning(f"Transcript persist failed for {call_id[:12]}: {tx_err}")

        # ----- Resolve real outcome + drive call_service.handle_call_status -----
        # The call_service chain does (atomically when the RPC is available):
        #   * UPDATE calls SET status='completed', outcome=<enum>, ended_at, duration_seconds
        #   * UPDATE leads SET status, last_call_result, last_called_at, call_attempts++
        #   * RPC increment_campaign_counter(...) to bump calls_completed | calls_failed
        #   * UPDATE dialer_jobs (retry vs. terminal)
        # Previously this branch wrote outcome="completed" via the now-removed
        # save_call_metrics_on_hangup helper, which silently bypassed the
        # counter + lead + dialer-job updates and left the dashboard's
        # progress_pct / success_rate_pct stuck at zero. resolve_call_outcome
        # classifies the call from live voice_session state and the optional
        # adapter cause code; handle_call_status owns the rest.
        try:
            dialer_call_id = getattr(voice_session, "_dialer_call_id", None)
            if dialer_call_id:
                from app.core.container import get_container as _gc2
                _c2 = _gc2()
                if _c2.is_initialized:
                    outcome = resolve_call_outcome(
                        voice_session,
                        hangup_reason=getattr(voice_session, "_hangup_reason", None),
                    )
                    duration = int(
                        getattr(
                            getattr(voice_session, "call_session", None),
                            "get_duration_seconds",
                            lambda: 0,
                        )()
                    )
                    # The hangup hook runs without a request-scoped JWT,
                    # so the postgres adapter's tenant context is empty
                    # and every RPC (update_call_status, increment_
                    # campaign_counter, dialer_jobs UPDATE) would be
                    # filtered to zero rows by the RLS policies. Set
                    # bypass_rls for the duration of this teardown so
                    # the writes actually land. The contextvar is
                    # process-scoped to this asyncio task so we don't
                    # leak the bypass to anyone else.
                    from app.core.security.tenant_isolation import (
                        set_bypass_rls,
                        set_current_tenant_id,
                    )
                    set_bypass_rls(True)
                    tenant_for_call = getattr(
                        voice_session, "_dialer_tenant_id", None,
                    )
                    if tenant_for_call:
                        set_current_tenant_id(tenant_for_call)

                    call_service = CallService(
                        db_client=_c2.db_client,
                        queue_service=getattr(_c2, "_queue_service", None),
                    )
                    await call_service.handle_call_status(
                        call_uuid=dialer_call_id,
                        outcome=outcome,
                        duration=duration,
                    )
                    logger.info(
                        "call_outcome_persisted call_id=%s outcome=%s duration_s=%d",
                        call_id[:12], outcome.value, duration,
                    )
        except Exception as m_err:
            logger.warning(
                "call_outcome_persist_failed call_id=%s err=%s", call_id[:12], m_err,
            )

        # --- Save recording BEFORE session teardown ---
        try:
            await _save_call_recording(voice_session, call_id)
        except Exception as rec_err:
            logger.warning(f"Recording save failed for {call_id[:12]}: {rec_err}")

        try:
            await _get_orchestrator().end_session(voice_session)
        except Exception:
            pass
    # Clean up gateway session mapping and early audio buffer for this call.
    _state().remove_gateway_sessions_for_call(call_id)


async def _on_ws_session_start(call_id: str) -> None:
    """
    Called when FreeSWITCH mod_audio_fork WebSocket connects.
    Wires the bridge WebSocket into the media gateway and starts the pipeline.
    """
    from app.infrastructure.telephony.freeswitch_audio_bridge import get_audio_bridge

    _sb = _state()
    voice_session = _sb.get_voice_session(call_id)
    if not voice_session:
        # GAP 4 — Race: mod_audio_fork WebSocket can connect before _on_new_call
        # stores the session (especially under server load).  Poll for up to 2s
        # (40 × 50ms) before giving up — was 1s (20 × 50ms).
        for _ in range(40):
            await asyncio.sleep(0.05)
            voice_session = _sb.get_voice_session(call_id)
            if voice_session:
                break

    if not voice_session:
        logger.error("FS WebSocket session race timeout — hanging up call %s", call_id[:12])
        if _bridge()._adapter:
            try:
                await _bridge()._adapter.hangup(call_id)
            except Exception:
                pass
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
                    # FIX 3 — trigger session teardown so the session doesn't leak
                    # and the PBX hangs up the channel instead of staying connected.
                    await _on_call_ended(call_id)

            voice_session.pipeline_task = asyncio.create_task(_run())
            logger.info(f"Voice pipeline started for {call_id[:12]}")
    except Exception as exc:
        logger.error(f"WS session start error: {exc}", exc_info=True)
