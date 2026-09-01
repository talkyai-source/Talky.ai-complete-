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
remain the canonical owner of those names.

Per-call state (voice sessions, ringing warmups, gateway-session map,
etc.) is reached through ``_state()`` / ``get_state_backend()`` below.
The live PBX adapter — not per-call state, a single live ARI/ESL
connection object — is reached through
``app.domain.services.telephony.adapter_registry.get_adapter()``:
``telephony_bridge.py`` registers a getter closure over its own
``_adapter`` global at import time, so this module never has to import
the API layer to read it (that used to be done via a lazy-imported
``_bridge()`` helper — an API→domain dependency pointing the wrong way).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

# Imports from already-extracted telephony submodules
from app.domain.services.telephony.config import (
    _build_telephony_session_config,
    _build_outbound_greeting,
    _MAX_TELEPHONY_SESSIONS,
    _RINGING_MAX_AGE_S,
)
from app.domain.services.telephony.inbound_overrides import apply_qualification_overrides
from app.domain.services.telephony.adapter_registry import get_adapter
from app.domain.services.telephony.modes import resolve_first_speaker
from app.domain.services.telephony.modes.agent_first import _send_outbound_greeting
from app.domain.services.telephony.recording import _save_call_recording
from app.services.scripts import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)
from app.domain.services.call_service import CallService
from app.domain.services.telephony.outcome_resolver import resolve_call_outcome
from app.infrastructure.metrics.gateway_metrics import record_gateway_media_reconciliation

logger = logging.getLogger(__name__)


class InboundTerminalProofMissing(RuntimeError):
    """A confirmed Answer has no authoritative PBX-absence timestamp."""


def _state():
    """The telephony state backend (Phase 1, item 1 of the architecture
    roadmap). All per-call state reads/writes go through this so the
    Redis-backed backend can mirror them for restart recovery. Lazy
    import keeps the module-load graph acyclic. ``_adapter`` is NOT
    state-backend-managed (it's a live ARI/ESL connection); it's reached
    via ``adapter_registry.get_adapter()`` instead (see module docstring).
    """
    from app.domain.services.telephony.state_backend import get_state_backend

    return get_state_backend()


# Watchdog timeouts (read once at import time — match prior bridge behaviour).
_SESSION_INACTIVITY_TIMEOUT_S = int(os.getenv("TELEPHONY_INACTIVITY_TIMEOUT_S", "300"))
# Absolute hard ceiling — a call is force-ended past this no matter what
# (backstop against a wedged pipeline, or a call that keeps signalling "closing"
# forever). The *soft* cap below (5 min) is the normal target; this ceiling is
# generous — 10 min — so a genuine deal-closing conversation that legitimately
# runs past the soft cap is never cut short, only true runaways are caught.
_SESSION_MAX_DURATION_S = int(os.getenv("TELEPHONY_MAX_CALL_DURATION_S", "600"))
# Soft cap: the agent is given this long (5 min by default) to reach a
# conclusion. Past it the call is wrapped up UNLESS the agent is actively
# closing the deal (see `_session_is_closing`), in which case it may run on to
# the hard ceiling above. Set 0 to disable the soft cap (hard ceiling only).
_SESSION_SOFT_CAP_S = int(os.getenv("TELEPHONY_SOFT_CALL_CAP_S", "300"))

# P1-9 — module-level set retaining references to fire-and-forget forced-
# hangup tasks. asyncio only holds a *weak* reference to tasks spawned via
# create_task(); with nothing else referencing them, the task object can be
# garbage-collected mid-execution (CPython schedules on the next GC pass),
# silently dropping the forced teardown/hangup on crashed-pipeline and
# audio-route-failure paths. Mirrors the _track_task pattern in
# app/infrastructure/telephony/freeswitch_audio_bridge.py:82-90.
_background_tasks: set = set()


def _track_task(coro) -> "asyncio.Task":
    """Spawn coro as a task whose reference is retained until completion.

    Adds the task to the module-level ``_background_tasks`` set so it can't
    be garbage-collected while still running, and logs (rather than
    swallows) any exception it raises.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(t: "asyncio.Task") -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error("lifecycle background task failed: %s", t.exception())

    task.add_done_callback(_done)
    return task


def _realtime_fallback_enabled() -> bool:
    """Fix 14 config gate. When a realtime speech-to-speech socket drops
    mid-call, fall back to the cascaded pipeline instead of dying into dead
    air. Default ON; set REALTIME_FALLBACK_ENABLED=false to keep today's
    behaviour (the bridge ends and the inactivity watchdog eventually reaps
    the call). Read per-call so an operator can flip it without a restart."""
    return os.getenv("REALTIME_FALLBACK_ENABLED", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


async def _on_realtime_connection_lost(call_id: str, voice_session) -> None:
    """Fix 14 recovery: the realtime session's websocket dropped mid-call.
    Rebuild the cascaded pipeline on the SAME live media gateway and swap it in
    so the caller keeps talking to the agent instead of hitting dead air.

    Invoked (at most once per call) by RealtimeBridge.run() via its
    on_connection_lost hook. Guards:
      * config gate REALTIME_FALLBACK_ENABLED (also checked at wire time);
      * once-per-call (``_realtime_fallback_attempted`` on the session);
      * the call/session must still be live (present in the state backend).
    If the cascaded rebuild itself fails we log and force-end the call — the
    same terminal outcome as today, never worse.

    Double-teardown safety: the swap replaces ``voice_session.pipeline_task``
    with the new cascaded task BEFORE the dying realtime task's done-callback
    fires. ``_pipeline_done_cb`` ignores a superseded task (and the realtime
    task completes without an exception anyway), so it never force-ends the
    call out from under the fallback.
    """
    if not _realtime_fallback_enabled():
        return
    if voice_session is None:
        return
    if getattr(voice_session, "_realtime_fallback_attempted", False):
        logger.debug(
            "realtime_fallback_skip call=%s — already attempted once",
            call_id[:12],
        )
        return
    voice_session._realtime_fallback_attempted = True

    # Only recover a call that's still alive. A hangup that raced the socket
    # drop already removed the session (and is tearing down) — nothing to do.
    if _state().get_voice_session(call_id) is None:
        logger.info(
            "realtime_fallback_skip call=%s — session already gone",
            call_id[:12],
        )
        return

    try:
        new_task = await _get_orchestrator().start_cascaded_fallback(voice_session)
    except Exception as exc:  # noqa: BLE001 — defensive; builder already fail-soft
        logger.error(
            "realtime_fallback_build_raised call=%s: %s — ending call",
            call_id[:12],
            exc,
        )
        new_task = None

    if new_task is None:
        logger.error(
            "realtime_fallback_failed call=%s — cascaded rebuild produced no "
            "pipeline; ending call (same as today)",
            call_id[:12],
        )
        _track_task(_force_end_and_hangup(call_id))
        return

    # Swap the live task reference, then attach the done-callback so a later
    # crash of the cascaded pipeline still tears the call down.
    voice_session.pipeline_task = new_task
    new_task.add_done_callback(lambda t: _pipeline_done_cb(t, call_id))
    logger.warning(
        "realtime_fallback_active call=%s — cascaded pipeline swapped in after "
        "realtime socket drop",
        call_id[:12],
    )


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

# Watchdog zombie-session reconcile state. Maps a local voice-session
# call_id → how many consecutive watchdog ticks it has been ABSENT from
# Asterisk's live channel list. A genuine live call appears every tick so it
# never accumulates; a session orphaned by a missed ChannelDestroyed event is
# absent every tick and trips the threshold, releasing its leaked slot.
_zombie_channel_ticks: dict[str, int] = {}
_ZOMBIE_TICK_THRESHOLD = 2  # ~60s at the 30s watchdog cadence

# channel_id → consecutive watchdog ticks its C++ gateway MEDIA session has
# been absent from the gateway's own session list. The zombie sweep above
# reconciles against Asterisk and so only catches "local session, no channel".
# This catches the mirror failure that actually produces silent dead air: the
# channel is up and billing, but the RTP session carrying the audio is gone
# (gateway restart/crash, reaped session, a start that failed after answer).
# Asterisk reports that channel as perfectly live, so no other sweep fires.
_dead_media_ticks: dict[str, int] = {}
_DEAD_MEDIA_TICK_THRESHOLD = 2
try:
    _MEDIA_WATCHDOG_INTERVAL_S = max(
        0.5,
        min(30.0, float(os.getenv("TELEPHONY_MEDIA_WATCHDOG_INTERVAL_S", "1.0"))),
    )
except (TypeError, ValueError):
    _MEDIA_WATCHDOG_INTERVAL_S = 1.0

# Reliability quick-win: `_on_audio_received` swallows all exceptions from
# the media gateway so a single bad RTP packet can never crash the audio
# hot path — but a *recurring* fault (e.g. STT queue wedged, resample lib
# broken for this call's codec) used to be invisible until the 300s
# watchdog finally noticed dead air. This tracks per-call consecutive
# failures so we can (a) surface it at WARNING, rate-limited so a storm of
# bad packets doesn't spam the log, and (b) force-end the call well before
# the watchdog if the fault never clears.
_audio_route_failure_counts: dict[str, int] = {}
_audio_route_last_logged_at: dict[str, float] = {}
_AUDIO_ROUTE_LOG_INTERVAL_S = 5.0
_AUDIO_ROUTE_FORCE_END_THRESHOLD = 50


def _detect_zombie_sessions(
    local_ids: list,
    live_channel_ids: Optional[set],
    *,
    threshold: int = _ZOMBIE_TICK_THRESHOLD,
) -> list:
    """Reconcile local voice-session ids against Asterisk's live channel ids;
    return ids missing for >= ``threshold`` consecutive ticks.

    Pure except for the module-level ``_zombie_channel_ticks`` counter it
    advances. The debounce absorbs a transient ARI hiccup or a just-created
    session whose channel briefly races the list — a real live call is present
    every tick, so it can never reach the threshold.

    ``live_channel_ids is None`` means "couldn't query ARI this tick": a no-op
    that returns ``[]`` and advances no counter, so a flaky ARI read never
    tears down live calls.
    """
    if live_channel_ids is None:
        return []
    local_set = set(local_ids)
    # Forget counters for sessions that are no longer local (ended normally).
    for cid in list(_zombie_channel_ticks):
        if cid not in local_set:
            _zombie_channel_ticks.pop(cid, None)
    zombies: list = []
    for cid in local_ids:
        if cid in live_channel_ids:
            _zombie_channel_ticks.pop(cid, None)  # present → reset
        else:
            n = _zombie_channel_ticks.get(cid, 0) + 1
            _zombie_channel_ticks[cid] = n
            if n >= threshold:
                zombies.append(cid)
    return zombies


def _detect_dead_media_sessions(
    gateway_session_map: dict,
    live_gateway_session_ids: Optional[set],
    *,
    threshold: int = _DEAD_MEDIA_TICK_THRESHOLD,
) -> list:
    """Reconcile the channels we believe have media against the gateway's own
    session list; return the channel ids whose media session has been missing
    for >= ``threshold`` consecutive ticks.

    Mirrors ``_detect_zombie_sessions`` deliberately, including its two safety
    properties:

    * ``live_gateway_session_ids is None`` means "couldn't query the gateway
      this tick" — a no-op returning ``[]`` that advances no counter, so an
      unreachable gateway never mass-hangs-up live calls.
    * The debounce absorbs the window between ``/v1/sessions/start`` returning
      and the session appearing in the list; a real media session is present
      every tick and can never reach the threshold.

    A channel mapped to an empty session id is skipped: media was never started
    for it, which is the ringing/warmup sweeps' business, not this one's.
    """
    if live_gateway_session_ids is None:
        return []
    # Forget counters for channels that are no longer tracked (ended normally).
    for cid in list(_dead_media_ticks):
        if cid not in gateway_session_map:
            _dead_media_ticks.pop(cid, None)
    dead: list = []
    for cid, session_id in gateway_session_map.items():
        if not session_id:
            _dead_media_ticks.pop(cid, None)
            continue
        if session_id in live_gateway_session_ids:
            _dead_media_ticks.pop(cid, None)  # present → reset
        else:
            n = _dead_media_ticks.get(cid, 0) + 1
            _dead_media_ticks[cid] = n
            if n >= threshold:
                dead.append(cid)
    return dead


async def _reconcile_dead_media(adapter, force_end) -> list:
    """Force-end calls whose C++ gateway media session has disappeared.

    The sweep seam for ``_detect_dead_media_sessions``: queries the gateway for
    media ground truth, debounces, and tears down what is left. Extracted from
    the watchdog body so the WIRING is testable — a guard hooked to a signal
    that never varies is this codebase's recurring failure mode, so the query
    is proven to happen, not just the arithmetic.

    Returns the channel ids it force-ended (for logging/tests). Every failure
    mode is a no-op: no adapter, a non-Asterisk adapter, or a gateway that
    cannot be queried.
    """
    if adapter is None or getattr(adapter, "name", None) != "asterisk":
        return []
    if not hasattr(adapter, "list_active_gateway_session_ids"):
        return []
    session_map = adapter.gateway_session_map()
    if not session_map:
        # Nothing believed live; still prune stale counters.
        _detect_dead_media_sessions({}, set())
        return []
    live_ids = await adapter.list_active_gateway_session_ids()
    dead = _detect_dead_media_sessions(session_map, live_ids)
    ended: list = []
    for cid in dead:
        _dead_media_ticks.pop(cid, None)
        record_gateway_media_reconciliation("detected")
        logger.warning(
            "telephony_watchdog: media session for %s missing from the gateway "
            "for %d ticks — the channel is up but carries no audio, forcing end",
            cid[:12],
            _DEAD_MEDIA_TICK_THRESHOLD,
            extra={"call_id": cid, "alert": "gateway_media_session_lost"},
        )
        try:
            await force_end(cid)
            ended.append(cid)
            record_gateway_media_reconciliation("ended")
        except Exception as exc:  # noqa: BLE001 — one bad teardown must not abort the sweep
            record_gateway_media_reconciliation("end_failed")
            logger.debug("dead_media_force_end_failed cid=%s err=%s", cid[:12], exc)
    return ended


async def _media_session_watchdog() -> None:
    """Detect a vanished C++ media session independently of the 30s sweeps.

    At the default one-second cadence and two-miss debounce, an answered call
    with no RTP session is ended in roughly two seconds.  Keeping this check
    separate avoids running the much heavier ARI/database lifecycle sweep once
    per second.  An unreachable gateway is always a no-op; only an explicit,
    successful session inventory can advance the miss counter.
    """
    while True:
        try:
            await asyncio.sleep(_MEDIA_WATCHDOG_INTERVAL_S)
            await _reconcile_dead_media(get_adapter(), _force_end_and_hangup)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watchdogs must self-heal
            logger.warning("dead_media_reconcile_failed err=%s", exc)


def _session_is_closing(vs) -> bool:
    """True when the agent is actively closing the deal — which earns the call
    an extension past the soft cap (up to the hard ceiling).

    The soft cap exists so a chatty or stalled call is wrapped up at ~5 min,
    but a call that's genuinely about to convert shouldn't be cut off mid-close.
    Signals, any of which grant the extension:

      * ``voice_session._deal_closing`` — an explicit flag the pipeline/agent
        sets when the LLM signals it's about to close (strongest signal).
      * ``conversation_context.user_confirmed`` — the prospect has verbally
        confirmed the action; we're in the final beat of the close.
      * the conversation has reached the ``closing`` stage.

    Best-effort: any missing attribute just means "not closing" → the soft cap
    applies. Never raises.
    """
    if bool(getattr(vs, "_deal_closing", False)):
        return True
    cs = getattr(vs, "call_session", None)
    if cs is None:
        return False
    ctx = getattr(cs, "conversation_context", None)
    if ctx is not None and bool(getattr(ctx, "user_confirmed", False)):
        return True
    # Current conversation stage may live on the context or the session,
    # depending on pipeline. Treat a "closing" stage as an active close.
    stage = getattr(ctx, "state", None) or getattr(cs, "state", None) or getattr(cs, "stage", None)
    if stage is not None and str(getattr(stage, "value", stage)).lower() == "closing":
        return True
    return False


def _collect_expired_sessions(
    session_items,
    *,
    inactivity_timeout_s: int,
    max_duration_s: int,
    soft_cap_s: int = 0,
) -> tuple[list, list]:
    """Pure classifier: split live voice sessions into (stale, overlong).

    FIX #11 — wires the previously-dead ``_SESSION_MAX_DURATION_S``
    (``TELEPHONY_MAX_CALL_DURATION_S``) into an actual check. Before this,
    the watchdog only ever enforced inactivity (``is_stale``); a call
    parked on hold/IVR/voicemail music keeps producing transcripts, so
    ``last_activity_at`` stays fresh and ``is_stale()`` never trips — such a
    call could run to the gateway's ~2h hard cap.

    ``session_items`` is the ``(call_id, voice_session)`` pair iterable
    ``state_backend.iter_voice_session_items()`` yields. Extracted as a pure
    function (mirrors ``_detect_zombie_sessions`` above) so the
    classification logic is unit-testable without standing up the full
    watchdog loop's state-backend / adapter / redis dependencies.

    A session that is both silent AND ancient is reported once, as stale
    (inactivity takes priority) — matches the watchdog's pre-fix behaviour
    for the inactivity case exactly, just adds a second independent trip
    wire for calls that stay "active" indefinitely.
    """
    stale: list = []
    overlong: list = []
    for call_id, vs in session_items:
        call_session = getattr(vs, "call_session", None)
        if call_session is None:
            continue
        transfer_connected = bool(getattr(vs, "_transfer_connected", False))
        # Once a supervised human target answers, the AI/media session is
        # intentionally quiesced and therefore looks inactive. The pinned
        # absolute deadline remains authoritative for the human bridge.
        if not transfer_connected and call_session.is_stale(inactivity_timeout_s):
            stale.append(call_id)
            continue
        duration = call_session.get_duration_seconds()
        session_max_duration = getattr(vs, "_max_call_duration_seconds", max_duration_s)
        if not isinstance(session_max_duration, (int, float)) or session_max_duration <= 0:
            session_max_duration = max_duration_s
        session_soft_cap = getattr(vs, "_soft_call_cap_seconds", soft_cap_s)
        if not isinstance(session_soft_cap, (int, float)) or session_soft_cap < 0:
            session_soft_cap = soft_cap_s
        # Absolute hard ceiling — end no matter what.
        if duration > session_max_duration:
            overlong.append(call_id)
            continue
        # Soft cap (5 min): wrap up unless the agent is actively closing.
        if (
            not transfer_connected
            and session_soft_cap
            and duration > session_soft_cap
            and not _session_is_closing(vs)
        ):
            overlong.append(call_id)
    return stale, overlong


# ---------------------------------------------------------------------------
# Pre-hangup wrap-up nudge
# ---------------------------------------------------------------------------
# `_collect_expired_sessions` + `_force_end_and_hangup` enforce the duration
# caps by silently tearing the call down — the caller is cut off mid-sentence
# with no warning at all. This gives the agent ONE short heads-up shortly
# before its effective deadline so it can land the call gracefully.
#
# It is a *system-level instruction*, not a spoken line: the agent decides how
# to close in its own voice on its next turn. Delivery reuses the injection
# channel each pipeline mode already has — no new channel is built:
#
#   cascaded  — a MessageRole.SYSTEM entry appended to
#               ``call_session.conversation_history``. That's the same list the
#               pipeline already appends mid-call steering entries to
#               (turn_ender, instant_opener, agent_first's disclosure), and
#               turn_streamer hands it to the LLM with roles intact, so a
#               system-role entry arrives as a real system message.
#   realtime  — ``realtime_session.send_text(..., create_response=False)``, the
#               ``conversation.item.create`` path openai_realtime.py documents
#               for "any future system-initiated prompt". create_response is
#               False on purpose: the model must fold this into its NEXT turn,
#               not immediately talk over a caller who is mid-sentence.
_WRAP_UP_LEAD_DEFAULT_S = 20
_WRAP_UP_LEAD_MIN_S = 5
_WRAP_UP_LEAD_MAX_S = 60

_WRAP_UP_INSTRUCTION_TEMPLATE = (
    "The call must end in about {seconds} seconds. Finish your current point "
    "in one short sentence, thank the caller, and say goodbye. Do not start a "
    "new topic."
)


def _wrap_up_lead_seconds() -> int:
    """How many seconds before the effective deadline the nudge fires.

    ``CALL_WRAP_UP_LEAD_SECONDS``, clamped to [5, 60]. Read per call (not at
    import) so an operator can retune it without a restart; anything
    unparseable falls back to the 20s default rather than disabling the nudge.
    """
    raw = os.getenv("CALL_WRAP_UP_LEAD_SECONDS")
    if raw is None or not str(raw).strip():
        value = _WRAP_UP_LEAD_DEFAULT_S
    else:
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            logger.warning(
                "wrap_up_nudge: CALL_WRAP_UP_LEAD_SECONDS=%r is not a number — " "using %ds",
                str(raw)[:32],
                _WRAP_UP_LEAD_DEFAULT_S,
            )
            value = _WRAP_UP_LEAD_DEFAULT_S
    return max(_WRAP_UP_LEAD_MIN_S, min(_WRAP_UP_LEAD_MAX_S, value))


def _effective_deadline_seconds(vs, *, max_duration_s: int, soft_cap_s: int) -> float:
    """The duration at which ``_collect_expired_sessions`` would end this call.

    Mirrors that classifier exactly so the nudge lands relative to the deadline
    the call will ACTUALLY hit: the soft cap when it applies, otherwise the
    hard ceiling. Per-session overrides win over the module defaults, same as
    there.
    """
    session_max_duration = getattr(vs, "_max_call_duration_seconds", max_duration_s)
    if not isinstance(session_max_duration, (int, float)) or session_max_duration <= 0:
        session_max_duration = max_duration_s
    session_soft_cap = getattr(vs, "_soft_call_cap_seconds", soft_cap_s)
    if not isinstance(session_soft_cap, (int, float)) or session_soft_cap < 0:
        session_soft_cap = soft_cap_s
    transfer_connected = bool(getattr(vs, "_transfer_connected", False))
    if not transfer_connected and session_soft_cap:
        return float(min(session_soft_cap, session_max_duration))
    return float(session_max_duration)


def _collect_wrap_up_candidates(
    session_items,
    *,
    max_duration_s: int,
    soft_cap_s: int,
    lead_s: int,
) -> list:
    """Pure classifier: live sessions inside the wrap-up window.

    Returns ``(call_id, voice_session, seconds_remaining)`` for every session
    that is within ``lead_s`` of its effective deadline and has not been
    nudged yet. Same "pure function, plain objects" shape as
    ``_collect_expired_sessions`` / ``_detect_zombie_sessions`` so it is
    unit-testable without the watchdog's state-backend/adapter dependencies.

    Never raises: a session whose attributes blow up is skipped, because a
    nudge must never be able to stop the watchdog's teardown sweeps.
    """
    candidates: list = []
    for call_id, vs in session_items:
        try:
            if getattr(vs, "_wrap_up_nudged", False):
                continue
            call_session = getattr(vs, "call_session", None)
            if call_session is None:
                continue
            # A call the agent is actively closing is already heading for a
            # natural ending, and the soft cap has been waived for it —
            # telling it to wrap up would step on the close it is landing.
            if _session_is_closing(vs):
                continue
            deadline = _effective_deadline_seconds(
                vs,
                max_duration_s=max_duration_s,
                soft_cap_s=soft_cap_s,
            )
            remaining = deadline - float(call_session.get_duration_seconds())
            # remaining <= 0 is already overlong: `_collect_expired_sessions`
            # is ending it on this same tick, so a nudge would only arrive
            # after the hangup.
            if 0 < remaining <= lead_s:
                candidates.append((call_id, vs, remaining))
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            logger.debug(
                "wrap_up_nudge_classify_skipped call=%s err=%s",
                str(call_id)[:12],
                exc,
            )
    return candidates


async def _inject_wrap_up_nudge(
    call_id: str,
    vs,
    seconds_remaining: float,
    instruction: str,
) -> bool:
    """Deliver the wrap-up instruction on whichever channel this session has.

    Returns True when it landed. Never raises — this runs fire-and-forget off
    the watchdog, and a dead socket or a half-built session must degrade to a
    log line, never to a lost teardown sweep.
    """
    short_id = str(call_id)[:12]
    try:
        # Realtime speech-to-speech bridge.
        if getattr(vs, "realtime_bridge", None) is not None:
            rt = getattr(vs, "realtime_session", None)
            send_text = getattr(rt, "send_text", None)
            if not callable(send_text):
                logger.warning(
                    "wrap_up_nudge_no_channel call=%s mode=realtime — session "
                    "exposes no send_text; skipping (call will still be ended "
                    "on time)",
                    short_id,
                )
                return False
            await send_text(instruction, create_response=False)
            logger.info(
                "wrap_up_nudge call=%s mode=realtime seconds_remaining=%d",
                short_id,
                int(seconds_remaining),
            )
            return True

        # Cascaded STT→LLM→TTS pipeline.
        call_session = getattr(vs, "call_session", None)
        history = getattr(call_session, "conversation_history", None)
        if not isinstance(history, list):
            logger.warning(
                "wrap_up_nudge_no_channel call=%s mode=cascaded — no "
                "conversation_history to inject into; skipping (call will "
                "still be ended on time)",
                short_id,
            )
            return False
        from app.domain.models.conversation import Message, MessageRole

        history.append(Message(role=MessageRole.SYSTEM, content=instruction))
        logger.info(
            "wrap_up_nudge call=%s mode=cascaded seconds_remaining=%d",
            short_id,
            int(seconds_remaining),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        logger.warning(
            "wrap_up_nudge_failed call=%s seconds_remaining=%d err=%s",
            short_id,
            int(seconds_remaining),
            exc,
        )
        return False


def _dispatch_wrap_up_nudges(
    session_items,
    *,
    max_duration_s: int,
    soft_cap_s: int,
    lead_s: Optional[int] = None,
) -> list:
    """Nudge every session inside the wrap-up window; return the ids nudged.

    Synchronous and non-blocking: the classification is pure, and each
    injection is fire-and-forget via ``_track_task`` so a wedged realtime
    socket can never stall the watchdog's inactivity/zombie/orphan sweeps.

    The ``_wrap_up_nudged`` flag is set HERE, before the task is spawned — not
    inside the task — so the next 30s tick cannot fire a second nudge while
    the first injection is still in flight.
    """
    if lead_s is None:
        lead_s = _wrap_up_lead_seconds()
    nudged: list = []
    instruction = _WRAP_UP_INSTRUCTION_TEMPLATE.format(seconds=int(lead_s))
    for call_id, vs, remaining in _collect_wrap_up_candidates(
        session_items,
        max_duration_s=max_duration_s,
        soft_cap_s=soft_cap_s,
        lead_s=lead_s,
    ):
        try:
            vs._wrap_up_nudged = True
        except Exception as exc:  # noqa: BLE001 — can't mark it, don't fire it
            logger.debug(
                "wrap_up_nudge_unmarkable call=%s err=%s",
                str(call_id)[:12],
                exc,
            )
            continue
        _track_task(_inject_wrap_up_nudge(call_id, vs, remaining, instruction))
        nudged.append(call_id)
    return nudged


async def _force_end_and_hangup(
    call_id: str,
    *,
    require_confirmation: bool = True,
    provider_leg_ids: Optional[list[str]] = None,
    recovery_context: Optional[Dict[str, Any]] = None,
    acknowledge_ledger: bool = True,
) -> bool:
    """Prove PBX teardown, then force-end and persist the logical session.

    Forced teardown asks the PBX to remove every owned human leg first, then
    persists/settles the logical call. Asterisk exposes a confirmation-aware
    method so an ARI outage cannot release reservations while PSTN channels
    may still be billable. Ordinary terminal events race safely through the
    idempotent ``_on_call_ended`` path. Returns ``True`` only when logical
    teardown was allowed to run; ``False`` means termination was unconfirmed
    and all settlement must remain deferred.
    """
    if recovery_context is not None:
        # Asterisk DELETE can synchronously emit ChannelDestroyed/StasisEnd
        # before the bounded all-leg inventory proof has completed. Fence the
        # ordinary callback until this coordinator has proved every durable
        # linked provider leg absent.
        recovery_context["_awaiting_all_leg_absence_proof"] = True
        recovery_context.pop("_pbx_all_leg_absence_confirmed", None)
    adapter = get_adapter()
    hangup_confirmed = not require_confirmation
    if adapter is not None:
        try:
            confirm_many = getattr(adapter, "hangup_many_confirmed", None)
            confirm = getattr(adapter, "hangup_confirmed", None)
            explicit_legs = list(dict.fromkeys([call_id, *(provider_leg_ids or [])]))
            if len(explicit_legs) > 1:
                if callable(confirm_many):
                    hangup_confirmed = bool(await confirm_many(explicit_legs))
                else:
                    # Proving only the parent would release linked-leg billing
                    # and leases while a persisted human target may remain
                    # live. Adapters that own multiple legs must expose one
                    # bounded all-leg proof operation.
                    hangup_confirmed = False
                    logger.error(
                        "force_end_and_hangup: adapter lacks all-leg proof "
                        "call=%s linked_legs=%d",
                        call_id[:12],
                        len(explicit_legs) - 1,
                    )
            elif callable(confirm):
                hangup_confirmed = bool(await confirm(call_id))
            else:
                # Legacy/non-Asterisk adapters have no separate proof query.
                # Preserve their existing semantics for ordinary callers, but
                # never promote request acceptance into PBX proof when the
                # recovery/shutdown caller explicitly requires confirmation.
                await adapter.hangup(call_id)
                hangup_confirmed = not require_confirmation
        except Exception as exc:
            hangup_confirmed = False
            logger.warning(
                "force_end_and_hangup: adapter.hangup unconfirmed call=%s err=%s",
                call_id[:12],
                exc,
            )
    if not hangup_confirmed:
        logger.critical(
            "force_end_and_hangup: settlement deferred until PBX termination proof call=%s",
            call_id[:12],
        )
        return False
    if recovery_context is not None:
        recovery_context["_awaiting_all_leg_absence_proof"] = False
        recovery_context["_pbx_all_leg_absence_confirmed"] = True
    # Settle only after the PBX accepted deletion (or proved the channel was
    # already absent). Terminal callbacks may race this call; teardown is
    # idempotent and the in-flight marker chooses one finalizer.
    if recovery_context is None and acknowledge_ledger:
        # Keep the legacy one-argument call shape for tests and adapters that
        # replace the callback. Recovery passes explicit context below.
        logical_result = await _on_call_ended(call_id)
    else:
        logical_result = await _on_call_ended(
            call_id,
            recovery_context=recovery_context,
            acknowledge_ledger=acknowledge_ledger,
        )
    return logical_result is not False


async def _session_watchdog() -> None:
    """
    Periodically scan active sessions and tear down any that have been silent
    for longer than _SESSION_INACTIVITY_TIMEOUT_S, or that have run longer
    than _SESSION_MAX_DURATION_S regardless of activity.

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

            # ----- Active session inactivity + max-duration sweep -----
            _session_pairs = list(_sb.iter_voice_session_items())
            for call_id, _vs in _session_pairs:
                # Refresh the Redis ledger TTL for every live call each
                # tick (debounced in the backend) so a call that's up but
                # momentarily silent — caller on hold, long agent turn —
                # doesn't let its ledger entry expire and become an
                # un-recoverable zombie. No-op on the memory backend.
                _sb.touch_call(call_id)
            # FIX 1 — last_activity_at lives on CallSession (vs.call_session),
            # not VoiceSession (vs).  is_stale() compares datetime correctly
            # instead of mixing monotonic time + datetime.
            # FIX #11 — wires the previously-dead TELEPHONY_MAX_CALL_DURATION_S
            # / _SESSION_MAX_DURATION_S as a second, independent trip wire
            # (see _collect_expired_sessions docstring for why the inactivity
            # check alone isn't enough).
            # Give the agent a heads-up BEFORE the caps below cut the call
            # off mid-sentence. Fire-and-forget, exception-logged, at most
            # once per session — see _dispatch_wrap_up_nudges.
            try:
                _dispatch_wrap_up_nudges(
                    _session_pairs,
                    max_duration_s=_SESSION_MAX_DURATION_S,
                    soft_cap_s=_SESSION_SOFT_CAP_S,
                )
            except Exception as exc:  # noqa: BLE001 — never block the sweeps
                logger.warning("wrap_up_nudge_dispatch_failed err=%s", exc)

            stale, overlong = _collect_expired_sessions(
                _session_pairs,
                inactivity_timeout_s=_SESSION_INACTIVITY_TIMEOUT_S,
                max_duration_s=_SESSION_MAX_DURATION_S,
                soft_cap_s=_SESSION_SOFT_CAP_S,
            )
            for call_id in stale:
                logger.warning(
                    "telephony_watchdog: stale session %s (inactive >%ds) — forcing end",
                    call_id[:12],
                    _SESSION_INACTIVITY_TIMEOUT_S,
                )
                await _force_end_and_hangup(call_id)
            for call_id in overlong:
                logger.warning(
                    "telephony_watchdog: session %s exceeded max call duration "
                    "(>%ds) — forcing end",
                    call_id[:12],
                    _SESSION_MAX_DURATION_S,
                )
                await _force_end_and_hangup(call_id)

            # ----- Authoritative zombie-session reconcile -----
            # Ground-truth check against Asterisk: a local voice session whose
            # channel no longer exists is a zombie left by a missed
            # ChannelDestroyed event. The inactivity sweep above can miss these
            # (a stuck pipeline/greeting task keeps last_activity_at fresh), and
            # the lease-refresh loop further down then keeps the zombie's global
            # concurrency slot alive — ~10 such leaks fill the cap and block ALL
            # outbound calls (the 10/10 incident). Force-end zombies here, BEFORE
            # the lease refresh, so the slot is released within ~60s. Runs only
            # on the Asterisk adapter; a None channel list (ARI unreachable) is a
            # safe no-op.
            try:
                _adapter = get_adapter()
                if _adapter is not None and getattr(_adapter, "name", None) == "asterisk":
                    _live_ids = await _adapter.list_active_channel_ids()
                    _zombies = _detect_zombie_sessions(
                        [cid for cid, _ in _sb.iter_voice_session_items()],
                        _live_ids,
                    )
                    for cid in _zombies:
                        _zombie_channel_ticks.pop(cid, None)
                        logger.warning(
                            "telephony_watchdog: zombie session %s — no Asterisk "
                            "channel for %d ticks, forcing end to release its "
                            "concurrency slot",
                            cid[:12],
                            _ZOMBIE_TICK_THRESHOLD,
                        )
                        # adapter.hangup() here is a best-effort no-op in the
                        # common case (the channel is already gone — that's
                        # WHY it's a zombie) but is cheap insurance against a
                        # missed-event false positive where the channel is
                        # actually still live.
                        await _force_end_and_hangup(cid)

            except Exception as exc:
                logger.debug("zombie_reconcile_failed err=%s", exc)

            # TODO (FIX #1c — inverse reconcile, not implemented here):
            # the sweep above only catches "local session, no live channel".
            # The mirror leak — "live Asterisk channel, no local session" (a
            # channel ARI thinks is answered but for which _on_new_call never
            # ran / crashed before registering the session, e.g. the
            # exception path around line ~989 raced a crash before
            # `_state().set_voice_session` — leaves an RTP/ExternalMedia leg
            # burning gateway resources with nothing locally tracking it. To
            # implement: alongside `_live_ids = await _adapter.list_active_
            # channel_ids()` above, diff `_live_ids` against
            # `_session_keys | _warmup_keys` (both already computed a few
            # lines below) and debounce over N ticks the same way
            # `_detect_zombie_sessions` does, then `adapter.hangup(cid)` for
            # anything that survives the debounce. Left as a TODO rather than
            # implemented blind: a channel can legitimately be live-but-
            # unregistered for the few hundred ms between StasisStart and
            # `_on_new_call` finishing `_sb.set_voice_session(...)` — the
            # debounce window needs to be tuned against real Asterisk event
            # timing (not available in this environment) to avoid hanging up
            # calls that are simply mid-setup.

            # ----- Orphaned ringing-warmup sweep (bug #3 / #7) -----
            stale_ringing = [
                cid
                for cid, created_at in _sb.iter_ringing_started_at_items()
                if (now - created_at) > _RINGING_MAX_AGE_S
            ]
            for cid in stale_ringing:
                ringing = _pop_ringing_warmup(cid)
                _sb.pop_ringing_event(cid)
                logger.warning(
                    "telephony_watchdog: orphaned ringing_warmup %s "
                    "(age >%ds) — releasing STT/TTS sockets",
                    cid[:12],
                    _RINGING_MAX_AGE_S,
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
                            "Watchdog end_session failed for %s: %s",
                            cid[:12],
                            exc,
                        )

            # ----- Orphaned ringing_events sweep -----
            # Events without a matching warmup entry are pure leakage; drop on
            # the same age policy. (Events with a matching warmup entry get
            # cleaned up by the warmup sweep above.)
            _warmup_keys = {cid for cid, _ in _sb.iter_ringing_started_at_items()}
            _session_keys = {cid for cid, _ in _sb.iter_voice_session_items()}
            stale_events = [
                cid
                for cid in _sb.iter_ringing_event_keys()
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
                gw_id
                for gw_id, cid in _sb.iter_gateway_session_items()
                if cid not in active_call_ids
            ]
            for gw_id in orphan_gw:
                _sb.remove_gateway_session(gw_id)
                buf = _sb.drain_early_audio(gw_id)
                if buf:
                    logger.warning(
                        "telephony_watchdog: dropping orphan early_audio_buffer "
                        "gateway_session_id=%s chunks=%d",
                        gw_id,
                        len(buf),
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
                        live_ids = {getattr(v, "call_id", None) for v in _live_sessions}
                        live_ids.discard(None)
                        for cid in list(getattr(tts, "_call_ws", {}).keys()):
                            if cid not in live_ids:
                                logger.warning(
                                    "telephony_watchdog: evicting orphan cartesia WS " "call_id=%s",
                                    str(cid)[:12],
                                )
                                try:
                                    await tts.disconnect_for_call(cid)
                                except Exception as exc:
                                    logger.debug(
                                        "cartesia evict failed call_id=%s: %s",
                                        str(cid)[:12],
                                        exc,
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


_RECOVERY_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "ended",
        "failed",
        "cancelled",
        "canceled",
        "rejected",
        "busy",
        "no_answer",
    }
)
_RECOVERY_SETTLED_BILLING_STATUSES = frozenset({"finalized", "released", "reversed", "held"})


def _is_recovery_row_logically_settled(row: Mapping[str, Any]) -> bool:
    """Whether a durable call row no longer needs restart settlement."""

    status = str(row.get("status") or "").strip().lower()
    direction = str(row.get("direction") or "outbound").strip().lower()
    if direction == "inbound":
        return (
            row.get("ended_at") is not None
            and str(row.get("billing_status") or "").strip().lower()
            in _RECOVERY_SETTLED_BILLING_STATUSES
            and status in _RECOVERY_TERMINAL_STATUSES
        )
    return (
        row.get("ended_at") is not None
        and row.get("outcome") is not None
        and row.get("terminal_settled_at") is not None
        and (
            row.get("terminal_retry_payload") is None
            or row.get("terminal_retry_enqueued_at") is not None
        )
        and status in _RECOVERY_TERMINAL_STATUSES
    )


def _ledger_answer_elapsed_seconds(
    ledger_entry: Mapping[str, Any],
    *,
    ended_at: Any = None,
    max_seconds: Any = None,
) -> int:
    """Elapsed time from confirmed Answer evidence, bounded by reservation."""

    raw_timestamp = ledger_entry.get("answered_at")
    if not raw_timestamp:
        return 0
    try:
        started = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        ended = ended_at or datetime.now(timezone.utc)
        if isinstance(ended, str):
            ended = datetime.fromisoformat(ended.replace("Z", "+00:00"))
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        elapsed = max(
            1,
            math.ceil(max(0.0, (ended - started).total_seconds())),
        )
        if isinstance(max_seconds, int) and not isinstance(max_seconds, bool):
            return min(elapsed, max(0, max_seconds))
        return elapsed
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0


async def _hydrate_orphan_recovery_context(
    provider_session_id: str,
    ledger_entry: Mapping[str, Any],
) -> Dict[str, Any]:
    """Load the authoritative durable context for restart teardown.

    Redis intentionally mirrors only ownership metadata. Direction, provider
    identity, admission/billing state, elapsed duration, and connected
    transfer targets come from PostgreSQL so a successor never guesses an
    inbound call is outbound or proves only the parent while a connected human
    transfer leg remains live.

    A successful query with no row is authoritative too: the process may have
    crashed after registering a PBX session but before creating its durable
    call record. Such a channel still needs termination, but has no database
    settlement to perform. Dependency/query failures raise and leave both the
    PBX call and Redis obligation retryable.
    """

    from app.core.container import get_container
    from app.core.db_utils import acquire_with_tenant
    from app.domain.services.telephony.termination import (
        fetch_active_provider_leg_ids,
    )

    container = get_container()
    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        raise RuntimeError("database unavailable for orphan recovery hydration")

    durable_call_id = str(ledger_entry.get("durable_call_id") or "").strip() or None
    ledger_provider = str(ledger_entry.get("provider") or "").strip().lower()
    ledger_tenant_id = str(ledger_entry.get("tenant_id") or "").strip() or None

    async with acquire_with_tenant(db_pool, None, timeout=5.0) as conn:
        raw_row = await conn.fetchrow(
            """
            SELECT c.id, c.tenant_id, c.campaign_id, c.direction, c.provider,
                   c.provider_call_id, c.external_call_uuid, c.status,
                   c.outcome, c.started_at, c.answered_at, c.ended_at,
                   c.duration_seconds, c.admission_status,
                   c.admission_reason, c.processing_status,
                   c.billing_status, c.reserved_seconds,
                   c.concurrency_lease_id, c.route_snapshot,
                   c.provider_terminated_at,
                   c.terminal_settled_at, c.terminal_retry_payload,
                   c.terminal_retry_enqueued_at,
                    COUNT(*) OVER() AS recovery_match_count,
                    CASE
                      WHEN c.answered_at IS NOT NULL
                        AND c.provider_terminated_at IS NOT NULL
                        AND COALESCE(c.reserved_seconds,0) > 0
                        THEN LEAST(
                          c.reserved_seconds,
                          GREATEST(
                            0,
                            COALESCE(c.duration_seconds,0)
                          )
                        )
                      ELSE 0
                    END AS recovery_duration_seconds
            FROM calls c
            WHERE (c.provider_call_id=$1 OR c.external_call_uuid=$1)
              AND ($2::uuid IS NULL OR c.id=$2::uuid)
              AND (
                    NULLIF($3::text,'') IS NULL
                    OR LOWER(BTRIM(c.provider))=$3::text
                  )
              AND ($4::uuid IS NULL OR c.tenant_id=$4::uuid)
            ORDER BY CASE WHEN c.provider_call_id=$1 THEN 0 ELSE 1 END,
                     c.created_at DESC
            """,
            provider_session_id,
            durable_call_id,
            ledger_provider,
            ledger_tenant_id,
        )
        if raw_row is None:
            ledger_state = str(ledger_entry.get("state") or "").strip().lower()
            return {
                "provider_session_id": provider_session_id,
                "provider_leg_ids": [],
                "durable_call_id": durable_call_id,
                "direction": str(ledger_entry.get("direction") or "unknown"),
                "provider": str(ledger_entry.get("provider") or "unknown"),
                "provider_call_id": str(
                    ledger_entry.get("provider_call_id") or provider_session_id
                ),
                "duration_seconds": _ledger_answer_elapsed_seconds(ledger_entry),
                "was_answered": ledger_state in {"active", "answer_pending"},
                "answer_ambiguous": ledger_state == "answer_pending",
                "logical_settled": True,
                "ledger_entry": dict(ledger_entry),
            }

        row = dict(raw_row)
        match_count = int(row.pop("recovery_match_count", 0) or 0)
        if match_count != 1:
            raise RuntimeError(
                "orphan recovery identity is ambiguous for provider session "
                f"{provider_session_id}"
            )
        # Use the same durable all-leg query as tenant/admin termination.
        # Adapter transfer maps are process-local and empty after a restart.
        provider_leg_ids = list(
            await fetch_active_provider_leg_ids(
                conn,
                call_reference=str(row["id"]),
            )
        )

    status = str(row.get("status") or "").strip().lower()
    ledger_state = str(ledger_entry.get("state") or "").strip().lower()
    was_answered = bool(
        row.get("answered_at") is not None
        or status in {"answered", "in_call"}
        or ledger_state in {"active", "answer_pending"}
    )
    terminal_proof_missing = bool(
        was_answered and row.get("provider_terminated_at") is None
    )
    terminal_duration_ambiguous = bool(
        was_answered
        and row.get("provider_terminated_at") is not None
        and max(0, int(row.get("recovery_duration_seconds") or 0)) == 0
    )
    answer_ambiguous = bool(
        ledger_state == "answer_pending"
        or terminal_proof_missing
        or terminal_duration_ambiguous
    )
    if terminal_proof_missing or terminal_duration_ambiguous:
        row["recovery_duration_seconds"] = 0
    provider_call_id = str(
        row.get("provider_call_id") or row.get("external_call_uuid") or provider_session_id
    )
    admission = {
        # Direction is the durable routing authority. An admitted call may
        # already carry a terminal/released admission_status on an idempotent
        # replay, but it is still inbound and must never enter CallService's
        # outbound lead/campaign path.
        "allowed": (str(row.get("direction") or "").strip().lower() == "inbound"),
        "admission_status": row.get("admission_status"),
        "billing_status": row.get("billing_status"),
        "call_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "campaign_id": (str(row["campaign_id"]) if row.get("campaign_id") else None),
        "provider": str(row.get("provider") or "asterisk"),
        "provider_call_id": provider_call_id,
        "_terminal_reason": (
            "process_restart_answer_ambiguous" if answer_ambiguous else "process_restart_recovery"
        ),
        "_recovery_was_answered": was_answered,
        "_recovery_duration_seconds": max(0, int(row.get("recovery_duration_seconds") or 0)),
    }
    return {
        **row,
        "provider_session_id": provider_session_id,
        "provider_leg_ids": provider_leg_ids,
        "durable_call_id": str(row["id"]),
        "direction": str(row.get("direction") or "outbound").strip().lower(),
        "provider": str(row.get("provider") or "asterisk").strip().lower(),
        "provider_call_id": provider_call_id,
        "duration_seconds": max(0, int(row.get("recovery_duration_seconds") or 0)),
        "was_answered": was_answered,
        "answer_ambiguous": answer_ambiguous,
        "terminal_proof_missing": terminal_proof_missing,
        "terminal_duration_ambiguous": terminal_duration_ambiguous,
        "logical_settled": _is_recovery_row_logically_settled(row),
        "admission": admission,
        "ledger_entry": dict(ledger_entry),
    }


async def _settle_recovered_outbound_call(
    recovery_context: Dict[str, Any],
) -> None:
    """Persist a no-longer-in-memory outbound orphan and verify the write.

    ``CallService.handle_call_status`` is intentionally fail-soft for live
    callbacks, so recovery cannot treat its return as proof. After invoking
    the normal atomic call/lead/job/campaign chain, hydrate the row again and
    require terminal status, outcome and end timestamp before allowing Redis
    acknowledgement.
    """

    if recovery_context.get("logical_settled"):
        return
    durable_call_id = recovery_context.get("durable_call_id")
    if not durable_call_id:
        # A successful authoritative lookup proved there is no DB row.
        return

    from app.core.container import get_container
    from app.core.security.tenant_isolation import (
        set_bypass_rls,
        set_current_tenant_id,
    )
    from app.domain.models.dialer_job import CallOutcome

    container = get_container()
    if not getattr(container, "is_initialized", False):
        raise RuntimeError("service container unavailable for orphan settlement")

    hangup_reason = None
    adapter = get_adapter()
    get_cause = getattr(adapter, "get_hangup_cause", None)
    if callable(get_cause):
        try:
            hangup_reason = get_cause(str(recovery_context.get("provider_session_id") or ""))
        except Exception:
            hangup_reason = None
    outcome = (
        CallOutcome.ANSWERED
        if recovery_context.get("was_answered")
        else resolve_call_outcome(None, hangup_reason=hangup_reason)
    )

    set_bypass_rls(True)
    tenant_id = recovery_context.get("tenant_id")
    if tenant_id:
        set_current_tenant_id(str(tenant_id))
    call_service = CallService(
        db_client=container.db_client,
        queue_service=getattr(container, "_queue_service", None),
        db_pool=container.db_pool,
    )
    result = await call_service.handle_call_status(
        call_uuid=str(durable_call_id),
        outcome=outcome,
        duration=max(0, int(recovery_context.get("duration_seconds") or 0)),
    )
    if not result.durable:
        raise RuntimeError(
            "outbound orphan settlement was not durably committed: "
            f"{result.error or 'unverified'}"
        )

    verified = await _hydrate_orphan_recovery_context(
        str(recovery_context["provider_session_id"]),
        recovery_context.get("ledger_entry") or {},
    )
    if not verified.get("logical_settled"):
        raise RuntimeError("outbound orphan settlement did not reach durable terminal state")
    recovery_context.update(verified)


async def _load_termination_pending_candidates() -> list[dict[str, Any]]:
    """Return durable calls needing confirmed teardown or inbound settlement.

    ``termination_pending`` is deliberately nonterminal: it frees the dialer
    job from a wedged batch without claiming the carrier call ended. The
    telephony owner consumes these rows on startup and every 30-second watchdog
    tick, runs the same confirmation/settlement path as Redis orphans, and
    leaves the status untouched on any dependency or PBX proof failure.

    The second predicate closes a shorter recovery gap: a terminal provider
    callback can prove the PBX leg gone and persist ``ended_at``/terminal
    status, then crash before ``InboundAdmissionService.finalize`` commits.
    Those rows still carry ``billing_status='reserved'`` and must settle on the
    next <=30s tick, not wait for the generic multi-hour reservation reaper.
    The third covers operator-confirmed outbound endings or interrupted Redis
    retry dispatch: terminal PBX truth is not lead/job/campaign settlement.
    """

    from app.core.container import get_container
    from app.core.db_utils import acquire_with_tenant

    container = get_container()
    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        raise RuntimeError("database unavailable for pending termination scan")
    adapter = get_adapter()
    adapter_provider = str(getattr(adapter, "name", "") or "asterisk").strip().lower()
    # This release has proof-aware restart control only for the active
    # Asterisk adapter. A row owned by another carrier/PBX must remain
    # quarantined rather than treating an Asterisk 404 as carrier absence.
    if adapter_provider != "asterisk":
        logger.error(
            "termination_pending_scan_unsupported_adapter provider=%s",
            adapter_provider or "unknown",
        )
        return []
    async with acquire_with_tenant(db_pool, None, timeout=5.0) as conn:
        rows = list(
            await conn.fetch(
                """
                SELECT id, tenant_id, provider, provider_call_id,
                       external_call_uuid, direction, status,
                       processing_status, billing_status, ended_at, updated_at,
                       terminal_settled_at, terminal_retry_payload,
                       terminal_retry_enqueued_at
                FROM calls
                WHERE (
                      status='termination_pending'
                   OR (
                        direction='inbound'
                    AND billing_status='reserved'
                    AND (
                         ended_at IS NOT NULL
                         OR status IN (
                           'ended','completed','failed','cancelled','canceled',
                           'rejected','busy','no_answer'
                         )
                    )
                   )
                   OR (
                        direction='outbound'
                    AND status IN (
                       'ended','completed','failed','cancelled','canceled',
                       'rejected','busy','no_answer'
                    )
                    AND (
                         terminal_settled_at IS NULL
                         OR (
                              terminal_retry_payload IS NOT NULL
                          AND terminal_retry_enqueued_at IS NULL
                         )
                    )
                   )
                   OR (
                        direction='inbound'
                    AND status IN (
                       'ended','completed','failed','cancelled','canceled',
                       'rejected','busy','no_answer'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM call_legs pending_leg
                        WHERE pending_leg.call_id=calls.id
                          AND pending_leg.leg_type='transfer'
                          AND pending_leg.status IN (
                              'initiated','ringing','answered',
                              'in_progress','in_call','active'
                          )
                    )
                   )
                )
                  AND LOWER(BTRIM(provider))=$1::text
                  AND COALESCE(
                        NULLIF(BTRIM(provider_call_id),''),
                        NULLIF(BTRIM(external_call_uuid),'')
                ) IS NOT NULL
                ORDER BY updated_at
                LIMIT 4
                """,
                adapter_provider,
            )
        )

    candidates: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        provider_session_id = str(
            row.get("provider_call_id") or row.get("external_call_uuid") or ""
        ).strip()
        if not provider_session_id:
            logger.error(
                "termination_pending_missing_provider_identity durable_call=%s",
                str(row.get("id") or "-")[:12],
            )
            continue
        candidates.append(
            {
                "call_id": provider_session_id,
                "pod_id": "database:termination_pending",
                "tenant_id": str(row.get("tenant_id") or ""),
                "provider": str(row.get("provider") or ""),
                "durable_call_id": str(row.get("id") or ""),
                "_termination_pending": (str(row.get("status") or "") == "termination_pending"),
                "_inbound_settlement_pending": (
                    str(row.get("direction") or "") == "inbound"
                    and str(row.get("billing_status") or "") == "reserved"
                ),
                "_has_redis_ledger": False,
            }
        )
    return candidates


async def _rotate_deferred_termination_candidate(durable_call_id: str) -> None:
    """Move one still-unresolved durable retry behind newer queue entries."""

    if not durable_call_id:
        return
    from app.core.container import get_container
    from app.core.db_utils import acquire_with_tenant

    container = get_container()
    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        return
    async with acquire_with_tenant(db_pool, None, timeout=5.0) as conn:
        await conn.execute(
            """
            UPDATE calls
            SET updated_at=NOW()
            WHERE id=$1::uuid
              AND (
                   status='termination_pending'
                   OR billing_status='reserved'
                   OR (
                        direction='outbound'
                    AND status IN (
                       'ended','completed','failed','cancelled','canceled',
                       'rejected','busy','no_answer'
                    )
                    AND (
                         terminal_settled_at IS NULL
                         OR (
                              terminal_retry_payload IS NOT NULL
                          AND terminal_retry_enqueued_at IS NULL
                         )
                    )
                   )
              )
            """,
            durable_call_id,
        )


_orphan_recovery_in_flight: set[str] = set()
_ORPHAN_RECOVERY_SOURCE_BATCH = 4
_UNKNOWN_ASTERISK_CHANNEL_CONFIRMATIONS = 2
_unknown_asterisk_channel_ticks: dict[str, int] = {}
# A confirmed-delete request can synchronously trigger the adapter's ordinary
# terminal callback before ``recover_orphaned_calls`` reaches its explicit
# contextual `_on_call_ended` invocation. Keep the hydrated database truth
# visible to that callback so it cannot guess that a restarted inbound call is
# outbound or acknowledge Redis on its own. Entries survive failed attempts
# and are removed only at recovery's explicit commit boundary.
_orphan_recovery_contexts_by_call: dict[str, Dict[str, Any]] = {}


def _current_recovery_exclusions(
    state_backend: Any,
    adapter: Any,
    *,
    ignore_recovery_call_id: Optional[str] = None,
) -> Optional[set[str]]:
    """Return a fail-closed snapshot of locally managed provider channels."""

    exclusions_snapshot = getattr(adapter, "recovery_excluded_channel_ids", None)
    if not callable(exclusions_snapshot):
        return None
    try:
        exclusions = {str(value) for value in exclusions_snapshot() if value}
        for iterator_name in (
            "iter_voice_session_items",
            "iter_ringing_warmup_keys",
            "iter_ringing_event_keys",
        ):
            iterator = getattr(state_backend, iterator_name, None)
            if not callable(iterator):
                continue
            values = iterator()
            if iterator_name == "iter_voice_session_items":
                exclusions.update(str(call_id) for call_id, _ in values if call_id)
            else:
                exclusions.update(str(call_id) for call_id in values if call_id)
        exclusions.update(str(value) for value in _inbound_admissions_in_flight if value)
        exclusions.update(str(value) for value in _inbound_admissions_pending if value)
        exclusions.update(
            str(value)
            for value in _orphan_recovery_in_flight
            if value and str(value) != str(ignore_recovery_call_id or "")
        )
        return exclusions
    except Exception as exc:  # noqa: BLE001 - uncertain local state is not absence proof
        logger.warning("recovery_local_exclusion_snapshot_failed err=%s", exc)
        return None


async def _register_unknown_asterisk_cleanup_candidates(
    state_backend: Any,
    adapter: Any,
    *,
    owner_already_confirmed: bool = False,
) -> int:
    """Mirror previously invisible live ARI channels into the retry ledger.

    This is the inverse of ordinary orphan recovery.  It handles a hard crash
    after Asterisk emits inbound ``StasisStart`` but before admission's first
    awaited Redis registration.  Only the exclusive telephony owner may scan,
    two successful inventories must observe the same unknown channel, and the
    state backend must prove no ledger exists before a strict write.  PBX work
    is intentionally left to the normal hydrate/confirm/settle recovery path.
    """

    if str(getattr(adapter, "name", "") or "").strip().lower() != "asterisk":
        return 0
    inventory = getattr(adapter, "list_recoverable_application_channel_ids", None)
    if not callable(inventory):
        return 0
    ownership_check = getattr(state_backend, "is_telephony_owner", None)
    if not callable(ownership_check) or (not owner_already_confirmed and not ownership_check()):
        return 0

    live_channels = await inventory()
    if live_channels is None:
        return 0
    exclusions = _current_recovery_exclusions(state_backend, adapter)
    if exclusions is None:
        return 0
    unknown = {
        str(channel_id)
        for channel_id in live_channels
        if channel_id and str(channel_id) not in exclusions
    }

    # A channel must appear in two successful owner-scoped inventories.  Drop
    # counters as soon as a successful inventory no longer classifies it as
    # unknown, so short setup races cannot become delayed teardown requests.
    for channel_id in list(_unknown_asterisk_channel_ticks):
        if channel_id not in unknown:
            _unknown_asterisk_channel_ticks.pop(channel_id, None)
    for channel_id in unknown:
        _unknown_asterisk_channel_ticks[channel_id] = min(
            _UNKNOWN_ASTERISK_CHANNEL_CONFIRMATIONS,
            _unknown_asterisk_channel_ticks.get(channel_id, 0) + 1,
        )

    claim = getattr(state_backend, "claim_cleanup_obligation_if_absent", None)
    if not callable(claim):
        return 0

    registered = 0
    ready = sorted(
        channel_id
        for channel_id, observations in _unknown_asterisk_channel_ticks.items()
        if observations >= _UNKNOWN_ASTERISK_CHANNEL_CONFIRMATIONS
    )
    for channel_id in ready[:_ORPHAN_RECOVERY_SOURCE_BATCH]:
        # Inventory and Redis reads are awaited. Re-prove exclusive authority
        # at the final durable-write boundary; normal recovery checks it again
        # after database hydration and immediately before PBX control.
        if not ownership_check():
            logger.warning("inverse_ari_recovery_stopped_after_ownership_loss")
            break
        final_exclusions = _current_recovery_exclusions(state_backend, adapter)
        if final_exclusions is None:
            break
        if channel_id in final_exclusions:
            _unknown_asterisk_channel_ticks.pop(channel_id, None)
            continue
        claimed = await claim(
            channel_id,
            state="termination_pending",
            provider="asterisk",
            provider_call_id=channel_id,
        )
        _unknown_asterisk_channel_ticks.pop(channel_id, None)
        if not claimed:
            continue
        registered += 1
        logger.critical(
            "inverse_ari_recovery_registered channel=%s",
            channel_id[:12],
        )
    return registered


async def recover_orphaned_calls() -> int:
    """Hang up and record calls abandoned by a dead process incarnation.

    Candidates come from both Redis entries whose owning incarnation is dead
    and durable ``calls.status='termination_pending'`` rows emitted by the
    stuck-call safety reaper. Discovery removes neither source. For each, we:

      1. Ask the adapter for confirmation-aware PBX teardown.
      2. Run normal logical teardown only after that proof.
      3. Acknowledge (delete) a Redis orphan entry only after both steps. A
         database-only candidate converges when settlement transitions its
         calls row out of ``termination_pending``.

    An unconfirmed attempt does not emit an ended event, release a lease, or
    settle billing. The untouched ledger entry is retried by the next watchdog
    pass or successor process. A local in-flight set prevents startup recovery
    and the watchdog from acting on the same call concurrently.

    Returns the number of calls recovered.
    """
    sb = _state()
    active_adapter = get_adapter()
    adapter_provider = str(getattr(active_adapter, "name", "") or "asterisk").strip().lower()
    ownership_check = getattr(sb, "is_telephony_owner", None)
    if callable(ownership_check) and not ownership_check():
        logger.warning("orphan_recovery_skipped_nonowner")
        return 0
    try:
        await _register_unknown_asterisk_cleanup_candidates(
            sb,
            active_adapter,
            owner_already_confirmed=True,
        )
    except Exception as exc:
        # Inventory/Redis ambiguity is fail-closed: no PBX work is derived from
        # this inverse source. Existing durable Redis/DB recovery still runs.
        logger.warning("inverse_ari_recovery_scan_failed err=%s", exc)
    try:
        redis_orphans = await sb.recover_orphans(
            limit=_ORPHAN_RECOVERY_SOURCE_BATCH,
        )
    except Exception as exc:
        logger.warning("recover_orphaned_calls: recover_orphans failed: %s", exc)
        redis_orphans = []

    # PostgreSQL fallback for the exact case Redis cannot represent after its
    # session TTL/data is lost: a live-looking inbound parent with a durable,
    # reserved transfer child. Only the process holding exclusive telephony
    # ownership may claim these rows, and every locally-owned initialization,
    # VoiceSession, and ringing warmup is excluded before the parent is fenced.
    takeover_count = 0
    try:
        ownership_check = getattr(sb, "is_telephony_owner", None)
        exclusive_owner = bool(callable(ownership_check) and ownership_check())
        if exclusive_owner:
            from app.core.container import get_container
            from app.domain.services.telephony.transfer_restart_recovery import (
                claim_inbound_transfer_takeovers,
            )

            container = get_container()
            db_pool = getattr(container, "db_pool", None)
            if getattr(container, "is_initialized", False) and db_pool is not None:
                exclusions = set(_inbound_admissions_in_flight)
                iter_voice = getattr(sb, "iter_voice_session_items", None)
                if callable(iter_voice):
                    exclusions.update(str(call_id) for call_id, _ in iter_voice())
                iter_warmups = getattr(sb, "iter_ringing_warmup_keys", None)
                if callable(iter_warmups):
                    exclusions.update(str(call_id) for call_id in iter_warmups())
                claims = await claim_inbound_transfer_takeovers(
                    db_pool,
                    exclusive_owner_confirmed=True,
                    excluded_provider_call_ids=exclusions,
                )
                takeover_count = len(claims)
                if takeover_count:
                    logger.critical(
                        "transfer_restart_takeover_claimed count=%d",
                        takeover_count,
                    )
    except Exception as exc:
        # No PBX work happened unless the database claim committed its durable
        # termination_pending marker. The next watchdog/startup pass retries.
        logger.warning(
            "recover_orphaned_calls: transfer takeover scan failed: %s",
            exc,
        )
    try:
        pending_rows = await _load_termination_pending_candidates()
    except Exception as exc:
        logger.warning(
            "recover_orphaned_calls: termination_pending scan failed: %s",
            exc,
        )
        pending_rows = []

    # Bound both sources independently, then alternate durable DB and Redis
    # work with DB first. A large/stuck Redis backlog can no longer delay the
    # database-only row that proves a committed reservation needs recovery.
    redis_batch = list(redis_orphans)[:_ORPHAN_RECOVERY_SOURCE_BATCH]
    pending_batch = list(pending_rows)[:_ORPHAN_RECOVERY_SOURCE_BATCH]
    interleaved: list[tuple[str, Mapping[str, Any]]] = []
    for index in range(max(len(redis_batch), len(pending_batch))):
        if index < len(pending_batch):
            interleaved.append(("database", pending_batch[index]))
        if index < len(redis_batch):
            interleaved.append(("redis", redis_batch[index]))

    candidates_by_call: dict[tuple[str, str], dict[str, Any]] = {}
    for source, raw in interleaved:
        entry = dict(raw)
        call_id = str(entry.get("call_id") or "").strip()
        if not call_id:
            continue
        provider = str(entry.get("provider") or adapter_provider).strip().lower()
        entry["call_id"] = call_id
        entry["provider"] = provider
        candidate_key = (provider, call_id)
        existing = candidates_by_call.get(candidate_key)
        if existing is None:
            entry["_has_redis_ledger"] = source == "redis"
            candidates_by_call[candidate_key] = entry
            continue

        if source == "redis":
            existing["_has_redis_ledger"] = True
            existing["pod_id"] = entry.get("pod_id") or existing.get("pod_id")
            continue

        existing["_termination_pending"] = bool(
            existing.get("_termination_pending") or entry.get("_termination_pending")
        )
        existing["_inbound_settlement_pending"] = bool(
            existing.get("_inbound_settlement_pending") or entry.get("_inbound_settlement_pending")
        )
        # The DB candidate is the exact durable authority. Keep the Redis ack
        # flag while replacing stale mirror identity with the selected row.
        existing["durable_call_id"] = entry.get("durable_call_id")
        existing["tenant_id"] = entry.get("tenant_id")

    candidates = list(candidates_by_call.values())
    if not candidates:
        return 0

    recovered = 0
    for entry in candidates:
        # A bounded scan can still outlive the ownership lease. Once authority
        # is lost, do not start another PBX operation; already-completed proof
        # and logical settlement above remain idempotent.
        if callable(ownership_check) and not ownership_check():
            logger.warning("orphan_recovery_stopped_after_ownership_loss")
            break
        call_id = entry.get("call_id")
        if not call_id or call_id in _orphan_recovery_in_flight:
            continue
        # The application inventory and Redis scan can overlap a legitimate
        # local StasisStart/Answer. Never let recovery control a channel the
        # live adapter or lifecycle currently manages. A successor has empty
        # local maps and will still recover a true crash orphan.
        if callable(getattr(active_adapter, "recovery_excluded_channel_ids", None)):
            live_exclusions = _current_recovery_exclusions(sb, active_adapter)
            if live_exclusions is None:
                logger.warning("orphan_recovery_deferred_unverifiable_local_state")
                break
            if str(call_id) in live_exclusions:
                logger.info(
                    "orphan_recovery_deferred_locally_managed call=%s",
                    str(call_id)[:12],
                )
                continue
        _orphan_recovery_in_flight.add(call_id)
        recovery_context: Optional[Dict[str, Any]] = None
        logical_teardown_succeeded = False
        deferred_for_retry = False
        try:
            # Database truth is loaded before touching the PBX. In particular,
            # this distinguishes inbound reservation settlement from outbound
            # dialer completion and supplies connected transfer targets that
            # vanished from adapter memory with the dead process.
            recovery_context = await _hydrate_orphan_recovery_context(
                call_id,
                entry,
            )
            recovered_provider = str(recovery_context.get("provider") or "").strip().lower()
            if recovered_provider not in {adapter_provider, "unknown"}:
                raise RuntimeError(
                    "orphan provider cannot be proved by the active adapter: "
                    f"row={recovered_provider or 'missing'} "
                    f"adapter={adapter_provider or 'missing'}"
                )
            if recovery_context.get("direction") == "inbound":
                # A dead process cannot prove whether provider Answer raced
                # the durable Answer write. Preserve any pre-Answer child
                # reservation until authoritative carrier reconciliation.
                recovery_context["hold_ambiguous_transfer_legs"] = True
            if callable(getattr(active_adapter, "recovery_excluded_channel_ids", None)):
                final_live_exclusions = _current_recovery_exclusions(
                    sb,
                    active_adapter,
                    ignore_recovery_call_id=str(call_id),
                )
                if final_live_exclusions is None:
                    deferred_for_retry = True
                    logger.warning("orphan_recovery_deferred_unverifiable_final_local_state")
                    continue
                if str(call_id) in final_live_exclusions:
                    deferred_for_retry = True
                    logger.info(
                        "orphan_recovery_deferred_new_local_owner call=%s",
                        str(call_id)[:12],
                    )
                    continue
            # Hydration is an awaited database operation. Ownership may expire
            # between the loop-top check and this PBX boundary, so prove it
            # again immediately before issuing any hangup request.
            if callable(ownership_check) and not ownership_check():
                logger.warning("orphan_recovery_stopped_before_pbx_after_owner_loss")
                break
            _orphan_recovery_contexts_by_call[call_id] = recovery_context
            confirmed = await _force_end_and_hangup(
                call_id,
                require_confirmation=True,
                provider_leg_ids=recovery_context.get("provider_leg_ids") or [],
                recovery_context=recovery_context,
                acknowledge_ledger=False,
            )
            if not confirmed:
                deferred_for_retry = True
                logger.warning(
                    "orphan_recovery deferred call=%s prior_owner=%s; "
                    "durable ledger retained for retry",
                    call_id[:12],
                    entry.get("pod_id") or "-",
                )
                continue
            logical_teardown_succeeded = True

            if entry.get("_has_redis_ledger"):
                # This awaited acknowledgement is the recovery commit point.
                # Local session removal never unregisters Redis, so a process
                # crash or cancellation anywhere before this exact await leaves
                # the obligation discoverable by the next successor.
                await sb.acknowledge_orphan_recovery(call_id)
            _orphan_recovery_contexts_by_call.pop(call_id, None)
            recovered += 1
            logger.info(
                "orphan_recovery confirmed call=%s prior_owner=%s tenant=%s",
                call_id[:12],
                entry.get("pod_id"),
                entry.get("tenant_id") or "-",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            deferred_for_retry = True
            # Keep the durable ledger entry. The adapter and logical teardown
            # paths are idempotent, so a later pass can safely converge.
            logger.warning(
                "orphan_recovery attempt_failed call=%s prior_owner=%s err=%s; "
                "durable ledger retained for retry",
                call_id[:12],
                entry.get("pod_id") or "-",
                exc,
            )
        finally:
            if deferred_for_retry:
                durable_call_id = str(
                    (recovery_context or {}).get("durable_call_id")
                    or entry.get("durable_call_id")
                    or ""
                )
                try:
                    await _rotate_deferred_termination_candidate(durable_call_id)
                except Exception as exc:
                    logger.warning(
                        "termination_retry_rotation_failed call=%s err=%s",
                        call_id[:12],
                        exc,
                    )
            if (
                not logical_teardown_succeeded
                and recovery_context is not None
                and recovery_context.get("_logical_marker_acquired")
                and recovery_context.get("_logical_marker_owner_task") is asyncio.current_task()
                and call_id not in _ended_calls_logically_completed
            ):
                # `_on_call_ended` normally suppresses duplicate terminal ARI
                # bursts for ten minutes. A failed recovery settlement is not
                # a completed terminal event; release the local marker now so
                # the next watchdog tick can retry while the durable ledger is
                # still present.
                _ended_calls_in_flight.discard(call_id)
                _ended_calls_logically_completed.discard(call_id)
            _orphan_recovery_in_flight.discard(call_id)
    return recovered


def _pipeline_done_cb(task: asyncio.Task, call_id: str) -> None:
    """
    FIX 3 — Done-callback attached to Asterisk pipeline tasks.

    If start_pipeline() raises an unhandled exception after being fire-and-forgot
    via create_task(), Python logs to stderr but the session stays in
    _telephony_sessions forever.  This callback detects the failure and triggers
    _on_call_ended so the session is cleaned up and the PBX hangs up the channel.

    FIX #1b — this is also the fast path for a terminal STT failure (primary
    Deepgram stream dies AND, when failover is enabled, the secondary also
    fails): AudioIngest.process now re-raises that condition as
    TerminalSTTError instead of swallowing it, so it surfaces here as
    task.exception() within seconds instead of waiting for the ~300s
    inactivity watchdog. Uses _force_end_and_hangup (not bare
    _on_call_ended) so the live Asterisk channel actually gets released —
    a dead pipeline task means nothing else in the call is going to hang
    the channel up.
    """
    if task.cancelled():
        return
    # Fix 14 — a SUPERSEDED pipeline task must never tear the call down. When
    # the realtime→cascaded fallback swaps in a new pipeline_task, the dying
    # realtime task's done-callback still fires; if it's no longer the
    # session's current task, the fallback has taken over — do nothing. (The
    # realtime task also completes without an exception, so the exc branch
    # below would be skipped anyway; this is the explicit, documented guard.)
    try:
        _vs = _state().get_voice_session(call_id)
    except Exception:
        _vs = None
    if _vs is not None and getattr(_vs, "pipeline_task", None) not in (None, task):
        logger.debug(
            "pipeline_done_cb: superseded task for %s (fallback took over) — " "no teardown",
            call_id[:12],
        )
        return
    exc = task.exception()
    if exc:
        logger.error(
            "pipeline_task crashed for %s — triggering session teardown: %s",
            call_id[:12],
            exc,
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
        # P1-9 — retain the task reference (see _track_task) so it can't be
        # GC'd mid-flight and silently drop the forced hangup.
        _track_task(_force_end_and_hangup(call_id))


async def _on_early_ringing(call_id: str) -> None:
    """Carrier reported 180 Ringing for an outbound channel (seconds before
    StasisStart). Status-only hook: advance the live call to RINGING so the
    UI moves "Dialing" → "Ringing" in real time. Never does warmup, never
    raises — a status emit must not touch call setup."""
    try:
        from app.domain.services.call_status import (
            CallState,
            record_call_state_by_provider_id,
        )
        from app.core.container import get_container as _gc_er

        _c = _gc_er()
        if _c.is_initialized:
            await record_call_state_by_provider_id(
                _c.db_pool,
                provider_call_id=call_id,
                new_state=CallState.RINGING,
                metadata={"description": "Ringing"},
            )
    except Exception as _er_exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "call_status.early_ringing_emit_raised call=%s err=%s",
            call_id[:12],
            _er_exc,
        )


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
    _ringing_adapter = get_adapter()
    if _ringing_adapter is None or getattr(_ringing_adapter, "name", "") != "asterisk":
        return
    _sb = _state()
    if (
        _sb.has_ringing_warmup(call_id)
        or _sb.get_ringing_event(call_id) is not None
        or _sb.get_voice_session(call_id) is not None
    ):
        return  # idempotent/reserved — never create a second warmup for a call

    # Track B (live call transparency): the callee's phone is now ringing.
    # Emit RINGING so the live-calls panel advances "Dialing" → "Ringing" in
    # real time instead of sitting on "Dialing" right up until answer. Uses the
    # same provider-id resolver as the ANSWERED/ENDED emits, so it lands on the
    # same calls row. Best-effort — a status emit must never block warmup.
    try:
        from app.domain.services.call_status import (
            CallState,
            record_call_state_by_provider_id,
        )
        from app.core.container import get_container as _gc_ring

        _cr = _gc_ring()
        if _cr.is_initialized:
            await record_call_state_by_provider_id(
                _cr.db_pool,
                provider_call_id=call_id,
                new_state=CallState.RINGING,
                metadata={"description": "Ringing"},
            )
    except Exception as _ring_exc:
        logger.debug(
            "call_status.ringing_emit_raised call=%s err=%s",
            call_id[:12],
            _ring_exc,
        )

    if _sb.voice_session_count() + _sb.ringing_warmup_count() >= _MAX_TELEPHONY_SESSIONS:
        logger.warning(
            "ringing_warmup_skipped_at_capacity call_id=%s",
            call_id[:12],
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
                voice_session.stt_provider.pre_connect(voice_session.call_session.call_id)
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
                                "ringing_warmup_coro[%d] failed: %s",
                                i,
                                r,
                            )

            # Step 2: Build greeting text from session config
            greeting_text = _build_outbound_greeting(voice_session.call_session)

            # Step 3: Synthesize greeting and buffer all audio chunks
            chunks: list[bytes] = []
            _synth_t0 = asyncio.get_event_loop().time()
            try:
                tts_config = voice_session.config
                async for audio_chunk in voice_session.tts_provider.stream_synthesize(
                    text=greeting_text,
                    voice_id=tts_config.voice_id if tts_config else "default",
                    sample_rate=(tts_config.tts_sample_rate if tts_config else 16000),
                    call_id=voice_session.call_id,
                ):
                    raw = audio_chunk.data if hasattr(audio_chunk, "data") else audio_chunk
                    if raw:
                        # Ensure Int16 alignment (2 bytes per sample)
                        if len(raw) % 2 != 0:
                            raw = raw[:-1]
                        if raw:
                            chunks.append(raw)

                _synth_ms = (asyncio.get_event_loop().time() - _synth_t0) * 1000.0
                total_bytes = sum(len(c) for c in chunks)
                logger.info(
                    "WARMUP greeting_presynth_done call_id=%s " "chunks=%d bytes=%d synth_ms=%.0f",
                    call_id[:12],
                    len(chunks),
                    total_bytes,
                    _synth_ms,
                )

                # Store on the voice_session so _send_outbound_greeting can
                # grab them without any dict lookup.
                voice_session._presynth_greeting_audio = chunks
                voice_session._presynth_greeting_text = greeting_text

            except Exception as synth_exc:
                logger.warning(
                    "WARMUP greeting_presynth_failed call_id=%s: %s",
                    call_id[:12],
                    synth_exc,
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
            call_id[:12],
            len(warmup_coros),
            elapsed_ms,
        )
    except Exception as exc:
        logger.error(f"Ringing warmup failed for {call_id[:12]}: {exc}", exc_info=True)
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
    _reject_adapter = get_adapter()
    if _reject_adapter:
        try:
            await _reject_adapter.hangup(call_id)
        except Exception:
            pass


_inbound_admissions_in_flight: Dict[str, Dict[str, Any]] = {}
_inbound_admissions_finalized: set[tuple[str, str]] = set()
_inbound_admissions_pending: set[str] = set()
_inbound_heartbeat_tasks: Dict[str, asyncio.Task] = {}
_inbound_deadline_tasks: Dict[str, asyncio.Task] = {}


def _admission_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    return dict(to_dict()) if callable(to_dict) else {}


async def _release_global_inbound_slot(
    release_callback: Any,
    redis_client: Any,
    pbx_call_id: str,
) -> None:
    """Bound strict slot cleanup and propagate ambiguity to the retry owner."""

    try:
        await asyncio.wait_for(
            release_callback(redis_client, call_id=pbx_call_id),
            timeout=1.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            "inbound_global_lease_release_timed_out call=%s; cleanup ledger retained",
            pbx_call_id[:12],
        )
        raise RuntimeError(f"global inbound lease release timed out for {pbx_call_id}")


async def _admit_inbound_call(
    pbx_call_id: str,
    metadata: Dict[str, Any],
) -> Any:
    """Adapter callback: commit admission while the channel is unanswered."""
    from app.core.container import get_container
    from app.domain.services.telephony.inbound_admission import (
        InboundAdmissionRequest,
        InboundAdmissionService,
    )

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise RuntimeError("database unavailable for inbound admission")

    # A production Redis partition or stolen owner lock fences the adapter's
    # process immediately. Check again on the pre-answer path so a channel
    # that arrived during the fencing window is rejected, never answered.
    state_backend = _state()
    if not state_backend.is_telephony_owner():
        raise RuntimeError("this process is not the active telephony owner")

    # The unanswered PBX channel already exists. Make its cleanup identity the
    # first awaited side effect, before even local/global capacity rejection,
    # so every fail-closed admission outcome has a successor-visible owner.
    await state_backend.register_cleanup_obligation(
        pbx_call_id,
        state="termination_pending",
    )

    # Capacity is evaluated while the Asterisk channel is still unanswered.
    # Count already-admitted/pre-media calls as well as live sessions so a
    # burst cannot queue beyond this pod's memory budget.
    get_voice_session = getattr(state_backend, "get_voice_session", None)
    pre_session_admissions = sum(
        1
        for live_id in _inbound_admissions_in_flight
        if not callable(get_voice_session) or get_voice_session(live_id) is None
    )
    local_load = (
        state_backend.voice_session_count()
        + pre_session_admissions
        + len(_inbound_admissions_pending)
    )
    if local_load >= _MAX_TELEPHONY_SESSIONS:
        raise RuntimeError("pod capacity reached before inbound answer")
    _inbound_admissions_pending.add(pbx_call_id)

    from app.domain.services.global_concurrency import (
        acquire_lease as acquire_global_lease,
        resolve_global_cap,
    )

    try:
        global_lease = await acquire_global_lease(
            getattr(container, "redis", None),
            call_id=pbx_call_id,
            pod_id=os.getenv("POD_ID") or os.getenv("HOSTNAME") or "talky-pod",
            cap=resolve_global_cap(),
            # Unlike outbound origination, true inbound has not been answered
            # yet.  Redis is the cluster-cap authority, so an unavailable or
            # unverifiable lease must reject the channel pre-answer.
            fail_closed=True,
        )
        if not global_lease:
            raise RuntimeError(
                "global capacity unavailable before inbound answer: " f"{global_lease.reason}"
            )
        request = InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id=pbx_call_id,
            called_did=metadata.get("called_did"),
            caller_ani=metadata.get("caller_number"),
            ingress=metadata.get("ingress") or "asterisk",
            context=metadata.get("context"),
            request_id=pbx_call_id,
            # The admission service intersects this absolute ceiling with the
            # campaign's pinned maximum and the tenant's exact remaining
            # quota, then reserves that full enforceable window pre-answer.
            reservation_seconds=14_400,
            metadata={
                "ingress_endpoint": metadata.get("ingress_endpoint"),
                "linked_id": metadata.get("linked_id"),
            },
        )
        decision = await InboundAdmissionService(container.db_pool).admit(request)
        payload = _admission_dict(decision)
        # Enrich the already-durable ledger with DB authority before the
        # adapter can Answer. If this strict write fails, the original generic
        # entry remains retryable and the adapter takes the pre-answer cleanup
        # path; the global slot is deliberately retained until absence proof.
        await state_backend.register_cleanup_obligation(
            pbx_call_id,
            tenant_id=(str(payload.get("tenant_id")) if payload.get("tenant_id") else None),
            campaign_id=(str(payload.get("campaign_id")) if payload.get("campaign_id") else None),
            state="termination_pending",
            durable_call_id=(str(payload.get("call_id")) if payload.get("call_id") else None),
            provider=(str(payload.get("provider")) if payload.get("provider") else "asterisk"),
            provider_call_id=(
                str(payload.get("provider_call_id"))
                if payload.get("provider_call_id")
                else pbx_call_id
            ),
        )
        if bool(payload.get("allowed")):
            _inbound_admissions_in_flight[pbx_call_id] = payload
        return decision
    except asyncio.CancelledError:
        # The adapter still owns the unanswered PBX leg. Releasing capacity
        # here would permit a replacement admission while that channel may be
        # live/billable. Its proof task (or restart recovery) owns both durable
        # settlement and global-slot release.
        raise
    except Exception:
        # Same ownership transfer as cancellation: never release before PBX
        # absence proof, including dependency errors after lease acquisition.
        raise
    finally:
        _inbound_admissions_pending.discard(pbx_call_id)


async def _persist_pre_row_inbound_rejection(
    pbx_call_id: str,
    rejection: Dict[str, Any],
) -> None:
    """Adapter callback: persist a non-billable inbound denial immediately."""

    from app.core.container import get_container
    from app.domain.services.telephony.inbound_admission import InboundAdmissionService

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise RuntimeError("database unavailable for inbound rejection persistence")

    await InboundAdmissionService(container.db_pool).record_pre_row_rejection(
        provider=str(rejection.get("provider") or "asterisk"),
        provider_call_id=str(rejection.get("provider_call_id") or pbx_call_id),
        called_did=rejection.get("called_did"),
        caller_ani=rejection.get("caller_ani"),
        ingress=str(rejection.get("ingress") or "asterisk"),
        reason=str(rejection.get("reason") or "admission_denied"),
    )


_INBOUND_ANSWER_DURABILITY_TIMEOUT_S = 5.0


def _normalise_answered_at_dt(value: Any) -> datetime:
    """Return a timezone-aware UTC ``datetime``.

    asyncpg validates a parameter's PYTHON type against the inferred column
    type before it sends anything, so a ``timestamptz`` placeholder must be
    given a real ``datetime``. Writing ``$4::timestamptz`` in the SQL does not
    help: the cast is applied server-side, long after the client-side type
    check has already rejected a ``str`` with
    "invalid input for query argument $4 ... got 'str'".
    """

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            parsed = datetime.now(timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_answered_at(value: Any) -> str:
    """Return a timezone-aware ISO timestamp for Redis/JSON consumers.

    Kept as the string form because the cleanup-ledger promotion and this
    hook's return value are serialised, not bound as query parameters. Use
    ``_normalise_answered_at_dt`` for anything handed to asyncpg.
    """

    return _normalise_answered_at_dt(value).isoformat()


async def _find_inbound_admission_by_provider_identity(
    container: Any,
    *,
    provider: str,
    provider_call_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve an admission whose callback result was lost after DB commit.

    An adapter timeout can cancel the caller after ``admit`` committed but
    before its payload reached the in-memory cache. PBX absence proof must
    therefore consult PostgreSQL before treating the cleanup as capacity-only.
    """

    from app.core.db_utils import acquire_with_tenant

    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        raise RuntimeError("database unavailable for inbound identity recovery")
    async with acquire_with_tenant(db_pool, None, timeout=5.0) as conn:
        raw = await conn.fetchrow(
            """
            SELECT id, tenant_id, campaign_id, provider, provider_call_id,
                   billing_status, COUNT(*) OVER() AS identity_match_count
            FROM calls
            WHERE direction='inbound'
              AND LOWER(BTRIM(provider))=$1::text
              AND provider_call_id=$2::text
            """,
            provider.strip().lower(),
            provider_call_id,
        )
    if raw is None:
        return None
    row = dict(raw)
    if int(row.pop("identity_match_count", 0) or 0) != 1:
        raise RuntimeError(f"ambiguous inbound provider identity {provider}:{provider_call_id}")
    return {
        "allowed": True,
        "call_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "campaign_id": (str(row["campaign_id"]) if row.get("campaign_id") else None),
        "provider": str(row.get("provider") or provider).strip().lower(),
        "provider_call_id": str(row.get("provider_call_id") or provider_call_id),
        "billing_status": row.get("billing_status"),
    }


async def _persist_inbound_answered(
    pbx_call_id: str,
    admission: Mapping[str, Any],
    *,
    answered_at: Any,
) -> str:
    """Commit provider Answer truth before Asterisk may create media.

    ``POST /answer`` is already an externally committed, billable action when
    this hook runs. PostgreSQL is updated first so any later terminal path
    finalizes rather than releases the reservation. The Redis cleanup ledger
    is then strictly promoted to ``active`` with the same first-answer
    timestamp, making a crash before bridge/session creation recoverable.

    Either dependency failing raises into the adapter. Its provisional
    handoff owner then performs confirmation-aware PBX cleanup and retains the
    durable obligation until settlement succeeds.
    """

    from app.core.container import get_container
    from app.core.db_utils import acquire_with_tenant

    payload = dict(admission or {})
    durable_call_id = str(payload.get("call_id") or "").strip()
    tenant_id = str(payload.get("tenant_id") or "").strip()
    provider_call_id = str(payload.get("provider_call_id") or pbx_call_id).strip()
    if not durable_call_id or not tenant_id or not provider_call_id:
        raise RuntimeError("inbound Answer lacks durable call authority")

    # Bound as $4 against a timestamptz column, so this must be a datetime.
    answer_timestamp = _normalise_answered_at_dt(answered_at)
    container = get_container()
    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        raise RuntimeError("database unavailable for durable inbound Answer")

    async def _commit_answer() -> Mapping[str, Any]:
        async with acquire_with_tenant(
            db_pool,
            tenant_id,
            timeout=_INBOUND_ANSWER_DURABILITY_TIMEOUT_S,
        ) as conn:
            row = await conn.fetchrow(
                """
                UPDATE calls
                   SET answered_at=COALESCE(answered_at,$4::timestamptz),
                       status=CASE
                         WHEN status IS NULL OR status IN (
                           'queued','initiated','dialing','ringing'
                         ) THEN 'answered'
                         ELSE status
                       END,
                       updated_at=NOW()
                 WHERE id=$1::uuid
                   AND tenant_id=$2::uuid
                   AND direction='inbound'
                   AND (provider_call_id=$3 OR external_call_uuid=$3)
                RETURNING status, answered_at
                """,
                durable_call_id,
                tenant_id,
                provider_call_id,
                answer_timestamp,
            )
            if row is None:
                raise RuntimeError("durable inbound call row was not found for Answer")
            return dict(row)

    row = await asyncio.wait_for(
        _commit_answer(),
        timeout=_INBOUND_ANSWER_DURABILITY_TIMEOUT_S,
    )
    persisted_answered_at = row.get("answered_at")
    if persisted_answered_at is None:
        raise RuntimeError("inbound Answer timestamp was not persisted")
    persisted_timestamp = _normalise_answered_at(persisted_answered_at)

    state_backend = _state()
    promote = getattr(
        state_backend,
        "promote_answered_cleanup_obligation",
        None,
    )
    if not callable(promote):
        raise RuntimeError("telephony state backend lacks durable Answer promotion")
    await asyncio.wait_for(
        promote(
            pbx_call_id,
            answered_at=persisted_timestamp,
            tenant_id=tenant_id,
            campaign_id=(str(payload.get("campaign_id")) if payload.get("campaign_id") else None),
        ),
        timeout=_INBOUND_ANSWER_DURABILITY_TIMEOUT_S,
    )
    answered_at_monotonic = payload.get("_answered_at_monotonic")
    if isinstance(answered_at_monotonic, bool) or not isinstance(
        answered_at_monotonic,
        (int, float),
    ):
        raise RuntimeError("durable inbound Answer lacks a monotonic timestamp")
    _start_inbound_runtime_guards(
        pbx_call_id,
        payload,
        answered_at_monotonic=float(answered_at_monotonic),
        max_duration_seconds=_pinned_inbound_max_duration(payload),
    )
    return persisted_timestamp


async def _persist_inbound_terminal_proof(
    pbx_call_id: str,
    admission: Mapping[str, Any],
    *,
    terminated_at: Any,
    duration_seconds: int,
) -> Dict[str, Any]:
    """Persist PBX absence before gateway/media cleanup may delay teardown.

    This is deliberately a projection-only write: billing settlement and
    lease release remain owned by ``_finalize_inbound_admission``.  Recovery
    may trust ``ended_at`` only because the adapter awaits this write at the
    first authoritative parent-leg absence boundary.
    """

    from app.core.container import get_container
    from app.core.db_utils import acquire_with_tenant

    payload = dict(admission or {})
    durable_call_id = str(payload.get("call_id") or "").strip()
    tenant_id = str(payload.get("tenant_id") or "").strip()
    provider_call_id = str(payload.get("provider_call_id") or pbx_call_id).strip()
    if not durable_call_id or not tenant_id or not provider_call_id:
        raise RuntimeError("inbound terminal proof lacks durable call authority")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
        raise RuntimeError("inbound terminal proof duration must be an integer")
    duration = max(0, duration_seconds)
    max_duration = _pinned_inbound_max_duration(payload)
    if duration > max_duration:
        raise RuntimeError("inbound terminal proof exceeds the pinned reservation")
    terminal_timestamp = _normalise_answered_at_dt(terminated_at)

    container = get_container()
    db_pool = getattr(container, "db_pool", None)
    if not getattr(container, "is_initialized", False) or db_pool is None:
        raise RuntimeError("database unavailable for durable inbound terminal proof")
    async with acquire_with_tenant(
        db_pool,
        tenant_id,
        timeout=_INBOUND_ANSWER_DURABILITY_TIMEOUT_S,
    ) as conn:
        row = await conn.fetchrow(
            """
            UPDATE calls
               SET provider_terminated_at=LEAST(
                       COALESCE(provider_terminated_at,$4::timestamptz),
                       $4::timestamptz
                   ),
                   ended_at=LEAST(COALESCE(ended_at,$4::timestamptz),$4::timestamptz),
                   duration_seconds=CASE
                     WHEN answered_at IS NULL THEN 0
                     WHEN provider_terminated_at IS NULL
                       OR $4::timestamptz < provider_terminated_at THEN $5
                     ELSE duration_seconds
                   END,
                   updated_at=NOW()
             WHERE id=$1::uuid
               AND tenant_id=$2::uuid
               AND direction='inbound'
               AND (provider_call_id=$3 OR external_call_uuid=$3)
            RETURNING ended_at, duration_seconds
            """,
            durable_call_id,
            tenant_id,
            provider_call_id,
            terminal_timestamp,
            duration,
        )
    if row is None:
        raise RuntimeError("durable inbound call row was not found for terminal proof")
    persisted_ended_at = row.get("ended_at")
    if persisted_ended_at is None:
        raise RuntimeError("inbound terminal proof timestamp was not persisted")
    return {
        "ended_at": _normalise_answered_at(persisted_ended_at),
        "duration_seconds": max(0, int(row.get("duration_seconds") or 0)),
    }


async def _finalize_inbound_admission(
    pbx_call_id: str,
    admission: Optional[Dict[str, Any]] = None,
    *,
    terminal_status: str,
    duration_seconds: int,
    outcome: Optional[str] = None,
    reason: Optional[str] = None,
    release_only: bool = False,
) -> None:
    """Release/settle one admitted inbound call, idempotently."""
    payload = dict(admission or _inbound_admissions_in_flight.get(pbx_call_id) or {})
    if not payload:
        adapter = get_adapter()
        getter = getattr(adapter, "get_inbound_admission", None)
        if callable(getter):
            payload = dict(getter(pbx_call_id) or {})
    provider = str(payload.get("provider") or "asterisk")
    provider_call_id = str(payload.get("provider_call_id") or pbx_call_id)

    from app.core.container import get_container
    from app.domain.services.telephony.inbound_admission import (
        InboundAdmissionService,
        InboundFinalizationRequest,
    )

    container = get_container()
    durable_call_id = payload.get("call_id")
    if not durable_call_id:
        recovered_payload = await _find_inbound_admission_by_provider_identity(
            container,
            provider=provider,
            provider_call_id=provider_call_id,
        )
        if recovered_payload is not None:
            payload.update(recovered_payload)
            provider = str(payload["provider"])
            provider_call_id = str(payload["provider_call_id"])
            durable_call_id = payload["call_id"]

    dedupe_key = (provider, provider_call_id)
    if dedupe_key in _inbound_admissions_finalized:
        return

    if durable_call_id:
        service = InboundAdmissionService(container.db_pool)
        if release_only:
            await service.release(
                call_id=str(durable_call_id),
                provider=provider,
                provider_call_id=provider_call_id,
                reason=reason or "pre_media_release",
                request_id=pbx_call_id,
            )
        else:
            await service.finalize(
                InboundFinalizationRequest(
                    call_id=str(durable_call_id),
                    provider=provider,
                    provider_call_id=provider_call_id,
                    terminal_status=terminal_status,
                    duration_seconds=max(0, int(duration_seconds or 0)),
                    outcome=outcome,
                    reason=reason,
                    request_id=pbx_call_id,
                )
            )
        # Answer persistence arms the hard deadline before media setup.  A
        # confirmed post-Answer setup failure can therefore settle without
        # ever reaching `_on_new_call`. Keep both guards through a failed
        # durable write, but retire them immediately after release/finalize
        # commits so the heartbeat cannot misread the now-settled lease as a
        # live authority loss.
        await _cancel_inbound_runtime_guards(pbx_call_id)

    # The database settlement/release above is the durable authority.  Only
    # after it succeeds may this call stop counting against the cluster-wide
    # Redis cap; releasing first creates a brief under-count (and permits an
    # over-cap admission) whenever the durable write subsequently fails.
    # ``release_lease`` is idempotent, so this also safely covers paths whose
    # terminal callback already attempted cleanup.
    from app.domain.services.global_concurrency import (
        release_lease_strict as release_global_lease,
    )

    await _release_global_inbound_slot(
        release_global_lease,
        getattr(container, "redis", None),
        pbx_call_id,
    )

    if not durable_call_id:
        # PostgreSQL authoritatively had no row at the proof boundary. This is
        # capacity-only cleanup, not durable settlement. Do not poison the
        # provider dedupe: a late/ambiguous admission commit must remain
        # discoverable by a later recovery pass.
        _inbound_admissions_in_flight.pop(pbx_call_id, None)
        await _cancel_inbound_runtime_guards(pbx_call_id)
        adapter = get_adapter()
        pop_cached = getattr(adapter, "pop_inbound_admission", None)
        if callable(pop_cached):
            pop_cached(pbx_call_id)
        return

    _inbound_admissions_finalized.add(dedupe_key)
    if len(_inbound_admissions_finalized) > 10_000:
        _inbound_admissions_finalized.pop()
    _inbound_admissions_in_flight.pop(pbx_call_id, None)
    adapter = get_adapter()
    pop_cached = getattr(adapter, "pop_inbound_admission", None)
    if callable(pop_cached):
        pop_cached(pbx_call_id)


_INBOUND_LEASE_LOSS_RETRY_S = 5.0
_INBOUND_HEARTBEAT_INTERVAL_S = 30.0
_INBOUND_HEARTBEAT_ERROR_RETRY_S = 10.0
_INBOUND_HEARTBEAT_FAILURE_BUDGET_S = 45.0


def _monotonic_time() -> float:
    return asyncio.get_running_loop().time()


async def _fence_inbound_call_after_lease_loss(
    container: Any,
    *,
    pbx_call_id: str,
    durable_call_id: str,
    admission: Mapping[str, Any],
) -> bool:
    """Persist termination ownership and require all-leg PBX absence proof.

    Once a live admission lease is lost, that call may never resume. The retry
    ledger and ``termination_pending`` row let the watchdog or successor keep
    proving termination if this process exits between attempts.
    """

    tenant_id = str(admission.get("tenant_id") or "").strip() or None
    campaign_id = str(admission.get("campaign_id") or "").strip() or None
    provider_call_id = str(admission.get("provider_call_id") or pbx_call_id)
    provider_leg_ids: tuple[str, ...] = ()

    try:
        await _state().register_cleanup_obligation(
            pbx_call_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            state="termination_pending",
        )
    except Exception as exc:
        # Losing Redis must not suppress the immediate PBX termination request;
        # the database fence below remains the second durable recovery owner.
        logger.critical(
            "inbound_lease_loss_retry_ledger_failed call=%s err=%s",
            pbx_call_id[:12],
            exc,
        )

    try:
        from app.domain.services.telephony.termination import (
            mark_termination_pending_and_load_context,
        )

        context = await mark_termination_pending_and_load_context(
            container.db_pool,
            call_reference=durable_call_id,
            tenant_id=tenant_id,
        )
        provider_call_id = context.provider_call_id or provider_call_id
        provider_leg_ids = context.provider_leg_ids
    except Exception as exc:
        # A database outage is exactly when the provider-side fence is most
        # important. Keep retrying it and let the durable admission/Redis state
        # drive reconciliation when storage recovers.
        logger.critical(
            "inbound_lease_loss_db_fence_failed call=%s durable_call=%s err=%s",
            pbx_call_id[:12],
            durable_call_id[:12],
            exc,
        )

    # Confirmation alone is insufficient: ARI may prove a channel was already
    # absent without delivering a terminal callback. Run the normal idempotent
    # logical finalizer after all-leg proof so local media, billing, tenant and
    # global concurrency state converge in this same ownership path.
    completed = await _force_end_and_hangup(
        provider_call_id,
        require_confirmation=True,
        provider_leg_ids=list(provider_leg_ids),
    )
    if not completed:
        logger.critical(
            "inbound_lease_loss_termination_unconfirmed call=%s",
            pbx_call_id[:12],
        )
    return completed


async def _heartbeat_active_inbound_admission(
    pbx_call_id: str,
    admission: Dict[str, Any],
) -> None:
    """Keep one live inbound lease owned for as long as media is active.

    A missing lease is an authoritative state mismatch, so continuing the call
    would bypass the atomic concurrency and usage reservation. Transient
    database errors are retried only inside a bounded authority budget. Once
    that budget expires, the call is fenced exactly like an explicit lease
    loss; the schema guarantees this budget expires before the shortest legal
    TTL-plus-grace window.
    """

    durable_call_id = admission.get("call_id")
    if not durable_call_id:
        return
    provider = str(admission.get("provider") or "asterisk")
    provider_call_id = str(admission.get("provider_call_id") or pbx_call_id)
    last_success_at = _monotonic_time()
    container: Any = None

    async def _terminate(reason: str) -> None:
        admission["_terminal_reason"] = reason
        session = _state().get_voice_session(pbx_call_id)
        if session is not None:
            session._hangup_reason = reason
        while not await _fence_inbound_call_after_lease_loss(
            container,
            pbx_call_id=pbx_call_id,
            durable_call_id=str(durable_call_id),
            admission=admission,
        ):
            await asyncio.sleep(_INBOUND_LEASE_LOSS_RETRY_S)

    while True:
        try:
            from app.core.container import get_container
            from app.domain.services.telephony.inbound_admission import (
                heartbeat_inbound_call,
            )

            container = get_container()
            refreshed = await heartbeat_inbound_call(
                container.db_pool,
                call_id=str(durable_call_id),
                provider=provider,
                provider_call_id=provider_call_id,
                request_id=f"{pbx_call_id}:heartbeat",
            )
            if refreshed:
                last_success_at = _monotonic_time()
                # The cluster-wide pre-answer lease is keyed by the PBX call
                # id. Refresh it here as well as the durable tenant lease so
                # after-hours transfers (which intentionally have no AI
                # VoiceSession) cannot fall out of the global count.
                from app.domain.services.global_concurrency import refresh_lease

                await refresh_lease(
                    getattr(container, "redis", None),
                    call_id=pbx_call_id,
                )
                # The recording emergency switch is deliberately live, not a
                # call-start snapshot. Each media owner rechecks it on this
                # cluster heartbeat and immediately destroys any buffered
                # caller/agent audio when it is closed.
                from app.domain.services.telephony.modes.caller_first import (
                    _live_inbound_recording_enabled,
                )

                if not await _live_inbound_recording_enabled(container.db_pool):
                    disable_live_inbound_recordings(pbx_call_id)
                # Refresh immediately when the post-answer callback starts,
                # then at a fixed cadence.  Sleeping first left the call with
                # no runtime proof of its durable lease during the slowest
                # provider-initialisation window.
                await asyncio.sleep(_INBOUND_HEARTBEAT_INTERVAL_S)
                continue
            logger.error(
                "inbound_admission_heartbeat_lost call=%s durable_call=%s",
                pbx_call_id[:12],
                str(durable_call_id)[:12],
            )
            # Never return on a request acknowledgement. A false result means
            # one or more PSTN legs may still be live without concurrency or
            # billing authority, so this task becomes a finite-cadence hard
            # termination owner until PBX absence is proven.
            await _terminate("inbound_admission_lease_lost")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "inbound_admission_heartbeat_failed call=%s err=%s",
                pbx_call_id[:12],
                exc,
            )
            if _monotonic_time() - last_success_at >= _INBOUND_HEARTBEAT_FAILURE_BUDGET_S:
                logger.error(
                    "inbound_admission_authority_unverifiable call=%s; "
                    "forcing confirmed termination",
                    pbx_call_id[:12],
                )
                await _terminate("inbound_admission_authority_unverifiable")
                return
            await asyncio.sleep(_INBOUND_HEARTBEAT_ERROR_RETRY_S)


def disable_live_inbound_recordings(pbx_call_id: Optional[str] = None) -> int:
    """Close and clear live inbound recording buffers owned by this process.

    The admin control endpoint invokes this immediately on its worker. Other
    media-owner workers converge through the 30-second admission heartbeat;
    the persistence path independently rechecks the switch before storage.
    """

    call_ids = (
        [pbx_call_id] if pbx_call_id is not None else list(_inbound_admissions_in_flight.keys())
    )
    disabled = 0
    state = _state()
    for call_id in call_ids:
        if call_id not in _inbound_admissions_in_flight:
            continue
        session = state.get_voice_session(call_id)
        if session is None:
            continue
        gateway = getattr(session, "media_gateway", None)
        session_call_id = str(getattr(session, "call_id", "") or call_id)
        set_gate = getattr(gateway, "set_recording_enabled", None)
        if callable(set_gate):
            if set_gate(session_call_id, False):
                disabled += 1
        else:
            clear = getattr(gateway, "clear_recording_buffer", None)
            if callable(clear):
                clear(session_call_id)
                disabled += 1
        session._recording_allowed = False
    if disabled:
        logger.warning(
            "inbound_recording_emergency_stop_applied active_calls=%d",
            disabled,
        )
    return disabled


def _pinned_inbound_max_duration(admission: Mapping[str, Any]) -> int:
    """Read the one quota-backed duration shared by admission and runtime."""

    snapshot = admission.get("config_snapshot")
    route = snapshot.get("route") if isinstance(snapshot, Mapping) else None
    if not isinstance(route, Mapping):
        raise RuntimeError("admitted inbound call has no pinned route")
    duration = route.get("max_call_duration_seconds")
    reservation = route.get("reservation_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, int)
        or not 60 <= duration <= 14_400
        or reservation != duration
    ):
        raise RuntimeError("admitted inbound call has an invalid quota-backed duration")
    return duration


def _confirmed_inbound_duration_seconds(
    admission: Mapping[str, Any],
    terminal_at_monotonic: Any,
) -> int:
    """Measure confirmed Answer to frozen PBX absence, capped to reservation.

    Answer intent is deliberately not billable evidence.  A timeout across the
    ARI Answer boundary is held for carrier adjudication elsewhere and stays at
    zero seconds here instead of growing until restart/cleanup eventually runs.
    """

    answered_at = admission.get("_answered_at_monotonic")
    if isinstance(answered_at, bool) or not isinstance(answered_at, (int, float)):
        return 0
    if isinstance(terminal_at_monotonic, bool) or not isinstance(
        terminal_at_monotonic,
        (int, float),
    ):
        raise InboundTerminalProofMissing(
            "confirmed inbound Answer lacks authoritative terminal proof"
        )
    elapsed = max(
        1,
        math.ceil(max(0.0, float(terminal_at_monotonic) - float(answered_at))),
    )
    return min(elapsed, _pinned_inbound_max_duration(admission))


async def _enforce_inbound_deadline(
    call_id: str,
    max_duration_seconds: int,
    answered_at_monotonic: float,
) -> None:
    """End one admitted call at its reserved deadline, independent of scans."""

    loop = asyncio.get_running_loop()
    delay = max(
        0.0,
        float(max_duration_seconds) - (loop.time() - answered_at_monotonic),
    )
    await asyncio.sleep(delay)
    session = _state().get_voice_session(call_id)
    admission = _inbound_admissions_in_flight.get(call_id)
    if session is None and admission is None:
        return
    if session is not None:
        session._hangup_reason = "inbound_max_duration_reached"
    if admission is not None:
        admission["_terminal_reason"] = "inbound_max_duration_reached"
    logger.warning(
        "inbound_deadline_reached call=%s max_duration_seconds=%d",
        call_id[:12],
        max_duration_seconds,
    )
    # Do not await teardown from the timer stored on the session: teardown
    # cancels all per-call tasks, including this one. Scheduling it separately
    # avoids a task ever trying to cancel/await itself.
    _track_task(_force_end_and_hangup(call_id))


def _start_inbound_runtime_guards(
    call_id: str,
    admission: Dict[str, Any],
    *,
    answered_at_monotonic: float,
    max_duration_seconds: int,
) -> None:
    """Start lease/deadline guards before any after-hours transfer await."""
    admission["_answered_at_monotonic"] = float(answered_at_monotonic)
    _inbound_admissions_in_flight[call_id] = admission
    if call_id not in _inbound_heartbeat_tasks:
        _inbound_heartbeat_tasks[call_id] = asyncio.create_task(
            _heartbeat_active_inbound_admission(call_id, admission),
            name=f"inbound-lease-heartbeat:{call_id}",
        )
    if call_id not in _inbound_deadline_tasks:
        _inbound_deadline_tasks[call_id] = asyncio.create_task(
            _enforce_inbound_deadline(
                call_id,
                max_duration_seconds,
                answered_at_monotonic,
            ),
            name=f"inbound-deadline:{call_id}",
        )


async def _cancel_inbound_runtime_guards(call_id: str) -> None:
    current = asyncio.current_task()
    pending = []
    for registry in (_inbound_heartbeat_tasks, _inbound_deadline_tasks):
        task = registry.pop(call_id, None)
        if task is None or task.done() or task is current:
            continue
        task.cancel()
        pending.append(task)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _pinned_inbound_opening(
    admission_payload: Mapping[str, Any],
) -> tuple[str, Optional[str]]:
    """Return (first_speaker, custom_greeting) from one admitted snapshot."""

    snapshot = admission_payload.get("config_snapshot")
    inbound_cfg = snapshot.get("inbound_config") if isinstance(snapshot, dict) else None
    pinned_mode = admission_payload.get("opening_mode")
    snapshot_mode = inbound_cfg.get("opening_mode") if isinstance(inbound_cfg, dict) else None
    if pinned_mode not in {"caller_first", "agent_first"} or snapshot_mode != pinned_mode:
        raise RuntimeError("admitted inbound call has inconsistent opening_mode")
    greeting = inbound_cfg.get("greeting") if isinstance(inbound_cfg, dict) else None
    return (
        "agent" if pinned_mode == "agent_first" else "user",
        str(greeting).strip() if isinstance(greeting, str) and greeting.strip() else None,
    )


def _pinned_inbound_action(
    admission_payload: Mapping[str, Any],
) -> tuple[str, Optional[str], Optional[str], str]:
    """Return the admitted after-hours action without consulting mutable state.

    The same values are intentionally carried in ``inbound_config`` and
    ``schedule_decision``.  Requiring them to agree detects a partial/corrupt
    snapshot before the runtime answers as the wrong agent or sends a call to
    an unapproved destination.
    """

    snapshot = admission_payload.get("config_snapshot")
    if not isinstance(snapshot, Mapping):
        raise RuntimeError("admitted inbound call is missing its config snapshot")
    inbound_cfg = snapshot.get("inbound_config")
    schedule = snapshot.get("schedule_decision")
    if not isinstance(inbound_cfg, Mapping) or not isinstance(schedule, Mapping):
        raise RuntimeError("admitted inbound call is missing its schedule decision")

    action = inbound_cfg.get("selected_action")
    if action not in {"agent", "hangup", "voicemail", "transfer"}:
        raise RuntimeError("admitted inbound call has an invalid selected_action")
    if schedule.get("selected_action") != action:
        raise RuntimeError("admitted inbound call has inconsistent selected_action")

    destination = inbound_cfg.get("selected_destination")
    schedule_destination = schedule.get("selected_destination")
    if destination != schedule_destination:
        raise RuntimeError("admitted inbound call has inconsistent transfer destination")
    if action == "transfer" and not (isinstance(destination, str) and destination.strip()):
        raise RuntimeError("admitted inbound transfer has no destination")
    if action != "transfer" and destination not in {None, ""}:
        raise RuntimeError("non-transfer inbound action carries a destination")

    message = inbound_cfg.get("after_hours_message")
    if message is not None and not isinstance(message, str):
        raise RuntimeError("admitted inbound call has an invalid after-hours message")

    transfer_policy = inbound_cfg.get("transfer_policy")
    failure_action = "hangup"
    if isinstance(transfer_policy, Mapping):
        candidate = transfer_policy.get("failure_action")
        if candidate in {"voicemail", "return_to_agent", "hangup"}:
            failure_action = str(candidate)
    return (
        str(action),
        str(destination).strip() if isinstance(destination, str) and destination.strip() else None,
        message.strip() if isinstance(message, str) and message.strip() else None,
        failure_action,
    )


def _pinned_inbound_ai_config(
    admission_payload: Mapping[str, Any],
):
    """Rehydrate provider and tuning objects solely from the admitted row."""

    snapshot = admission_payload.get("config_snapshot")
    raw = snapshot.get("tenant_ai_config") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw, Mapping) or not raw.get("id"):
        raise RuntimeError("admitted inbound call has no pinned tenant AI configuration")

    required = (
        "llm_provider",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "stt_provider",
        "stt_model",
        "stt_language",
        "stt_engine",
        "tts_provider",
        "tts_model",
        "tts_voice_id",
        "tts_sample_rate",
        "pipeline_mode",
    )
    missing = [key for key in required if raw.get(key) is None or raw.get(key) == ""]
    if missing:
        raise RuntimeError(
            "admitted inbound call has incomplete pinned AI configuration: " + ",".join(missing)
        )

    from app.domain.models.ai_config import AIProviderConfig
    from app.domain.services.voice_tuning import VoiceTuning, get_voice_tuning_resolver

    ai_fields = {
        key: raw.get(key)
        for key in (
            "llm_provider",
            "llm_model",
            "llm_temperature",
            "llm_max_tokens",
            "stt_provider",
            "stt_model",
            "stt_language",
            "stt_engine",
            "tts_provider",
            "tts_model",
            "tts_voice_id",
            "tts_sample_rate",
            "pipeline_mode",
            "realtime_model",
            "realtime_voice",
            "realtime_settings",
        )
        if raw.get(key) is not None
    }
    try:
        ai_config = AIProviderConfig(**ai_fields)
    except Exception as exc:
        raise RuntimeError("admitted inbound call has invalid pinned AI configuration") from exc

    raw_tuning = raw.get("voice_tuning") or {}
    if not isinstance(raw_tuning, Mapping):
        raise RuntimeError("admitted inbound call has invalid pinned voice tuning")
    tuning_values = get_voice_tuning_resolver().coerce_user_partial(dict(raw_tuning))
    voice_tuning = VoiceTuning(**tuning_values)
    return ai_config, voice_tuning


_TRUE_INBOUND_DIRECTIVE = """\
TRUE INBOUND CALL — THE CALLER CONTACTED THE COMPANY (this overrides any
outbound/cold-call framing below):
- The caller dialed this number. Never say or imply that you called them.
- Answer their direct question first. Then ask at most one relevant question.
- Be a concise, warm inbound representative: understand why they called,
  collect only what is needed, and move to the appropriate approved next step.
- Never claim that booking, transfer, callback, opt-out, or another external
  action succeeded unless the corresponding runtime action confirms it.
- Respect opt-out, safety, wrong-number, privacy, and human-transfer requests
  before qualification or sales goals.
"""

_AFTER_HOURS_VOICEMAIL_DIRECTIVE = """\
AFTER-HOURS AI MESSAGE INTAKE (this overrides sales and qualification stages):
- Tell the caller the team is unavailable and invite one concise message.
- Collect only their name, callback details if they volunteer them, and the
  reason for the call. Ask one question at a time and do not sell or qualify.
- Briefly confirm the message. Never promise when somebody will respond unless
  an approved schedule explicitly says so. Then close the call politely.
"""

_AI_MESSAGE_INTAKE_FALLBACK_GREETING = (
    "We are currently unavailable. Please tell me your name, number, " "and a short message."
)


def _build_pinned_inbound_config(
    admission_payload: Mapping[str, Any],
    *,
    gateway_type: str,
    selected_action: str,
):
    """Build one voice-session config from the immutable admission snapshot."""

    snapshot = admission_payload.get("config_snapshot")
    campaign = snapshot.get("campaign") if isinstance(snapshot, Mapping) else None
    inbound_cfg = snapshot.get("inbound_config") if isinstance(snapshot, Mapping) else None
    if not isinstance(campaign, Mapping) or not isinstance(inbound_cfg, Mapping):
        raise RuntimeError("admitted inbound call has incomplete pinned campaign data")

    from app.domain.services.voice_orchestrator import Direction

    pinned_campaign = apply_qualification_overrides(
        dict(campaign),
        inbound_cfg.get("qualification_config") or {},
    )
    ai_config, voice_tuning = _pinned_inbound_ai_config(admission_payload)
    config = _build_telephony_session_config(
        gateway_type=gateway_type,
        campaign=pinned_campaign,
        direction=Direction.INBOUND,
        voice_tuning_override=voice_tuning,
        ai_config_override=ai_config,
    )
    base_prompt = str(getattr(config, "system_prompt", "") or "").strip()
    directive = _TRUE_INBOUND_DIRECTIVE
    if selected_action == "voicemail":
        directive += "\n" + _AFTER_HOURS_VOICEMAIL_DIRECTIVE
    config.system_prompt = f"{directive}\n\n{base_prompt}" if base_prompt else directive

    # Realtime builds its session instructions before ``VoiceSession`` exists,
    # so its opening policy must travel on the config now.  Deriving this from
    # ``Direction.INBOUND`` inside the orchestrator used to force every inbound
    # realtime call to caller-first, leaving configured agent-first calls and
    # after-hours message intake silent.  Only the immutable admission snapshot
    # is consulted here.
    first_speaker, pinned_greeting = _pinned_inbound_opening(admission_payload)
    if selected_action == "voicemail":
        first_speaker = "agent"
        raw_message = inbound_cfg.get("after_hours_message")
        pinned_greeting = (
            str(raw_message).strip()
            if isinstance(raw_message, str) and raw_message.strip()
            else _AI_MESSAGE_INTAKE_FALLBACK_GREETING
        )
    config.realtime_greet_on_start = first_speaker == "agent"
    config.realtime_opening_greeting = pinned_greeting if config.realtime_greet_on_start else None
    config.realtime_message_intake = selected_action == "voicemail"
    return config, pinned_campaign


async def _on_new_call(call_id: str, inbound_admission: Any = None) -> None:
    """Initialize AI pipeline when a new SIP call arrives."""
    # The adapter captured provider Answer before any bridge/media await.  This
    # callback may arrive much later, so it must preserve that clock rather than
    # silently starting a new billing/deadline clock here.
    setup_reference_monotonic = asyncio.get_running_loop().time()
    state_backend = _state()
    if (
        getattr(state_backend, "strict_ownership_active", False)
        and not state_backend.is_telephony_owner()
    ):
        # This callback can start after Asterisk has answered and created local
        # media but before a VoiceSession is registered.  Hand the exact
        # adapter-owned channel/resources to its cleanup owner; never scan or
        # control channels belonging to another process.
        logger.critical(
            "telephony_new_call_fenced_after_ownership_loss call=%s",
            call_id[:12],
        )
        adapter = get_adapter()
        reject_handoff = getattr(adapter, "reject_pending_inbound_handoff", None)
        if callable(reject_handoff):
            reject_handoff(call_id, reason="ownership_lost_before_registration")
        return
    admission_payload = _admission_dict(inbound_admission)
    if admission_payload and bool(admission_payload.get("allowed")):
        _inbound_admissions_in_flight[call_id] = admission_payload
    is_true_inbound = bool(admission_payload and admission_payload.get("allowed"))

    # The callback runs only after Answer. Arm both safety guards before the
    # first DB/provider await so a slow optional status write cannot create an
    # unbounded, non-heartbeating inbound call. Snapshot validation is purely
    # synchronous and therefore also happens before that first await.
    inbound_selected_action = "agent"
    inbound_selected_destination: Optional[str] = None
    inbound_after_hours_message: Optional[str] = None
    inbound_transfer_failure_action = "hangup"
    effective_max_duration: Optional[int] = None
    if is_true_inbound:
        try:
            (
                inbound_selected_action,
                inbound_selected_destination,
                inbound_after_hours_message,
                inbound_transfer_failure_action,
            ) = _pinned_inbound_action(admission_payload)
            effective_max_duration = _pinned_inbound_max_duration(admission_payload)
            answered_at_monotonic = admission_payload.get("_answered_at_monotonic")
            if isinstance(answered_at_monotonic, bool) or not isinstance(
                answered_at_monotonic,
                (int, float),
            ):
                raise RuntimeError("admitted inbound call lacks confirmed Answer time")
            setup_reference_monotonic = float(answered_at_monotonic)
            _start_inbound_runtime_guards(
                call_id,
                admission_payload,
                answered_at_monotonic=float(answered_at_monotonic),
                max_duration_seconds=effective_max_duration,
            )
        except Exception as exc:
            admission_payload["_terminal_reason"] = "invalid_admission_snapshot"
            logger.error(
                "inbound_runtime_guard_start_failed call=%s err=%s",
                call_id[:12],
                exc,
            )
            await _cancel_inbound_runtime_guards(call_id)
            adapter = get_adapter()
            reject_handoff = getattr(
                adapter,
                "reject_pending_inbound_handoff",
                None,
            )
            if callable(reject_handoff):
                reject_handoff(call_id, reason="invalid_admission_snapshot")
            return

        # Admission's strict pre-answer entry remains termination-pending until
        # this post-Answer callback has installed its runtime guards. Promote
        # it before any optional status/provider await (notably the bounded
        # after-hours transfer) so a healthy owner's watchdog does not race a
        # legitimate initialization, while a process restart still exposes
        # the now-stale active entry immediately.
        await state_backend.register_cleanup_obligation(
            call_id,
            tenant_id=(
                str(admission_payload.get("tenant_id"))
                if admission_payload.get("tenant_id")
                else None
            ),
            campaign_id=(
                str(admission_payload.get("campaign_id"))
                if admission_payload.get("campaign_id")
                else None
            ),
            state="active",
        )

    # Track B (live call transparency): the remote side just picked up.
    # Emit ANSWERED so the live-calls panel flips from "ringing" to a
    # green "in-call" pill. Pre-pipeline start so the UI updates fast.
    # Best-effort — never block call setup on a status emit.
    try:
        from app.domain.services.call_status import (
            CallState,
            record_call_state_by_provider_id,
        )
        from app.core.container import get_container as _gc

        _c = _gc()
        await asyncio.wait_for(
            record_call_state_by_provider_id(
                _c.db_pool,
                provider_call_id=call_id,
                new_state=CallState.ANSWERED,
                metadata={"description": "Call answered"},
            ),
            timeout=1.0,
        )
    except asyncio.CancelledError:
        if is_true_inbound:
            # Before the adapter's explicit acceptance callback, it still owns
            # PBX/media cleanup and durable release. Lifecycle cancellation may
            # only unwind the heartbeat/deadline tasks it created; finalizing
            # here would pop the admission before adapter cleanup can claim its
            # bridge/external-media/RTP resources.
            await asyncio.shield(_cancel_inbound_runtime_guards(call_id))
        raise
    except Exception as exc:
        logger.debug("call_status.answered_emit_raised call=%s err=%s", call_id[:12], exc)

    # Per-pod cap (existing, kept as a backstop so a single pod never
    # exceeds its MAX_TELEPHONY_SESSIONS memory budget). The global cap
    # below is the new cluster-wide check (T1.2).
    _pod_session_count = _state().voice_session_count()
    if _pod_session_count >= _MAX_TELEPHONY_SESSIONS:
        logger.error(
            "telephony_at_pod_capacity sessions=%d call_id=%s — rejecting",
            _pod_session_count,
            call_id[:12],
        )
        if is_true_inbound:
            await _cancel_inbound_runtime_guards(call_id)
            adapter = get_adapter()
            reject_handoff = getattr(
                adapter,
                "reject_pending_inbound_handoff",
                None,
            )
            if callable(reject_handoff):
                reject_handoff(call_id, reason="pod_capacity")
            return
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
    lease = True
    if not is_true_inbound:
        lease = await acquire_lease(
            redis_client,
            call_id=call_id,
            pod_id=os.getenv("POD_ID") or os.uname().nodename,
            cap=resolve_global_cap(),
        )
    if not lease:
        logger.error(
            "telephony_at_global_capacity call_id=%s current=%s — rejecting",
            call_id[:12],
            lease.current,
        )
        await _reject_overcap_call(call_id)
        return

    _sb = _state()
    voice_session = None
    logger.info(
        "BRIDGE new_call %s (ringing_warmup_available=%s)",
        call_id[:12],
        _sb.has_ringing_warmup(call_id),
    )
    try:
        orchestrator = _get_orchestrator()

        if is_true_inbound:
            # ``hangup`` is normally consumed by the Asterisk adapter before
            # Answer. Reaching lifecycle means an adapter/race path escaped
            # that gate; preserve the policy by ending immediately.
            if inbound_selected_action == "hangup":
                adapter = get_adapter()
                reject_handoff = getattr(
                    adapter,
                    "reject_pending_inbound_handoff",
                    None,
                )
                if callable(reject_handoff):
                    reject_handoff(call_id, reason="pinned_hangup_action")
                return

            if inbound_selected_action == "transfer":
                from app.core.container import get_container as _transfer_container
                from app.domain.services.telephony.inbound_transfer import (
                    InboundTransferError,
                    authorize_inbound_transfer,
                    complete_inbound_transfer,
                )

                transfer_result: Dict[str, Any] = {"status": "failed"}
                transfer_attempt = None
                transfer_exception: Optional[BaseException] = None
                provider_transfer_started = False
                container_for_transfer = _transfer_container()
                operation_key = (
                    "after-hours-"
                    + hashlib.sha256(
                        (f"{call_id}\x00{str(inbound_selected_destination)}").encode("utf-8")
                    ).hexdigest()
                )
                try:
                    transfer_attempt = await authorize_inbound_transfer(
                        container_for_transfer.db_pool,
                        call_reference=call_id,
                        destination=str(inbound_selected_destination),
                        mode="blind",
                        source="after_hours",
                        redis_client=getattr(container_for_transfer, "redis", None),
                        idempotency_key=operation_key,
                        actor_role="system",
                        actor_type="service",
                    )
                    if getattr(transfer_attempt, "is_replay", False):
                        replay_result = getattr(transfer_attempt, "replay_result", None)
                        transfer_result = (
                            dict(replay_result)
                            if isinstance(replay_result, dict)
                            else {
                                "status": "cleanup_pending",
                                "reason": "idempotent_transfer_in_progress",
                            }
                        )
                    else:
                        adapter = get_adapter()
                        if adapter is None or not adapter.connected:
                            transfer_result = {
                                "status": "failed",
                                "error": "adapter_unavailable",
                                "target_termination_confirmed": True,
                                "caller_media_retained": True,
                            }
                        else:
                            provider_transfer_started = True
                            transfer_result = await adapter.transfer(
                                call_id,
                                str(transfer_attempt.destination),
                                "blind",
                                provider_leg_id=str(
                                    getattr(
                                        transfer_attempt,
                                        "provider_leg_id",
                                        "",
                                    )
                                    or ""
                                ),
                            )
                except InboundTransferError as exc:
                    transfer_result = {
                        "status": "failed",
                        "error": exc.code,
                        "message": exc.message,
                        # Authorization failed before any PBX target could be
                        # created; returning to the still-owned caller is safe.
                        "target_termination_confirmed": True,
                        "caller_media_retained": True,
                    }
                except BaseException as exc:  # cancellation/provider uncertainty
                    transfer_exception = exc
                    transfer_result = (
                        {
                            "status": "cleanup_pending",
                            "reason": "adapter_exception",
                            "error": type(exc).__name__,
                            "provider_leg_id": getattr(
                                transfer_attempt,
                                "provider_leg_id",
                                None,
                            ),
                        }
                        if provider_transfer_started
                        else {
                            "status": "failed",
                            "error": type(exc).__name__,
                            "target_termination_confirmed": True,
                            "caller_media_retained": True,
                        }
                    )

                transfer_result = dict(transfer_result or {})
                if transfer_attempt is not None:
                    transfer_result.setdefault(
                        "attempt_id", getattr(transfer_attempt, "leg_id", None)
                    )
                    transfer_result.setdefault("idempotency_key", operation_key)

                transfer_succeeded = str(transfer_result.get("status") or "").lower() in {
                    "success",
                    "completed",
                    "transferred",
                    "ok",
                }
                if transfer_attempt is not None:
                    try:
                        await complete_inbound_transfer(
                            container_for_transfer.db_pool,
                            attempt=transfer_attempt,
                            succeeded=transfer_succeeded,
                            result=transfer_result,
                            redis_client=getattr(container_for_transfer, "redis", None),
                        )
                    except Exception as exc:
                        logger.error(
                            "inbound_transfer_outcome_persist_failed call=%s success=%s err=%s",
                            call_id[:12],
                            transfer_succeeded,
                            exc,
                        )
                        # Never expose/accept a provider handoff whose durable
                        # child state and idempotency response did not commit.
                        raise RuntimeError("after-hours transfer outcome was not durable") from exc
                if transfer_exception is not None:
                    raise transfer_exception

                result_status = str(transfer_result.get("status") or "").strip().lower()
                if result_status in {
                    "cleanup_pending",
                    "unconfirmed",
                    "termination_unconfirmed",
                    "in_progress",
                }:
                    # The adapter/lifecycle exception owner will fence the
                    # provisional inbound parent. Continuing into AI/message
                    # intake could overlap an unproved PSTN target.
                    raise RuntimeError("after-hours transfer cleanup remains unconfirmed")
                if transfer_succeeded:
                    admission_payload["_transfer_connected"] = True
                    _inbound_admissions_in_flight[call_id] = admission_payload
                    # The strict active ledger was already promoted before the
                    # transfer await, covering a crash anywhere in its bounded
                    # answer/proof window. Do not rely on a post-success write.
                    accept_handoff = getattr(
                        adapter,
                        "accept_inbound_handoff",
                        None,
                    )
                    if not callable(accept_handoff) or not accept_handoff(call_id):
                        raise RuntimeError(
                            "after-hours transfer lifecycle ownership was not accepted"
                        )
                    logger.info(
                        "inbound_after_hours_transfer_connected call=%s destination=approved",
                        call_id[:12],
                    )
                    return

                logger.warning(
                    "inbound_after_hours_transfer_failed call=%s failure_action=%s error=%s",
                    call_id[:12],
                    inbound_transfer_failure_action,
                    transfer_result.get("error") or transfer_result.get("status"),
                )
                if inbound_transfer_failure_action == "hangup":
                    adapter = get_adapter()
                    reject_handoff = getattr(
                        adapter,
                        "reject_pending_inbound_handoff",
                        None,
                    )
                    rejected = bool(
                        callable(reject_handoff)
                        and reject_handoff(
                            call_id,
                            reason="after_hours_transfer_failed",
                        )
                    )
                    if not rejected:
                        raise RuntimeError("after-hours failed transfer could not be fenced")
                    return
                if not (
                    transfer_result.get("target_termination_confirmed")
                    and transfer_result.get("caller_media_retained")
                ):
                    raise RuntimeError("after-hours transfer fallback lacks provider proof")
                inbound_selected_action = (
                    "voicemail" if inbound_transfer_failure_action == "voicemail" else "agent"
                )

        # Select the correct media gateway based on the active PBX adapter:
        #   - Asterisk path: TelephonyMediaGateway (HTTP callbacks, no WebSocket)
        #   - FreeSWITCH path: BrowserMediaGateway (mod_audio_fork WebSocket)
        is_asterisk = bool(get_adapter() and get_adapter().name == "asterisk")
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
                    _wait_ms = (
                        asyncio.get_running_loop().time() - setup_reference_monotonic
                    ) * 1000.0
                    logger.info(
                        "BRIDGE ringing_warmup_consumed call_id=%s wait_ms=%.0f",
                        call_id[:12],
                        _wait_ms,
                    )
            _sb.pop_ringing_event(call_id)  # clean up event
        connect_task: Optional[asyncio.Task] = None
        inbound_campaign_row = None
        if pre is not None:
            voice_session, connect_task = pre  # type: ignore[assignment]
        elif is_true_inbound:
            # The adapter already admitted this channel before answering. Use
            # only that pinned snapshot; never re-query a "latest" campaign or
            # fall through to the process-default agent for true inbound.
            tenant_id = admission_payload.get("tenant_id")
            campaign_id = admission_payload.get("campaign_id")
            snapshot = admission_payload.get("config_snapshot")
            inbound_campaign_row = snapshot.get("campaign") if isinstance(snapshot, dict) else None
            if not tenant_id or not campaign_id or not isinstance(inbound_campaign_row, dict):
                raise RuntimeError("admitted inbound call is missing its pinned campaign snapshot")
            config, inbound_campaign_row = _build_pinned_inbound_config(
                admission_payload,
                gateway_type=gateway_type,
                selected_action=inbound_selected_action,
            )
            voice_session = await orchestrator.create_voice_session(config)
            logger.info(
                "inbound_session_from_admission call=%s tenant=%s campaign=%s "
                "route_version=%s config_version=%s replay=%s",
                call_id[:12],
                str(tenant_id)[:8],
                str(campaign_id)[:8],
                admission_payload.get("route_version"),
                admission_payload.get("config_version"),
                bool(admission_payload.get("is_replay")),
            )
        else:
            # Non-inbound slow path (outbound warmup unavailable). Preserve its
            # historical process-default configuration.
            config = _build_telephony_session_config(gateway_type=gateway_type)
            voice_session = await orchestrator.create_voice_session(config)

        _sb.set_voice_session(call_id, voice_session)

        # True inbound first-speaker comes only from the admitted, pinned
        # configuration.  Do not infer it from channel context or global
        # outbound defaults.
        if is_true_inbound:
            if effective_max_duration is None:
                raise RuntimeError("admitted inbound call has no runtime duration")
            voice_session._max_call_duration_seconds = effective_max_duration
            voice_session._soft_call_cap_seconds = 0
            voice_session._inbound_deadline_task = _inbound_deadline_tasks.get(call_id)
            voice_session._inbound_heartbeat_task = _inbound_heartbeat_tasks.get(call_id)
            inbound_first_speaker, inbound_greeting = _pinned_inbound_opening(admission_payload)
            snapshot = admission_payload.get("config_snapshot")
            inbound_cfg = snapshot.get("inbound_config") if isinstance(snapshot, dict) else {}
            consent_message = (
                inbound_cfg.get("consent_message") if isinstance(inbound_cfg, dict) else None
            )
            if inbound_selected_action == "voicemail":
                inbound_first_speaker = "agent"
                inbound_greeting = (
                    inbound_after_hours_message or _AI_MESSAGE_INTAKE_FALLBACK_GREETING
                )
            voice_session._first_speaker = inbound_first_speaker
            voice_session._pinned_inbound_greeting = inbound_greeting
            voice_session._inbound_greeting = inbound_greeting
            voice_session._call_direction = "inbound"
            voice_session._inbound_selected_action = inbound_selected_action
            voice_session._inbound_selected_destination = inbound_selected_destination
            voice_session._inbound_transfer_failure_action = inbound_transfer_failure_action
            if isinstance(consent_message, str) and consent_message.strip():
                voice_session._recording_disclosure_text_override = consent_message.strip()
            qualification = (
                inbound_cfg.get("qualification_config") if isinstance(inbound_cfg, dict) else {}
            )
            if isinstance(qualification, Mapping):
                silence_timeout = qualification.get("silence_timeout_seconds")
                if isinstance(silence_timeout, (int, float)) and 3 <= silence_timeout <= 60:
                    voice_session._silence_timeout_seconds = float(silence_timeout)
            _cs0 = getattr(voice_session, "call_session", None)
            if _cs0 is not None:
                _cs0._first_speaker = inbound_first_speaker
                _cs0._pinned_inbound_greeting = inbound_greeting
                _cs0._inbound_greeting = inbound_greeting
                _cs0._call_direction = "inbound"
                _cs0._enable_silence_monitor = True
                if hasattr(voice_session, "_silence_timeout_seconds"):
                    _cs0._silence_timeout_seconds = voice_session._silence_timeout_seconds

        # Ensure the per-call first-speaker is on the session for the greeting
        # decision below. The pre-warm-consumed path already carries it; the
        # fresh-session FALLBACK above does NOT (first_speaker isn't in the
        # rebuilt config), so a caller-first call would wrongly play the agent
        # greeting. Apply the value stashed at make_call (idempotent — same
        # value on the pre-warm path).
        try:
            _fs = None if is_true_inbound else _sb.get_first_speaker(call_id)
            if _fs:
                voice_session._first_speaker = _fs
                _cs = getattr(voice_session, "call_session", None)
                if _cs is not None:
                    _cs._first_speaker = _fs
        except Exception:
            pass

        # ── Bind dialer calls.id for campaign transcript persist ────────
        # Admission created the inbound calls row before Asterisk answered, so
        # true inbound consumes that exact identity and pinned snapshot. Never
        # create a second, best-effort inbound row after answer.
        try:
            from app.core.container import get_container as _gc

            _c = _gc()
            if is_true_inbound:
                durable_call_id = str(admission_payload["call_id"])
                voice_session._dialer_call_id = durable_call_id
                voice_session._dialer_tenant_id = str(admission_payload["tenant_id"])
                voice_session._dialer_campaign_id = str(admission_payload["campaign_id"])
                voice_session._dialer_phone = admission_payload.get("caller_ani")
                voice_session._is_true_inbound = True
                voice_session._inbound_admission = dict(admission_payload)
                _call_session = getattr(voice_session, "call_session", None)
                if _call_session is not None:
                    _call_session._dialer_call_id = durable_call_id
                    _call_session._dialer_tenant_id = voice_session._dialer_tenant_id
                    _call_session._dialer_campaign_id = voice_session._dialer_campaign_id

                # Knowledge content itself is captured during pre-answer
                # admission.  Never re-read campaign nodes here: a mid-call KB
                # edit must affect only the next admitted call.
                try:
                    from app.services.scripts.knowledge.session_inject import (
                        apply_pinned_campaign_knowledge,
                    )

                    _knowledge_snapshot = (
                        snapshot.get("knowledge_snapshot") if isinstance(snapshot, dict) else None
                    )
                    if _call_session is not None:
                        apply_pinned_campaign_knowledge(
                            _call_session,
                            _knowledge_snapshot,
                        )
                        _realtime_bridge = getattr(voice_session, "realtime_bridge", None)
                        if _realtime_bridge is not None:
                            _realtime_bridge._knowledge_snapshot_nodes = getattr(
                                _call_session,
                                "_knowledge_snapshot_nodes",
                                None,
                            )
                except Exception as _kb_exc:
                    raise RuntimeError("failed to apply pinned inbound knowledge") from _kb_exc
            elif _c.is_initialized:
                # Preserve the existing outbound association path.
                await bind_telephony_call(
                    voice_session=voice_session,
                    pbx_channel_id=call_id,
                    db_client=_c.db_client,
                )
        except Exception as _bind_exc:
            if is_true_inbound:
                raise RuntimeError("failed to bind admitted inbound call") from _bind_exc
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
            gateway_session_id = getattr(get_adapter(), "_gateway_sessions", {}).get(call_id)
            if gateway_session_id:
                _sb.set_call_id_for_gateway_session(gateway_session_id, call_id)

            await voice_session.media_gateway.on_call_started(
                voice_session.call_id,
                {
                    "adapter": get_adapter(),
                    "pbx_call_id": call_id,
                    # True inbound starts with both recording buffers closed.
                    # Only the shared disclosure/policy helper may open them.
                    "recording_enabled": not is_true_inbound,
                },
            )
            # ── Caller-first 2-second greeting timer (parallel with setup) ──
            # The pre-synthesized greeting audio was prepared during the
            # ringing phase (prepare_pre_originate_greeting). The media
            # gateway is now registered, which is the only thing the
            # audio pump needs — pipeline_task / connect_task / etc. can
            # start in parallel.
            #
            # Anchoring setup telemetry on the confirmed Answer clock and spawning the task
            # here (instead of after pipeline_task creation) ensures the
            # greeting fires at exactly t=2.0s from answer, regardless of
            # any variance in downstream setup. The previous late-spawn
            # gave 2s + setup_time perceived delay, with jitter equal to
            # the variance in setup duration.
            _early_first_speaker = resolve_first_speaker(voice_session)
            if _early_first_speaker == "user":
                # Caller-speaks-first: the caller opens, so play NO auto/pre-
                # recorded intro. The agent waits and responds naturally on the
                # first caller turn (handle_turn_end). Previously a 2s-delayed
                # greeting fired here, which collided with that natural response
                # (two things talking over each other). Agent-first still greets
                # immediately (see below).
                logger.info(
                    "caller_first_no_greeting call=%s — waiting for caller, no auto intro",
                    voice_session.call_id[:12],
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
                        call_id[:12],
                        len(early_chunks),
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
        # Realtime sessions already opened their single speech-to-speech socket
        # in create_voice_session; there are no separate STT/LLM/TTS providers
        # to warm (they are None). Skip the cascaded provider warmup entirely.
        _is_realtime = getattr(voice_session, "realtime_bridge", None) is not None
        if _is_realtime:
            pass
        elif connect_task is not None:
            try:
                results = await asyncio.wait_for(connect_task, timeout=1.0)
                if isinstance(results, list):
                    for i, r in enumerate(results):
                        if isinstance(r, Exception):
                            logger.warning(
                                "telephony_ringing_warmup[%d] failed (non-fatal): %s",
                                i,
                                r,
                            )
                _warmup_ms = (
                    asyncio.get_running_loop().time() - setup_reference_monotonic
                ) * 1000.0
                logger.info(
                    "BRIDGE telephony_warmup_done call_id=%s source=ringing await_ms=%.0f",
                    call_id[:12],
                    _warmup_ms,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "telephony_ringing_warmup_slow call_id=%s — providers will "
                    "complete handshake on first use",
                    call_id[:12],
                )
        else:
            warmup_coros = []
            _tts_connect = getattr(voice_session.tts_provider, "connect_for_call", None)
            if _tts_connect is not None:
                warmup_coros.append(_tts_connect(voice_session.call_id))
            if hasattr(voice_session.stt_provider, "pre_connect"):
                warmup_coros.append(
                    voice_session.stt_provider.pre_connect(voice_session.call_session.call_id)
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
                _warmup_ms = (
                    asyncio.get_running_loop().time() - setup_reference_monotonic
                ) * 1000.0
                logger.info(
                    "BRIDGE telephony_warmup_done call_id=%s source=answer "
                    "warmups=%d warmup_ms=%.0f",
                    call_id[:12],
                    len(warmup_coros),
                    _warmup_ms,
                )

        if is_true_inbound:
            # Compliance ordering: caller RTP may already be feeding STT, but
            # the media gateway's recording buffers remain closed until the
            # shared disclosure completes and tenant policy is re-checked.
            # This is intentionally before the pipeline starts, so the agent
            # cannot answer queued caller speech ahead of the notice.
            from app.domain.services.telephony.modes.caller_first import (
                prepare_inbound_recording,
            )

            await prepare_inbound_recording(voice_session)

        if is_asterisk:
            # Start the voice pipeline. Two modes:
            #   realtime  — a single OpenAI gpt-realtime-2 speech-to-speech
            #               bridge (pipeline_mode=="realtime"); it pumps caller
            #               audio ↔ model audio over the SAME media gateway.
            #   cascaded  — the classic STT → LLM → TTS loop (default).
            # The media gateway and gateway_session mapping were already
            # registered above, so any caller audio that arrived during warmup
            # is waiting in input_queue.
            if getattr(voice_session, "realtime_bridge", None) is not None:
                # Fix 14 — wire the mid-call connection-loss fallback BEFORE the
                # bridge starts. Gated on REALTIME_FALLBACK_ENABLED: when off we
                # wire nothing, so a socket drop keeps today's behaviour. The
                # closure captures THIS call's PBX call_id + session (the bridge
                # only knows its internal uuid), which the recovery handler needs
                # to look the session up in the state backend.
                if _realtime_fallback_enabled():
                    _rt_bridge = voice_session.realtime_bridge
                    _rt_session = voice_session
                    _rt_call_id = call_id
                    _set_hook = getattr(_rt_bridge, "set_on_connection_lost", None)
                    if callable(_set_hook):
                        _set_hook(lambda: _on_realtime_connection_lost(_rt_call_id, _rt_session))
                voice_session.pipeline_task = asyncio.create_task(
                    voice_session.realtime_bridge.run()
                )
                logger.info(
                    "BRIDGE realtime_pipeline_started call_id=%s",
                    call_id[:12],
                )
            else:
                voice_session.pipeline_task = asyncio.create_task(
                    voice_session.pipeline.start_pipeline(voice_session.call_session, None)
                )
            # FIX 3 — attach done-callback so a crash inside start_pipeline triggers
            # _on_call_ended rather than leaving a silent dead session.
            voice_session.pipeline_task.add_done_callback(lambda t: _pipeline_done_cb(t, call_id))
            _pipeline_start_ms = (
                asyncio.get_running_loop().time() - setup_reference_monotonic
            ) * 1000.0
            logger.info(
                "BRIDGE pipeline_started call_id=%s total_setup_ms=%.0f source=%s",
                call_id[:12],
                _pipeline_start_ms,
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
            # Realtime mode owns its own turn-taking + greeting (the model
            # greets per its instructions when the bridge triggers the first
            # response). Skip the cascaded TTS greeting path entirely — it uses
            # voice_session.pipeline, which is None for realtime sessions.
            if getattr(voice_session, "realtime_bridge", None) is not None:
                pass
            elif first_speaker == "agent":
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
        if get_adapter():
            await get_adapter().start_audio_stream(call_id)

        _total_init_ms = (
            asyncio.get_running_loop().time() - setup_reference_monotonic
        ) * 1000.0
        logger.info(
            "BRIDGE ai_pipeline_initialized call_id=%s total_init_ms=%.0f",
            call_id[:12],
            _total_init_ms,
        )

        if is_true_inbound:
            # Registration above is provisional while providers, media and the
            # pipeline still have cancellable work.  Acceptance is literally
            # the final operation in this callback: once the adapter observes
            # it, no later statement can fail or create another resource.
            handoff_adapter = get_adapter()
            accept_handoff = getattr(
                handoff_adapter,
                "accept_inbound_handoff",
                None,
            )
            if callable(accept_handoff) and not accept_handoff(call_id):
                raise RuntimeError("inbound adapter refused lifecycle ownership acceptance")
    except asyncio.CancelledError:
        if is_true_inbound:
            await asyncio.shield(_cancel_inbound_runtime_guards(call_id))
            cancellation_state = _state()
            registered = cancellation_state.pop_voice_session(call_id)
            cancellation_state.remove_gateway_sessions_for_call(call_id)
            orphan = registered or voice_session
            if orphan is not None:
                try:
                    await asyncio.shield(_get_orchestrator().end_session(orphan))
                except Exception:
                    pass
        raise
    except Exception as exc:
        logger.error(f"Failed to initialize AI pipeline for {call_id[:12]}: {exc}", exc_info=True)
        # GAP 3 — Error-path hangup: tell the PBX to release the channel so
        # the caller doesn't hear silence indefinitely.  Tear down the
        # half-initialised session (pre-warmed or otherwise) directly — the
        # PBX hangup will fire _on_call_ended, but end_session() is idempotent
        # and running it here guards against cases where the hangup path
        # silently drops the StasisEnd event.
        failure_state = _state()
        orphan = failure_state.pop_voice_session(call_id)
        failure_state.remove_gateway_sessions_for_call(call_id)
        if orphan is not None:
            for _task_attr in (
                "_inbound_heartbeat_task",
                "_inbound_deadline_task",
            ):
                _orphan_task = getattr(orphan, _task_attr, None)
                if _orphan_task is not None and not _orphan_task.done():
                    _orphan_task.cancel()
                    await asyncio.gather(_orphan_task, return_exceptions=True)
            try:
                await _get_orchestrator().end_session(orphan)
            except Exception:
                pass
        if is_true_inbound:
            await _cancel_inbound_runtime_guards(call_id)
            failure_adapter = get_adapter()
            reject_handoff = getattr(
                failure_adapter,
                "reject_pending_inbound_handoff",
                None,
            )
            if callable(reject_handoff):
                # Before explicit acceptance the adapter still owns the PBX,
                # gateway, bridge and admission payload.  Do not finalize (and
                # therefore pop) that admission here: StasisEnd may beat the
                # hangup response and needs the cached payload to prove every
                # deterministic media resource absent before releasing it.
                # The adapter's fenced cleanup invokes the release-only
                # finalizer after that proof, so there is still one durable
                # settlement owner and no recording/media leak window.
                if not reject_handoff(
                    call_id,
                    reason="pipeline_initialization_failed",
                ):
                    logger.critical(
                        "inbound_initialization_cleanup_unclaimed call=%s",
                        call_id[:12],
                    )
                return

            # Adapters without the explicit provisional-handoff contract keep
            # the legacy lifecycle-owned settlement path.
            try:
                await _finalize_inbound_admission(
                    call_id,
                    admission_payload,
                    terminal_status="failed",
                    duration_seconds=0,
                    reason="pipeline_initialization_failed",
                )
            except Exception as finalize_exc:
                logger.error(
                    "inbound_initialization_finalize_failed call=%s err=%s",
                    call_id[:12],
                    finalize_exc,
                )
        if get_adapter():
            try:
                await get_adapter().hangup(call_id)
            except Exception:
                pass


async def _on_transfer_provider_identity_persisted(
    parent_provider_call_id: str,
    planned_provider_leg_id: str,
    actual_provider_leg_id: str,
    tenant_id: str,
    durable_call_id: str,
) -> str:
    """Bind an ARI returned channel ID before the adapter may dial it."""

    from app.core.container import get_container
    from app.domain.services.telephony.transfer_provider_identity import (
        persist_asterisk_transfer_provider_identity,
    )

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise RuntimeError("database unavailable for transfer identity persistence")
    identity = await persist_asterisk_transfer_provider_identity(
        container.db_pool,
        tenant_id=tenant_id,
        durable_call_id=durable_call_id,
        parent_provider_call_id=parent_provider_call_id,
        planned_provider_leg_id=planned_provider_leg_id,
        actual_provider_leg_id=actual_provider_leg_id,
    )
    return identity.provider_leg_id


async def _on_transfer_answered_persisted(
    call_id: str,
    target_call_id: str,
) -> int:
    """Commit exact child Answer before Asterisk publishes transfer success."""

    from app.core.container import get_container
    from app.domain.services.telephony.inbound_transfer import (
        mark_inbound_transfer_answered,
    )

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise RuntimeError("database unavailable for transfer answer persistence")
    return await mark_inbound_transfer_answered(
        container.db_pool,
        parent_call_id=call_id,
        provider_leg_id=target_call_id,
    )


async def _on_transfer_connected(call_id: str, target_call_id: str) -> None:
    """Quiesce AI media only after a supervised target really answers.

    The adapter owns the caller/target bridge until either human leg hangs up.
    We deliberately keep the VoiceSession registered so the ordinary terminal
    event can measure the full call, settle the inbound reservation, and update
    the durable call exactly once.
    """
    admission = _inbound_admissions_in_flight.get(call_id)
    if admission is not None:
        admission["_transfer_connected"] = True
        admission["_transfer_target_call_id"] = target_call_id

    voice_session = _state().get_voice_session(call_id)
    if voice_session is None:
        # After-hours transfer happens before an AI VoiceSession is created.
        # Its heartbeat/deadline guards already run from _on_new_call.
        return
    if getattr(voice_session, "_transfer_media_quiesced", False):
        return

    voice_session._transfer_connected = True
    voice_session._transfer_target_call_id = target_call_id
    voice_session._hangup_reason = "transferred"
    pipeline = getattr(voice_session, "pipeline", None)
    if pipeline is not None:
        try:
            await pipeline.cancel_active_turn(call_id)
        except Exception as exc:
            logger.debug("transfer cancel_active_turn failed call=%s err=%s", call_id[:12], exc)

    # Persist both artifacts before end_session clears the media gateway's
    # recording buffers and closes provider connections.
    try:
        transcript_service = getattr(pipeline, "transcript_service", None)
        if transcript_service is None:
            transcript_service = getattr(voice_session, "transcript_service", None)
        if transcript_service is not None:
            from app.core.container import get_container

            container = get_container()
            await save_call_transcript_on_hangup(
                voice_session=voice_session,
                transcript_service=transcript_service,
                db_pool=container.db_pool if container.is_initialized else None,
            )
            voice_session._transcript_saved_at_transfer = True
    except Exception as exc:
        logger.warning("Transfer transcript persist failed for %s: %s", call_id[:12], exc)

    try:
        await _save_call_recording(voice_session, call_id)
        voice_session._recording_saved_at_transfer = True
    except Exception as exc:
        logger.warning("Transfer recording save failed for %s: %s", call_id[:12], exc)

    try:
        await _get_orchestrator().end_session(voice_session)
        voice_session._transfer_media_quiesced = True
    except Exception as exc:
        logger.warning("Transfer media teardown failed for %s: %s", call_id[:12], exc)


async def _on_transfer_cleanup_confirmed(
    parent_call_id: str,
    provider_leg_id: str,
    reason: str,
) -> int:
    """Persist late target-only absence proof while the parent stays live.

    The adapter must await this callback before dropping its cleanup indexes
    and retry it if an exception escapes. That makes the durable child-leg and
    transfer-lease transition part of the same proof-owned cleanup contract.
    """

    from app.core.container import get_container
    from app.domain.services.telephony.inbound_transfer import (
        finalize_proven_inbound_transfer_cleanup,
    )

    container = get_container()
    if not getattr(container, "is_initialized", False) or container.db_pool is None:
        raise RuntimeError("database unavailable for transfer cleanup settlement")
    return await finalize_proven_inbound_transfer_cleanup(
        container.db_pool,
        parent_call_id=parent_call_id,
        provider_leg_id=provider_leg_id,
        reason=reason,
        redis_client=getattr(container, "redis", None),
    )


async def _on_audio_received(call_id: str, audio_bytes: bytes) -> None:
    """Route incoming audio from the PBX into the media gateway (STT input)."""
    # Hot path (per RTP packet). get_voice_session is a process-local dict
    # read in both backends — Redis is never touched here.
    sb = _state()
    if getattr(sb, "strict_ownership_active", False) and not sb.is_telephony_owner():
        # A stale ARI callback can race adapter.disconnect(). Dropping it is
        # the only safe response: forwarding audio would keep a second AI
        # controller alive after its ownership proof was lost.
        return
    voice_session = sb.get_voice_session(call_id)
    if not voice_session:
        return
    # Refresh the Redis ledger TTL so a long call's entry never expires
    # underneath it. Debounced inside the backend (>=30s), so calling it
    # on every audio frame is cheap — a dict lookup, no Redis per packet.
    sb.touch_call(call_id)
    try:
        await voice_session.media_gateway.on_audio_received(voice_session.call_id, audio_bytes)
        # Clear the streak on the next successful packet.
        if call_id in _audio_route_failure_counts:
            _audio_route_failure_counts.pop(call_id, None)
            _audio_route_last_logged_at.pop(call_id, None)
    except Exception as exc:
        count = _audio_route_failure_counts.get(call_id, 0) + 1
        _audio_route_failure_counts[call_id] = count

        now = time.monotonic()
        last_logged = _audio_route_last_logged_at.get(call_id, 0.0)
        if now - last_logged >= _AUDIO_ROUTE_LOG_INTERVAL_S:
            _audio_route_last_logged_at[call_id] = now
            logger.warning(
                "audio_route_error call_id=%s consecutive_failures=%d err=%s",
                call_id[:12],
                count,
                exc,
                extra={"call_id": call_id, "consecutive_failures": count},
            )

        if count >= _AUDIO_ROUTE_FORCE_END_THRESHOLD:
            logger.warning(
                "audio_route_error call_id=%s hit %d consecutive failures — "
                "forcing end instead of waiting for the 300s watchdog",
                call_id[:12],
                count,
            )
            _audio_route_failure_counts.pop(call_id, None)
            _audio_route_last_logged_at.pop(call_id, None)
            # Must not block/crash the audio hot path — fire-and-forget, but
            # P1-9 — retain the task reference (see _track_task) so it can't
            # be GC'd mid-flight and silently drop the forced hangup.
            _track_task(_force_end_and_hangup(call_id))


# Calls whose teardown is in flight (or already ran). ARI terminal events
# arrive in bursts (ChannelHangupRequest + StasisEnd + ChannelDestroyed) and
# the adapter can dispatch _on_call_ended more than once for the same call.
# Two concurrent teardowns of one call caused a REAL full-process deadlock
# (2026-07-08): teardown A held the calls-row lock at an await point while
# teardown B's call_service chain blocked the event loop synchronously
# waiting on that same row — A could never resume to commit. One teardown
# per call, ever. Entries are dropped after a delay purely to bound memory.
_ended_calls_in_flight: set[str] = set()
# Separate proof that the owner of the in-flight marker reached the end of
# critical logical settlement. A duplicate callback must never infer this from
# a partially-updated calls row: inbound settlement can commit before linked
# transfer lease finalization completes. Recovery may skip directly to Redis
# acknowledgement only when BOTH this marker and durable terminal state agree.
_ended_calls_logically_completed: set[str] = set()


def _release_ended_marker_later(call_id: str, delay_s: float = 600.0) -> None:
    async def _drop() -> None:
        try:
            await asyncio.sleep(delay_s)
        finally:
            _ended_calls_in_flight.discard(call_id)
            _ended_calls_logically_completed.discard(call_id)

    try:
        _track_task(_drop())
    except Exception:
        _ended_calls_in_flight.discard(call_id)
        _ended_calls_logically_completed.discard(call_id)


def _resolve_inbound_terminal_outcome(
    voice_session: Any,
    inbound_admission: Mapping[str, Any],
    *,
    recovery_context: Optional[Mapping[str, Any]] = None,
    hangup_reason: Optional[str] = None,
) -> str:
    """Return the stable outcome projected onto one durable inbound row.

    True inbound deliberately bypasses ``CallService`` because that service
    also mutates outbound lead, dialer-job, and campaign state. The admission
    finalizer therefore owns the small calls-row outcome projection too.

    Product ``selected_action='voicemail'`` means a live conversational AI
    message-intake session, not an outbound answering machine. Never run that
    path through the outbound voicemail-text heuristic: a successful message
    intake is an answered inbound call.
    """

    if voice_session is not None:
        selected_action = (
            str(getattr(voice_session, "_inbound_selected_action", "") or "").strip().lower()
        )
        if not selected_action:
            snapshot = inbound_admission.get("config_snapshot")
            inbound_config = (
                snapshot.get("inbound_config") if isinstance(snapshot, Mapping) else None
            )
            if isinstance(inbound_config, Mapping):
                selected_action = str(inbound_config.get("selected_action") or "").strip().lower()

        if selected_action == "voicemail":
            return (
                "failed" if bool(getattr(voice_session, "_pipeline_failed", False)) else "answered"
            )
        return resolve_call_outcome(
            voice_session,
            hangup_reason=hangup_reason,
        ).value

    # Restart recovery may already have an outcome from a prior partial
    # projection. Preserve known canonical values; the finalizer uses COALESCE
    # and will never overwrite a non-null durable outcome in any case.
    persisted = str((recovery_context or {}).get("outcome") or "").strip().lower()
    if persisted:
        from app.domain.models.dialer_job import CallOutcome

        try:
            return CallOutcome(persisted).value
        except ValueError:
            pass

    was_answered = bool(
        inbound_admission.get("_transfer_connected")
        or inbound_admission.get("_recovery_was_answered")
        or (recovery_context or {}).get("was_answered")
    )
    return "answered" if was_answered else "failed"


async def _on_call_ended(
    call_id: str,
    *,
    terminal_at_monotonic: Any = None,
    recovery_context: Optional[Dict[str, Any]] = None,
    acknowledge_ledger: bool = True,
) -> bool:
    """Clean up voice session when the call hangs up.

    Restart recovery supplies durable database context because the live
    ``VoiceSession`` and adapter admission cache died with the prior process.
    ``False`` means logical teardown did not run (normally a duplicate already
    in flight); recovery must retain its Redis obligation. ``True`` means the
    logical path converged. Durable ledger deletion is separately controllable
    so recovery can make it its final, awaited commit point.
    """
    if recovery_context is None:
        managed_context = _orphan_recovery_contexts_by_call.get(call_id)
        if managed_context is not None:
            # PBX confirmation can emit this ordinary adapter callback while
            # the recovery coroutine is still awaiting channel inventory.
            # Adopt its already-hydrated authoritative direction/admission and
            # leave Redis acknowledgement to the recovery coordinator.
            recovery_context = managed_context
            acknowledge_ledger = False
    if (
        recovery_context is not None
        and recovery_context.get("_awaiting_all_leg_absence_proof") is True
        and recovery_context.get("_pbx_all_leg_absence_confirmed") is not True
    ):
        logger.warning(
            "orphan_terminal_callback_fenced_until_all_leg_proof call=%s",
            call_id[:12],
        )
        return False
    if call_id in _ended_calls_in_flight:
        logger.info("Telephony bridge: duplicate call-end ignored %s", call_id[:12])
        # If a prior attempt completed settlement but crashed before Redis
        # acknowledgement, database hydration proves there is no logical work
        # left and the caller may proceed directly to the idempotent ack.
        return bool(
            recovery_context
            and recovery_context.get("logical_settled")
            and call_id in _ended_calls_logically_completed
        )
    _ended_calls_in_flight.add(call_id)
    if recovery_context is not None:
        recovery_context["_logical_marker_acquired"] = True
        recovery_context["_logical_marker_owner_task"] = asyncio.current_task()
    _release_ended_marker_later(call_id)

    logger.info(f"Telephony bridge: call ended {call_id[:12]}")

    # Capture admission ownership before the adapter clears its hand-off
    # cache. The service finalizer below owns inbound terminal persistence,
    # reservations, and billing; the outbound CallService chain must never be
    # run against an inbound row.
    inbound_admission = dict(
        (recovery_context or {}).get("admission")
        or _inbound_admissions_in_flight.get(call_id)
        or {}
    )
    if not inbound_admission:
        adapter = get_adapter()
        get_admission = getattr(adapter, "get_inbound_admission", None)
        if callable(get_admission):
            inbound_admission = dict(get_admission(call_id) or {})
    if recovery_context is None and terminal_at_monotonic is None:
        adapter = get_adapter()
        pop_terminal_time = getattr(
            adapter,
            "pop_terminal_at_monotonic",
            None,
        )
        if callable(pop_terminal_time):
            terminal_at_monotonic = pop_terminal_time(call_id)
    is_true_inbound = bool(
        (recovery_context or {}).get("direction") == "inbound" or inbound_admission.get("allowed")
    )
    # Normal outbound callbacks may remove volatile session objects during
    # teardown, but they may not publish logical completion or acknowledge the
    # durable Redis ledger until CallService proves its DB transaction (and any
    # retry outbox dispatch) committed.
    outbound_settlement_required = False
    outbound_settlement_verified = bool(is_true_inbound or recovery_context)
    inbound_transfer_settlement_verified = True
    inbound_duration_s = 0
    inbound_terminal_reason: Optional[str] = (
        str(inbound_admission.get("_terminal_reason"))
        if inbound_admission.get("_terminal_reason")
        else None
    )
    if is_true_inbound and recovery_context is None:
        try:
            inbound_duration_s = _confirmed_inbound_duration_seconds(
                inbound_admission,
                terminal_at_monotonic,
            )
        except InboundTerminalProofMissing:
            inbound_duration_s = 0
            inbound_terminal_reason = "process_restart_answer_ambiguous"
            logger.critical(
                "inbound_terminal_duration_held_missing_proof call=%s",
                call_id[:12],
            )
    if recovery_context is not None:
        recovery_duration = max(0, int(recovery_context.get("duration_seconds") or 0))
        reserved_seconds = recovery_context.get("reserved_seconds")
        if isinstance(reserved_seconds, int) and not isinstance(reserved_seconds, bool):
            recovery_duration = min(recovery_duration, max(0, reserved_seconds))
        inbound_duration_s = recovery_duration
    if is_true_inbound:
        await _cancel_inbound_runtime_guards(call_id)

    # Avoid leaking the audio-route failure trackers across calls.
    _audio_route_failure_counts.pop(call_id, None)
    _audio_route_last_logged_at.pop(call_id, None)

    # Outbound has no canonical durable admission finalizer, so retain its
    # early, fail-soft slot release. True inbound is intentionally different:
    # `_finalize_inbound_admission` releases the Redis slot only *after* the
    # durable usage/tenant-lease settlement succeeds. Releasing it here first
    # would under-count a still-reserved call if that later write failed.
    if not is_true_inbound and recovery_context is None:
        try:
            from app.domain.services.global_concurrency import release_lease
            from app.core.container import get_container as _gc

            _c = _gc()
            await asyncio.wait_for(
                release_lease(
                    getattr(_c, "redis", None) if _c.is_initialized else None,
                    call_id=call_id,
                ),
                timeout=1.0,
            )
        except Exception as exc:
            logger.debug("global_concurrency_release_raised call=%s err=%s", call_id[:12], exc)

    # Track B (live call transparency): mark the call ENDED in calls.status
    # and emit a stream_events row so the live-calls panel removes it from
    # the in-flight list. This projection is optional and explicitly bounded;
    # it can never delay the authoritative lease/session teardown.
    if recovery_context is None:
        try:
            from app.domain.services.call_status import (
                CallState,
                record_call_state_by_provider_id,
            )
            from app.core.container import get_container as _gc

            _c = _gc()
            await asyncio.wait_for(
                record_call_state_by_provider_id(
                    _c.db_pool,
                    provider_call_id=call_id,
                    new_state=CallState.ENDED,
                    metadata={"description": "Call ended"},
                ),
                timeout=1.0,
            )
        except Exception as exc:
            logger.debug(
                "call_status.ended_emit_raised call=%s err=%s",
                call_id[:12],
                exc,
            )

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

    _state().clear_first_speaker(call_id)  # per-call first-speaker stash cleanup
    voice_session = _state().pop_voice_session(call_id)
    if voice_session:
        if not inbound_admission:
            inbound_admission = dict(getattr(voice_session, "_inbound_admission", None) or {})
            is_true_inbound = bool(inbound_admission.get("allowed"))
        if is_true_inbound and recovery_context is None:
            try:
                inbound_duration_s = _confirmed_inbound_duration_seconds(
                    inbound_admission,
                    terminal_at_monotonic,
                )
            except InboundTerminalProofMissing:
                inbound_duration_s = 0
                inbound_terminal_reason = "process_restart_answer_ambiguous"
        elif not is_true_inbound:
            try:
                inbound_duration_s = int(
                    getattr(
                        getattr(voice_session, "call_session", None),
                        "get_duration_seconds",
                        lambda: 0,
                    )()
                )
            except Exception:
                inbound_duration_s = 0
        session_terminal_reason = getattr(voice_session, "_hangup_reason", None)
        if inbound_terminal_reason != "process_restart_answer_ambiguous":
            inbound_terminal_reason = session_terminal_reason
        # Cancel any per-call task that's still running. Without this,
        # tasks spawned during the call (silence handler, greeting,
        # presynth warm-ups) keep firing into a torn-down gateway and
        # produce log storms or zombie work for the rest of their
        # natural lifetime. Pattern follows Pipecat's session-cleanup
        # contract: hangup is authoritative, all per-session work cancels.
        #
        # QUIESCE BEFORE READING (2026-08-03). Everything below this point
        # reads per-call state and persists it: the transcript, the resolved
        # outcome, and the opt-out/DNC purge. Each of those awaits a DB round
        # trip, and every await yields the event loop.
        #
        # The in-flight TURN task is a SIBLING task (spawned from
        # transcript_handler), not a child of pipeline_task — so it kept
        # running through all of that. It could append the agent's final line
        # to conversation_history AFTER the transcript had been persisted, and,
        # far worse, set `_caller_opted_out` AFTER the outcome had already been
        # resolved and the purge decision made.
        #
        # Concretely: a caller says "take me off your list" and hangs up
        # mid-reply. The turn task sets the opt-out flag a few milliseconds
        # after teardown already read it as False, the DNC purge never runs,
        # and that person gets called again. That is a regulatory exposure, not
        # an untidy log.
        #
        # Cancelling here — before any awaited teardown work — makes the
        # session quiescent so what we read is what actually happened.
        # `end_session()` cancels the same things again later; both are
        # idempotent, so the duplicate is a cheap no-op.
        _pipeline = getattr(voice_session, "pipeline", None)
        if _pipeline is not None:
            try:
                await _pipeline.cancel_active_turn(call_id)
            except Exception as exc:  # noqa: BLE001 — teardown must not fail
                logger.debug("teardown cancel_active_turn failed: %s", exc)

        for _attr in (
            "_greeting_task",
            "_inbound_heartbeat_task",
            "_inbound_deadline_task",
        ):
            _t = getattr(voice_session, _attr, None)
            if _t is not None and not _t.done():
                # Bounded await, not fire-and-forget. A bare .cancel() returns
                # immediately and the task keeps running until it next hits an
                # await — during which it can still append to history or push
                # audio at a torn-down channel. This mirrors
                # VoicePipelineService._cancel_turn_task, which documents why
                # an unawaited cancel is unsafe; the greeting cancel was the
                # one path that skipped that pattern.
                _t.cancel()
                if _pipeline is not None:
                    try:
                        await _pipeline._cancel_turn_task(_t, call_id, _attr)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("teardown greeting cancel failed: %s", exc)

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
            # Realtime sessions have no cascaded pipeline; the bridge accumulated
            # transcripts into a TranscriptService stashed on the voice_session.
            if transcript_service is None:
                transcript_service = getattr(voice_session, "transcript_service", None)
            from app.core.container import get_container as _gc

            _c = _gc()
            _pool = _c.db_pool if _c.is_initialized else None
            if transcript_service is not None and not getattr(
                voice_session, "_transcript_saved_at_transfer", False
            ):
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
            if dialer_call_id and not is_true_inbound:
                outbound_settlement_required = True
                outbound_settlement_verified = False
                from app.core.container import get_container as _gc2

                _c2 = _gc2()
                if _c2.is_initialized:
                    # Pull the real PBX hangup cause off the adapter (captured
                    # from the terminal ARI event) so an unanswered call is
                    # classified NO_ANSWER / BUSY / REJECTED — not defaulted to
                    # an agent-side hangup — which is what lets it reschedule
                    # +24h. Only fill it if the pipeline didn't already set one.
                    if not getattr(voice_session, "_hangup_reason", None):
                        try:
                            _adapter = get_adapter()
                            _cause = _adapter.get_hangup_cause(call_id) if _adapter else None
                            if _cause:
                                voice_session._hangup_reason = _cause
                        except Exception as _cause_exc:
                            logger.debug(
                                "hangup_cause_lookup_failed call=%s err=%s",
                                call_id[:12],
                                _cause_exc,
                            )
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
                        voice_session,
                        "_dialer_tenant_id",
                        None,
                    )
                    if tenant_for_call:
                        set_current_tenant_id(tenant_for_call)

                    call_service = CallService(
                        db_client=_c2.db_client,
                        queue_service=getattr(_c2, "_queue_service", None),
                        # 2026-07-08: pooled + atomic teardown writes —
                        # see CallService._handle_call_status_pooled.
                        db_pool=_c2.db_pool,
                    )
                    settlement_result = await call_service.handle_call_status(
                        call_uuid=dialer_call_id,
                        outcome=outcome,
                        duration=duration,
                    )
                    if not settlement_result.durable:
                        raise RuntimeError(
                            "call terminal settlement unverified: "
                            f"{settlement_result.error or 'unknown'}"
                        )
                    outbound_settlement_verified = True
                    logger.info(
                        "call_outcome_persisted call_id=%s outcome=%s duration_s=%d",
                        call_id[:12],
                        settlement_result.terminal_outcome or outcome.value,
                        duration,
                    )
        except Exception as m_err:
            logger.warning(
                "call_outcome_persist_failed call_id=%s err=%s",
                call_id[:12],
                m_err,
            )

        # --- Compliance: honor an in-call opt-out (Phase 3d) ---------------
        # If the live agent flagged a "never call me again" request, purge
        # the lead now: DNC the number, cancel its scheduled jobs, mark the
        # lead DNC. Runs once, at teardown, and never blocks the rest of it.
        try:
            _cs = getattr(voice_session, "call_session", None)
            opted_out = bool(
                getattr(voice_session, "_caller_opted_out", False)
                or getattr(_cs, "_caller_opted_out", False)
            )
            # The end-action path now writes the DNC row BEFORE speaking the
            # farewell and marks the session; this teardown purge is the retry
            # for the case where that write did not land.
            if opted_out and getattr(voice_session, "_opt_out_purged", False):
                logger.info("opt_out_purge_already_done call_id=%s", call_id[:12])
            elif opted_out:
                from app.core.container import get_container as _gc3

                _c3 = _gc3()
                if _c3.is_initialized:
                    from app.domain.services.dialer.opt_out import purge_lead_on_opt_out

                    await purge_lead_on_opt_out(
                        db_pool=_c3.db_pool,
                        db_client=_c3.db_client,
                        tenant_id=getattr(voice_session, "_dialer_tenant_id", None),
                        lead_id=getattr(voice_session, "_dialer_lead_id", None),
                        phone_number=getattr(voice_session, "_dialer_phone", None),
                        call_id=getattr(voice_session, "_dialer_call_id", None),
                    )
        except Exception as oo_err:
            logger.warning(
                "opt_out_purge_failed call_id=%s err=%s",
                call_id[:12],
                oo_err,
            )

        # --- Save recording BEFORE session teardown ---
        if not getattr(voice_session, "_recording_saved_at_transfer", False):
            try:
                await _save_call_recording(voice_session, call_id)
            except Exception as rec_err:
                logger.warning(f"Recording save failed for {call_id[:12]}: {rec_err}")

        if not getattr(voice_session, "_transfer_media_quiesced", False):
            try:
                await _get_orchestrator().end_session(voice_session)
            except Exception:
                pass
    else:
        # ----- No voice session: the call was NEVER answered ---------------
        # (busy / no-answer / rejected / carrier failure — the adapter's
        # pre-Stasis terminal arm dispatched us). The answered-path outcome
        # persist above needs voice_session._dialer_call_id, so without this
        # branch an unanswered call keeps outcome=NULL until a reaper sweeps
        # it minutes later — the UI shows "ringing" long after the carrier
        # said "busy". Resolve the outcome from the captured Q.850 cause and
        # drive the SAME call_service chain (calls row + lead + campaign
        # counters + dialer job) so "not available" lands in real time.
        # Idempotent: only calls still without an outcome are touched.
        if not is_true_inbound and recovery_context is None:
            outbound_settlement_required = True
            outbound_settlement_verified = False
        try:
            from app.core.container import get_container as _gc4

            _c4 = _gc4()
            if _c4.is_initialized and not is_true_inbound and recovery_context is None:
                _cause = None
                try:
                    _adp = get_adapter()
                    _cause = _adp.get_hangup_cause(call_id) if _adp else None
                except Exception:
                    _cause = None
                from app.core.db_utils import acquire_with_tenant

                # Provider callback does not carry a tenant; this lookup exists
                # to discover it before all subsequent tenant-scoped work.
                async with acquire_with_tenant(_c4.db_pool, None) as _conn:
                    _row = await _conn.fetchrow(
                        """
                        SELECT id, tenant_id FROM calls
                        WHERE (provider_call_id = $1 OR external_call_uuid = $1)
                          AND COALESCE(direction, 'outbound') <> 'inbound'
                          AND (
                               terminal_settled_at IS NULL
                               OR (
                                    terminal_retry_payload IS NOT NULL
                                AND terminal_retry_enqueued_at IS NULL
                               )
                          )
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        call_id,
                    )
                if _row is not None:
                    outcome = resolve_call_outcome(None, hangup_reason=_cause)
                    from app.core.security.tenant_isolation import (
                        set_bypass_rls,
                        set_current_tenant_id,
                    )

                    set_bypass_rls(True)
                    set_current_tenant_id(str(_row["tenant_id"]))
                    call_service = CallService(
                        db_client=_c4.db_client,
                        queue_service=getattr(_c4, "_queue_service", None),
                        # 2026-07-08: pooled + atomic teardown writes —
                        # see CallService._handle_call_status_pooled.
                        db_pool=_c4.db_pool,
                    )
                    settlement_result = await call_service.handle_call_status(
                        call_uuid=str(_row["id"]),
                        outcome=outcome,
                        duration=0,
                    )
                    if not settlement_result.durable:
                        raise RuntimeError(
                            "preanswer terminal settlement unverified: "
                            f"{settlement_result.error or 'unknown'}"
                        )
                    outbound_settlement_verified = True
                    logger.info(
                        "call_outcome_persisted_preanswer call_id=%s outcome=%s cause=%s",
                        call_id[:12],
                        settlement_result.terminal_outcome or outcome.value,
                        _cause,
                    )
                else:
                    # A successful authoritative lookup found no unsettled
                    # durable outbound row for this provider session.
                    outbound_settlement_verified = True
        except Exception as pa_err:
            logger.warning(
                "preanswer_outcome_persist_failed call_id=%s err=%s",
                call_id[:12],
                pa_err,
            )
        if recovery_context is not None and not is_true_inbound:
            await _settle_recovered_outbound_call(recovery_context)
    # Clean up gateway session mapping and early audio buffer for this call.
    _state().remove_gateway_sessions_for_call(call_id)

    if is_true_inbound:
        # Settle every child leg before the parent for both live teardown and
        # restart recovery. The parent usage transaction is the final durable
        # acknowledgement; committing it first could hide an active/unbilled
        # PSTN target from the retry scan.
        try:
            from app.core.container import get_container as _transfer_gc
            from app.domain.services.telephony.inbound_transfer import (
                finalize_connected_inbound_transfers,
            )

            _transfer_container = _transfer_gc()
            await finalize_connected_inbound_transfers(
                _transfer_container.db_pool,
                call_id=str(inbound_admission["call_id"]),
                terminal_reason=inbound_terminal_reason,
                redis_client=getattr(_transfer_container, "redis", None),
                hold_ambiguous_transfer_legs=bool(
                    recovery_context is not None
                    and recovery_context.get("hold_ambiguous_transfer_legs")
                    and recovery_context.get("_pbx_all_leg_absence_confirmed") is True
                ),
            )
        except Exception as exc:
            inbound_transfer_settlement_verified = False
            logger.error(
                "inbound_transfer_terminal_finalize_failed call=%s err=%s",
                call_id[:12],
                exc,
            )
            if recovery_context is not None:
                # Leave the durable termination-pending candidate selectable
                # for the next watchdog pass.
                raise

    if is_true_inbound and inbound_transfer_settlement_verified:
        inbound_outcome = _resolve_inbound_terminal_outcome(
            voice_session,
            inbound_admission,
            recovery_context=recovery_context,
            hangup_reason=inbound_terminal_reason,
        )
        recovery_release_only = bool(
            recovery_context is not None
            and str(inbound_admission.get("admission_status") or "").strip().lower()
            in {"pending", "denied"}
            and not bool(recovery_context.get("was_answered"))
        )
        finalize_attempt = 0
        finalize_delay_s = max(
            0.01,
            float(os.getenv("INBOUND_FINALIZE_RETRY_INITIAL_S", "0.25")),
        )
        finalize_max_delay_s = max(
            finalize_delay_s,
            float(os.getenv("INBOUND_FINALIZE_RETRY_MAX_S", "5.0")),
        )
        while True:
            try:
                await _finalize_inbound_admission(
                    call_id,
                    inbound_admission,
                    terminal_status=(
                        "completed"
                        if voice_session is not None
                        or bool(inbound_admission.get("_transfer_connected"))
                        or bool((recovery_context or {}).get("was_answered"))
                        else "failed"
                    ),
                    duration_seconds=inbound_duration_s,
                    outcome=inbound_outcome,
                    reason=inbound_terminal_reason,
                    release_only=recovery_release_only,
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                finalize_attempt += 1
                logger.error(
                    "inbound_terminal_finalize_failed call=%s attempt=%d " "retry_in=%.2fs err=%s",
                    call_id[:12],
                    finalize_attempt,
                    finalize_delay_s,
                    exc,
                )
                if recovery_context is not None:
                    # Recovery must return control to the watchdog while the
                    # durable Redis obligation is still present. An unbounded
                    # retry loop here prevents later passes and cannot expose
                    # its failure to the acknowledgement boundary.
                    raise
                await asyncio.sleep(finalize_delay_s)
                finalize_delay_s = min(
                    finalize_max_delay_s,
                    finalize_delay_s * 2.0,
                )
    settlement_failed = bool(
        (outbound_settlement_required and not outbound_settlement_verified)
        or not inbound_transfer_settlement_verified
    )
    if settlement_failed:
        # Promote the current-pod ledger entry to an explicit cleanup
        # obligation. Ordinary active entries owned by this live incarnation
        # are intentionally excluded from orphan scans; termination_pending
        # entries are retried by the <=30s watchdog even when this pod remains
        # healthy. Never leave the ten-minute in-process duplicate marker in
        # front of that retry.
        try:
            tenant_for_retry = inbound_admission.get("tenant_id") or getattr(
                voice_session, "_dialer_tenant_id", None
            )
            campaign_for_retry = inbound_admission.get("campaign_id") or getattr(
                voice_session, "_dialer_campaign_id", None
            )
            await _state().register_cleanup_obligation(
                call_id,
                tenant_id=(str(tenant_for_retry) if tenant_for_retry else None),
                campaign_id=(str(campaign_for_retry) if campaign_for_retry else None),
                state="termination_pending",
            )
        except Exception as exc:
            logger.error(
                "terminal_settlement_retry_registration_failed call=%s err=%s",
                call_id[:12],
                exc,
            )
        _ended_calls_in_flight.discard(call_id)
        _ended_calls_logically_completed.discard(call_id)
        logger.error(
            "terminal_logical_completion_deferred call=%s outbound_verified=%s "
            "inbound_transfer_verified=%s; ledger retained",
            call_id[:12],
            outbound_settlement_verified,
            inbound_transfer_settlement_verified,
        )
        return False

    if recovery_context is not None and not is_true_inbound:
        # The normal live callback releases outbound capacity near the start
        # of teardown. Recovery defers it until after the durable call/lead/job
        # transaction has been verified, so a failed settlement remains fully
        # represented for the next attempt.
        from app.core.container import get_container as _recovery_gc
        from app.domain.services.global_concurrency import release_lease_strict

        recovery_container = _recovery_gc()
        await asyncio.wait_for(
            release_lease_strict(
                getattr(recovery_container, "redis", None),
                call_id=call_id,
            ),
            timeout=1.0,
        )

    _ended_calls_logically_completed.add(call_id)

    if acknowledge_ledger:
        # Local object removal above is intentionally not a durable commit.
        # Normal terminal callbacks acknowledge here, after all critical
        # settlement. Restart recovery passes False and performs the same
        # awaited operation itself so it can count/report the exact commit.
        try:
            await _state().acknowledge_orphan_recovery(call_id)
        except Exception as exc:
            logger.warning(
                "telephony_terminal_ledger_ack_failed call=%s err=%s; "
                "durable entry retained for retry",
                call_id[:12],
                exc,
            )

    return True


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
        # P1-10 — _on_new_call acquires the global concurrency lease for
        # this call_id BEFORE storing the VoiceSession, so a session-race
        # timeout here can fire with a lease already held. A bare
        # adapter.hangup() tears down the PBX channel but never releases
        # that lease, leaking the cluster-wide concurrency slot until its
        # ~10-min TTL expires. _force_end_and_hangup runs _on_call_ended
        # (which releases the lease first, then no-ops the rest since no
        # VoiceSession is stored) followed by the same best-effort hangup.
        await _force_end_and_hangup(call_id)
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
                    await voice_session.pipeline.start_pipeline(voice_session.call_session, None)
                except Exception as exc:
                    logger.error(f"Pipeline error {call_id[:12]}: {exc}", exc_info=True)
                    # FIX 3 / FIX #1b — trigger session teardown so the session
                    # doesn't leak, AND best-effort hang up so the PBX channel
                    # is actually released instead of staying connected on
                    # dead air (this also catches TerminalSTTError from a
                    # terminal STT failure on the FreeSWITCH path).
                    await _force_end_and_hangup(call_id)

            voice_session.pipeline_task = asyncio.create_task(_run())
            logger.info(f"Voice pipeline started for {call_id[:12]}")
    except Exception as exc:
        logger.error(f"WS session start error: {exc}", exc_info=True)
