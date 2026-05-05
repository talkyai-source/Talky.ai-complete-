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
from app.domain.services.telephony.modes.user_first import (
    _handle_user_first_silence,
    _user_first_fallback_enabled,
)
from app.domain.services.telephony.recording import _save_call_recording
from app.services.scripts import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)

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


# Watchdog timeouts (read once at import time — match prior bridge behaviour).
_SESSION_INACTIVITY_TIMEOUT_S = int(os.getenv("TELEPHONY_INACTIVITY_TIMEOUT_S", "300"))
_SESSION_MAX_DURATION_S = int(os.getenv("TELEPHONY_MAX_CALL_DURATION_S", "3600"))


def _pop_ringing_warmup(call_id: str):
    """
    Atomically pop a ringing-phase warmup entry and its parallel timestamp.

    Returns the (VoiceSession, connect_task) tuple if present, else None.
    Callers are responsible for cancelling the task and ending the session.
    """
    _bridge()._ringing_warmup_created_at.pop(call_id, None)
    return _bridge()._ringing_warmups.pop(call_id, None)


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

            # ----- Active session inactivity sweep -----
            stale = []
            for call_id, vs in list(_bridge()._telephony_sessions.items()):
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
                cid for cid, created_at in list(_bridge()._ringing_warmup_created_at.items())
                if (now - created_at) > _bridge()._RINGING_MAX_AGE_S
            ]
            for cid in stale_ringing:
                ringing = _pop_ringing_warmup(cid)
                _bridge()._ringing_events.pop(cid, None)
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
            stale_events = [
                cid for cid in list(_bridge()._ringing_events.keys())
                if cid not in _bridge()._ringing_warmup_created_at
                and cid not in _bridge()._telephony_sessions
            ]
            for cid in stale_events:
                _bridge()._ringing_events.pop(cid, None)

            # ----- Phase 1.3: orphan sweep across remaining session-keyed maps
            # Anything keyed by gateway_session_id whose call_id is no longer
            # an active or ringing-warming session is leakage from a crashed
            # call path that never called _on_call_ended. Drop on the same age
            # policy as ringing warmups.
            active_call_ids = set(_bridge()._telephony_sessions.keys()) | set(
                _bridge()._ringing_warmup_created_at.keys()
            )
            orphan_gw = [
                gw_id for gw_id, cid in list(_bridge()._gateway_session_to_call_id.items())
                if cid not in active_call_ids
            ]
            for gw_id in orphan_gw:
                _bridge()._gateway_session_to_call_id.pop(gw_id, None)
                buf = _bridge()._early_audio_buffers.pop(gw_id, None)
                if buf:
                    logger.warning(
                        "telephony_watchdog: dropping orphan early_audio_buffer "
                        "gateway_session_id=%s chunks=%d",
                        gw_id, len(buf),
                    )

            # Buffers that exist without any gateway-session mapping at all are
            # dead audio from calls that never registered. Cap their age too.
            for gw_id in list(_bridge()._early_audio_buffers.keys()):
                if gw_id not in _bridge()._gateway_session_to_call_id:
                    _bridge()._early_audio_buffers.pop(gw_id, None)

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
                    for vs in list(_bridge()._telephony_sessions.values()):
                        tts = getattr(vs, "tts_provider", None)
                        if tts is None or getattr(tts, "name", None) != "cartesia":
                            continue
                        cartesia_singletons.add(id(tts))
                        live_ids = {
                            getattr(v, "call_id", None)
                            for v in _bridge()._telephony_sessions.values()
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
                    for live_id in list(_bridge()._telephony_sessions.keys()):
                        await refresh_lease(_redis, call_id=live_id)
                    await reconcile_orphans(_redis)
            except Exception as exc:
                logger.debug("global_concurrency_watchdog_step_failed err=%s", exc)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("telephony_watchdog error: %s", exc)


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
    if (
        call_id in _bridge()._ringing_warmups
        or call_id in _bridge()._ringing_events
        or call_id in _bridge()._telephony_sessions
    ):
        return  # idempotent/reserved — never create a second warmup for a call
    if len(_bridge()._telephony_sessions) + len(_bridge()._ringing_warmups) >= _bridge()._MAX_TELEPHONY_SESSIONS:
        logger.warning(
            "ringing_warmup_skipped_at_capacity call_id=%s", call_id[:12],
        )
        return

    # Signal to _on_new_call that a ringing warmup is in progress.
    # This MUST be set before any await so the event is visible immediately
    # when the answer path checks for it (even if create_voice_session
    # takes ~1 s).
    evt = asyncio.Event()
    _bridge()._ringing_events[call_id] = evt

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

        _bridge()._ringing_warmups[call_id] = (voice_session, combined_task)
        _bridge()._ringing_warmup_created_at[call_id] = asyncio.get_event_loop().time()
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
    # Per-pod cap (existing, kept as a backstop so a single pod never
    # exceeds its MAX_TELEPHONY_SESSIONS memory budget). The global cap
    # below is the new cluster-wide check (T1.2).
    if len(_bridge()._telephony_sessions) >= _bridge()._MAX_TELEPHONY_SESSIONS:
        logger.error(
            "telephony_at_pod_capacity sessions=%d call_id=%s — rejecting",
            len(_bridge()._telephony_sessions), call_id[:12],
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
    logger.info(f"BRIDGE new_call {call_id[:12]} (ringing_warmup_available={call_id in _bridge()._ringing_warmups})")
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
            ringing_evt = _bridge()._ringing_events.get(call_id)
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
            _bridge()._ringing_events.pop(call_id, None)  # clean up event
        connect_task: Optional[asyncio.Task] = None
        if pre is not None:
            voice_session, connect_task = pre  # type: ignore[assignment]
        else:
            config = _build_telephony_session_config(gateway_type=gateway_type)
            voice_session = await orchestrator.create_voice_session(config)

        _bridge()._telephony_sessions[call_id] = voice_session

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
                _bridge()._gateway_session_to_call_id[gateway_session_id] = call_id

            await voice_session.media_gateway.on_call_started(
                voice_session.call_id,
                {"adapter": _bridge()._adapter, "pbx_call_id": call_id},
            )

            # ── Drain early audio buffer ────────────────────────────────
            # Audio from the C++ gateway arrives within ~40ms of callee
            # answering, but _on_new_call runs as create_task and hasn't
            # populated _gateway_session_to_call_id yet.  receive_gateway_audio
            # buffers those orphan chunks.  Now that the media gateway is
            # registered, replay them so Flux sees the callee's first words.
            if gateway_session_id:
                early_chunks = _bridge()._early_audio_buffers.pop(gateway_session_id, None)
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
            if first_speaker == "agent":
                # Track on the session so _on_call_ended can cancel the
                # greeting task on hangup. Without this, hanging up
                # mid-greeting leaves the task draining audio chunks into
                # a gateway session that has been torn down — visible as
                # "send_tts_audio: no gateway session" warning storms in
                # the logs and a small CPU/log leak per dropped call.
                voice_session._greeting_task = asyncio.create_task(
                    _send_outbound_greeting(voice_session)
                )
            else:
                logger.info(
                    "outbound_user_first call_id=%s — Flux listening, "
                    "AI is SILENT, waiting for callee to speak",
                    call_id[:12],
                )
                # User-speaks-first mode: the AI stays COMPLETELY SILENT.
                # Flux is already connected and the pipeline is running
                # (started above), so the STT is actively listening.
                # When the callee says "Hello?", Flux emits EndOfTurn →
                # handle_turn_end fires → LLM responds naturally.
                #
                # Caller-speaks-first means the AI must remain silent until
                # Flux hears a real caller turn. The automatic opener is opt-in
                # only; otherwise it races normal pickup speech and causes the
                # exact first-interaction delay this mode is meant to avoid.
                if _user_first_fallback_enabled():
                    voice_session._user_first_silence_task = asyncio.create_task(
                        _handle_user_first_silence(voice_session, call_id)
                    )
                else:
                    logger.info(
                        "user_first_fallback_disabled call_id=%s — AI remains "
                        "silent until caller speech is transcribed",
                        call_id[:12],
                    )

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
        orphan = _bridge()._telephony_sessions.pop(call_id, None)
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
    voice_session = _bridge()._telephony_sessions.get(call_id)
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

    voice_session = _bridge()._telephony_sessions.pop(call_id, None)
    if voice_session:
        # Cancel any per-call task that's still running. Without this,
        # tasks spawned during the call (silence handler, greeting,
        # presynth warm-ups) keep firing into a torn-down gateway and
        # produce log storms or zombie work for the rest of their
        # natural lifetime. Pattern follows Pipecat's session-cleanup
        # contract: hangup is authoritative, all per-session work cancels.
        for _attr in (
            "_user_first_silence_task",
            "_greeting_task",
        ):
            _t = getattr(voice_session, _attr, None)
            if _t is not None and not _t.done():
                _t.cancel()

        # --- Persist transcript to dialer's calls row BEFORE teardown ----
        # Reads the in-memory TranscriptService buffer (keyed on the
        # session's original call_id) and writes to the dialer's calls.id
        # resolved at _on_new_call. Never raises.
        try:
            pipeline = getattr(voice_session, "pipeline", None)
            transcript_service = getattr(pipeline, "transcript_service", None)
            if transcript_service is not None:
                from app.core.container import get_container as _gc
                _c = _gc()
                _pool = _c.db_pool if _c.is_initialized else None
                await save_call_transcript_on_hangup(
                    voice_session=voice_session,
                    transcript_service=transcript_service,
                    db_pool=_pool,
                )
        except Exception as tx_err:
            logger.warning(f"Transcript persist failed for {call_id[:12]}: {tx_err}")

        # --- Save recording BEFORE session teardown ---
        try:
            await _save_call_recording(voice_session, call_id)
        except Exception as rec_err:
            logger.warning(f"Recording save failed for {call_id[:12]}: {rec_err}")

        try:
            await _get_orchestrator().end_session(voice_session)
        except Exception:
            pass
    # Clean up gateway session mapping and early audio buffer
    keys_to_remove = [k for k, v in _bridge()._gateway_session_to_call_id.items() if v == call_id]
    for k in keys_to_remove:
        _bridge()._gateway_session_to_call_id.pop(k, None)
        _bridge()._early_audio_buffers.pop(k, None)


async def _on_ws_session_start(call_id: str) -> None:
    """
    Called when FreeSWITCH mod_audio_fork WebSocket connects.
    Wires the bridge WebSocket into the media gateway and starts the pipeline.
    """
    from app.infrastructure.telephony.freeswitch_audio_bridge import get_audio_bridge

    voice_session = _bridge()._telephony_sessions.get(call_id)
    if not voice_session:
        # GAP 4 — Race: mod_audio_fork WebSocket can connect before _on_new_call
        # stores the session (especially under server load).  Poll for up to 2s
        # (40 × 50ms) before giving up — was 1s (20 × 50ms).
        for _ in range(40):
            await asyncio.sleep(0.05)
            voice_session = _bridge()._telephony_sessions.get(call_id)
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
