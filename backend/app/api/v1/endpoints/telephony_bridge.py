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
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
    build_telephony_greeting,
)
from app.services.scripts import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
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


def _pop_ringing_warmup(call_id: str):
    """
    Atomically pop a ringing-phase warmup entry and its parallel timestamp.

    Returns the (VoiceSession, connect_task) tuple if present, else None.
    Callers are responsible for cancelling the task and ending the session.
    """
    _ringing_warmup_created_at.pop(call_id, None)
    return _ringing_warmups.pop(call_id, None)

# Coordination events for ringing-phase warmup.  When _on_ringing starts, it
# inserts an unset asyncio.Event for the call_id.  When the warmup completes
# (or fails), the event is set.  _on_new_call awaits this event instead of
# polling _ringing_warmups — this eliminates the race condition where the
# answer path (7ms ARI setup) finishes long before the warmup (~1s for
# create_voice_session + provider init).
_ringing_events: dict[str, asyncio.Event] = {}




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_orchestrator():
    from app.core.container import get_container
    return get_container().voice_orchestrator


def _outbound_first_speaker() -> str:
    """
    Who speaks first on an outbound (campaign) call after the callee answers.

    Returns "user" or "agent".  Default is "agent" — the estimation agent speaks
    an immediate greeting so the callee never hears dead silence after picking up.
    Set TELEPHONY_FIRST_SPEAKER=user to wait for the callee to speak first
    (useful for inbound-style testing).
    """
    val = (os.getenv("TELEPHONY_FIRST_SPEAKER") or "agent").strip().lower()
    return "user" if val == "user" else "agent"


def _build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign=None,
    agent_name: Optional[str] = None,
):
    """
    Thin shim kept for call-site compatibility.
    All config logic lives in telephony_session_config.build_telephony_session_config().
    """
    return build_telephony_session_config(
        gateway_type=gateway_type,
        campaign=campaign,
        agent_name_override=agent_name,
    )


# ---------------------------------------------------------------------------
# Audio pipeline lifecycle (called when a new call arrives on any B2BUA)
# ---------------------------------------------------------------------------

def _build_outbound_greeting(session) -> str:
    """
    Build the estimation agent's opening line from the session's agent_config.

    Delegates to telephony_session_config.build_telephony_greeting() so the
    greeting and the system prompt always reference the same agent_name and
    company_name — both set in build_telephony_session_config().
    """
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or "All States Estimation"
    return build_telephony_greeting(agent_name, company)


async def _send_outbound_greeting(voice_session) -> None:
    """
    Speak the AI's opening line immediately after an outbound call is answered.

    Fast path (pre-synthesized): The greeting audio was already synthesized
    during the ringing phase by _on_ringing.  The buffered PCM chunks are
    pumped directly into the media gateway — first audio reaches the callee
    within ~5ms of this function starting (same instant-start pattern as
    Ask AI's pre-fetched greeting).

    Slow path (fallback): If ringing-phase pre-synthesis failed or was skipped,
    falls back to real-time TTS via synthesize_and_send_audio.
    """
    from app.domain.models.conversation import Message, MessageRole
    import time as _time

    call_id = voice_session.call_id
    session = voice_session.call_session

    # Mark LLM as active so handle_turn_end in the pipeline skips any early
    # caller speech ("Hello?") that arrives before the greeting plays.
    if session.llm_active:
        logger.debug(f"Greeting skipped — LLM already active for {call_id[:12]}")
        return
    session.llm_active = True

    try:
        # Clear any barge_in_event that fired when the callee answered ("Hello?")
        # so the greeting is not immediately suppressed before a single chunk plays.
        voice_session.pipeline.clear_barge_in_event(session)

        # ── Fast path: pre-synthesized greeting from ringing phase ──────
        presynth_chunks = getattr(voice_session, "_presynth_greeting_audio", None)
        presynth_text = getattr(voice_session, "_presynth_greeting_text", None)

        # Defaults visible to the history-append block below regardless of
        # which playback path runs.
        was_interrupted = False
        chunks_sent = 0

        if presynth_chunks and presynth_text:
            _t0 = _time.monotonic()
            greeting = presynth_text
            logger.info(
                "outbound_greeting_presynth call_id=%s chunks=%d text=%r",
                call_id[:12], len(presynth_chunks), greeting[:60],
            )

            session.tts_active = True
            barge_in_event = getattr(session, "barge_in_event", None)

            for chunk in presynth_chunks:
                # Check barge-in before each chunk
                if barge_in_event and barge_in_event.is_set():
                    was_interrupted = True
                    barge_in_event.clear()
                    try:
                        await voice_session.media_gateway.clear_output_buffer(call_id)
                    except Exception:
                        pass
                    logger.info("presynth_greeting_barge_in call_id=%s", call_id[:12])
                    break

                await voice_session.media_gateway.send_audio(call_id, chunk)
                chunks_sent += 1

                # Check barge-in after send
                if barge_in_event and barge_in_event.is_set():
                    was_interrupted = True
                    barge_in_event.clear()
                    try:
                        await voice_session.media_gateway.clear_output_buffer(call_id)
                    except Exception:
                        pass
                    logger.info("presynth_greeting_barge_in_post_send call_id=%s", call_id[:12])
                    break

            # Flush remaining audio in the gateway buffer
            if not was_interrupted:
                flush = getattr(voice_session.media_gateway, "flush_tts_buffer", None)
                if not flush:
                    flush = getattr(voice_session.media_gateway, "flush_audio_buffer", None)
                if flush:
                    try:
                        await flush(call_id)
                    except Exception:
                        pass

            session.tts_active = False
            _elapsed_ms = (_time.monotonic() - _t0) * 1000.0
            logger.info(
                "outbound_greeting_presynth_done call_id=%s elapsed_ms=%.0f interrupted=%s",
                call_id[:12], _elapsed_ms, was_interrupted,
            )

            # Free memory
            voice_session._presynth_greeting_audio = None
            voice_session._presynth_greeting_text = None

        else:
            # ── Slow path: real-time TTS (ringing pre-synth unavailable) ─
            await asyncio.sleep(0.05)
            greeting = _build_outbound_greeting(session)
            logger.info(
                "outbound_greeting_realtime call_id=%s text=%r",
                call_id[:12], greeting[:60],
            )
            await voice_session.pipeline.synthesize_and_send_audio(
                session, greeting, websocket=None
            )

        # Persist the greeting so the LLM sees it as conversation history on the
        # first real turn.  Without this the next turn sees an empty history and
        # the LLM re-reads "OPENING THE CALL" instructions, generating a duplicate.
        #
        # If the callee barged in mid-greeting, only persist the portion that
        # actually played. Recording the full text would make the LLM think the
        # callee already heard the question ("Do you have a minute to talk?")
        # when in reality they cut us off after just the intro — and the LLM
        # would then respond as if the question was answered, producing a
        # confused follow-up that sounds like a second greeting.
        try:
            _spoken_text = greeting
            if was_interrupted and presynth_chunks and chunks_sent > 0:
                _frac = chunks_sent / len(presynth_chunks)
                _words = greeting.split()
                _keep = max(1, int(len(_words) * _frac))
                _spoken_text = " ".join(_words[:_keep])
                if _keep < len(_words):
                    _spoken_text = _spoken_text.rstrip(".,!?") + "…"
        except Exception:
            _spoken_text = greeting
        session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=_spoken_text)
        )
    except Exception as exc:
        logger.warning(f"Outbound greeting failed for {call_id[:12]}: {exc}")
    finally:
        session.llm_active = False


# ──────────────────────────────────────────────────────────────────────────
# User-first silence handler
#
# When `first_speaker == "user"` we ring the bell, register STT (Flux is
# already pre-connected and ready to listen) and stay silent. If the callee
# never speaks, traditional voice agents deadlock — caller waiting for AI,
# AI waiting for caller. This helper runs a graduated state machine that
# matches the patterns documented in April 2026 by:
#
#   - Microsoft Copilot Studio (silence detection → reprompt → fallback)
#     https://learn.microsoft.com/en-us/microsoft-copilot-studio/voice-configuration
#   - ElevenLabs Conversation Flow (turn timeouts: 5–10s casual, 10–30s
#     thinking; LLM-generated openings, not scripted)
#     https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow
#   - NVIDIA PersonaPlex / Deepgram (200–600ms human conversation gap;
#     barge-in-first design; LLM produces backchannels naturally)
#
# Phases (defaults in seconds — env-overridable):
#   1.  open_s          (2.0): pickup-to-AI-greeting natural beat
#   2.  reprompt_s      (8.0): wait after greeting for callee to speak
#   3.  reprompt_s      (8.0): second reprompt
#   4.  farewell_s      (6.0): final wait, then graceful hangup
#
# Cancellation is symmetric: if the callee speaks at *any* point (real user
# message lands in conversation_history), the task exits and normal turn
# flow resumes. The agent-first flow is unchanged.
# ──────────────────────────────────────────────────────────────────────────

# Internal-cue prefix used to differentiate synthetic silence-handler
# instructions from real callee speech in conversation_history. Real
# user messages will never start with this token.
_USER_FIRST_CUE_PREFIX = "[CALLEE_"


async def _handle_user_first_silence(voice_session, pbx_call_id: str) -> None:
    """Run the graduated silence flow for a user-first outbound call."""
    from app.domain.models.conversation import Message, MessageRole

    session = voice_session.call_session
    call_id = voice_session.call_id

    open_s = float(os.getenv("TELEPHONY_USER_FIRST_OPEN_S", "2.0"))
    reprompt_s = float(os.getenv("TELEPHONY_USER_FIRST_REPROMPT_S", "8.0"))
    farewell_s = float(os.getenv("TELEPHONY_USER_FIRST_FAREWELL_S", "6.0"))
    max_reprompts = int(os.getenv("TELEPHONY_USER_FIRST_MAX_REPROMPTS", "2"))

    def _real_user_count() -> int:
        return sum(
            1
            for m in session.conversation_history
            if m.role == MessageRole.USER
            and not (m.content or "").startswith(_USER_FIRST_CUE_PREFIX)
        )

    initial_user_msgs = _real_user_count()

    async def _wait_or_speech(timeout: float) -> bool:
        """Sleep up to `timeout`s. Return True if the callee spoke (a real
        user message arrived) so the caller can bail out of the state
        machine. Polls every 100ms — cheap and avoids hooking new events
        into the pipeline."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if _real_user_count() > initial_user_msgs:
                return True
            await asyncio.sleep(0.1)
        return _real_user_count() > initial_user_msgs

    async def _drive_llm_with_cue(cue: str, phase: str) -> None:
        """Inject the cue as a synthetic user input and run one normal
        pipeline turn so the LLM produces audio using its existing system
        prompt (campaign company, agent name, persona)."""
        if session.tts_active or session.llm_active:
            logger.debug(
                "user_first_skip_phase phase=%s call=%s reason=busy",
                phase, call_id[:12],
            )
            return
        try:
            session.current_user_input = cue
            logger.info(
                "user_first_phase phase=%s call=%s",
                phase, call_id[:12],
            )
            await voice_session.pipeline.handle_turn_end(
                session, websocket=None, source=f"user_first_{phase}"
            )
        except Exception as exc:
            logger.warning(
                "user_first_phase_failed phase=%s call=%s err=%s",
                phase, call_id[:12], exc,
            )
        finally:
            try:
                session.current_user_input = ""
            except AttributeError:
                pass

    try:
        # Phase 1 — initial listen window. The natural beat between
        # phone-to-ear and first speech. ~2s matches outbound pickup
        # behavior; shorter than ElevenLabs' casual 5–10s because the
        # callee wasn't expecting a phone call and won't volunteer "hello"
        # if we wait too long.
        if await _wait_or_speech(open_s):
            return

        # Phase 2 — natural opening. The LLM uses the system prompt's
        # campaign context to greet (company, agent name, persona). The
        # bracketed cue is an internal instruction the model reads as a
        # situation hint, not as something to repeat verbatim.
        await _drive_llm_with_cue(
            "[CALLEE_SILENT_AT_PICKUP — The callee answered but has not "
            "spoken yet. Open the call naturally in one short sentence "
            "using your campaign context (company name, your agent name, "
            "persona). Do NOT repeat this cue.]",
            "open",
        )

        # Phases 3..N — reprompts (default 2 attempts).
        for i in range(max_reprompts):
            if await _wait_or_speech(reprompt_s):
                return
            await _drive_llm_with_cue(
                f"[CALLEE_NO_RESPONSE_REPROMPT_{i + 1} — The callee still "
                f"has not spoken. Briefly, warmly check if they're still "
                f"there. No more than 6 words. Do NOT repeat this cue.]",
                f"reprompt_{i + 1}",
            )

        # Phase final — graceful farewell + hangup.
        if await _wait_or_speech(farewell_s):
            return
        await _drive_llm_with_cue(
            "[CALLEE_UNRESPONSIVE — Multiple reprompts unanswered. Say "
            "one short polite goodbye and end the call. Do NOT repeat "
            "this cue.]",
            "farewell",
        )
        # Allow the farewell TTS to play out before tearing down.
        await asyncio.sleep(2.0)
        if _adapter is not None:
            try:
                await _adapter.hangup(pbx_call_id)
                logger.info(
                    "user_first_hangup_after_farewell call=%s",
                    call_id[:12],
                )
            except Exception as exc:
                logger.warning(
                    "user_first_hangup_failed call=%s err=%s",
                    call_id[:12], exc,
                )

    except asyncio.CancelledError:
        logger.info("user_first_silence_cancelled call=%s", call_id[:12])
        raise
    except Exception as exc:
        logger.warning(
            "user_first_silence_handler_failed call=%s err=%s",
            call_id[:12], exc,
        )


_SESSION_INACTIVITY_TIMEOUT_S = int(os.getenv("TELEPHONY_INACTIVITY_TIMEOUT_S", "300"))  # 5 min
_SESSION_MAX_DURATION_S = int(os.getenv("TELEPHONY_MAX_CALL_DURATION_S", "3600"))  # 1 hour


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
            for call_id, vs in list(_telephony_sessions.items()):
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
                cid for cid, created_at in list(_ringing_warmup_created_at.items())
                if (now - created_at) > _RINGING_MAX_AGE_S
            ]
            for cid in stale_ringing:
                ringing = _pop_ringing_warmup(cid)
                _ringing_events.pop(cid, None)
                logger.warning(
                    "telephony_watchdog: orphaned ringing_warmup %s "
                    "(age >%ds) — releasing STT/TTS sockets",
                    cid[:12], _RINGING_MAX_AGE_S,
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
                cid for cid in list(_ringing_events.keys())
                if cid not in _ringing_warmup_created_at
                and cid not in _telephony_sessions
            ]
            for cid in stale_events:
                _ringing_events.pop(cid, None)

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
                    for live_id in list(_telephony_sessions.keys()):
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
    if _adapter is None or getattr(_adapter, "name", "") != "asterisk":
        return
    if call_id in _ringing_warmups or call_id in _telephony_sessions:
        return  # idempotent — StasisStart should fire _on_ringing at most once
    if len(_telephony_sessions) + len(_ringing_warmups) >= _MAX_TELEPHONY_SESSIONS:
        logger.warning(
            "ringing_warmup_skipped_at_capacity call_id=%s", call_id[:12],
        )
        return

    # Signal to _on_new_call that a ringing warmup is in progress.
    # This MUST be set before any await so the event is visible immediately
    # when the answer path checks for it (even if create_voice_session
    # takes ~1 s).
    evt = asyncio.Event()
    _ringing_events[call_id] = evt

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

        _ringing_warmups[call_id] = (voice_session, combined_task)
        _ringing_warmup_created_at[call_id] = asyncio.get_event_loop().time()
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
    if _adapter:
        try:
            await _adapter.hangup(call_id)
        except Exception:
            pass


async def _on_new_call(call_id: str) -> None:
    """Initialize AI pipeline when a new SIP call arrives."""
    # Per-pod cap (existing, kept as a backstop so a single pod never
    # exceeds its MAX_TELEPHONY_SESSIONS memory budget). The global cap
    # below is the new cluster-wide check (T1.2).
    if len(_telephony_sessions) >= _MAX_TELEPHONY_SESSIONS:
        logger.error(
            "telephony_at_pod_capacity sessions=%d call_id=%s — rejecting",
            len(_telephony_sessions), call_id[:12],
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
    logger.info(f"BRIDGE new_call {call_id[:12]} (ringing_warmup_available={call_id in _ringing_warmups})")
    try:
        orchestrator = _get_orchestrator()

        # Select the correct media gateway based on the active PBX adapter:
        #   - Asterisk path: TelephonyMediaGateway (HTTP callbacks, no WebSocket)
        #   - FreeSWITCH path: BrowserMediaGateway (mod_audio_fork WebSocket)
        is_asterisk = bool(_adapter and _adapter.name == "asterisk")
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
            ringing_evt = _ringing_events.get(call_id)
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
            _ringing_events.pop(call_id, None)  # clean up event
        connect_task: Optional[asyncio.Task] = None
        if pre is not None:
            voice_session, connect_task = pre  # type: ignore[assignment]
        else:
            config = _build_telephony_session_config(gateway_type=gateway_type)
            voice_session = await orchestrator.create_voice_session(config)

        _telephony_sessions[call_id] = voice_session

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
            gateway_session_id = getattr(_adapter, "_gateway_sessions", {}).get(call_id)
            if gateway_session_id:
                _gateway_session_to_call_id[gateway_session_id] = call_id

            await voice_session.media_gateway.on_call_started(
                voice_session.call_id,
                {"adapter": _adapter, "pbx_call_id": call_id},
            )

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
            first_speaker = getattr(voice_session, "_first_speaker", None) or _outbound_first_speaker()
            if first_speaker == "agent":
                asyncio.create_task(_send_outbound_greeting(voice_session))
            else:
                logger.info(
                    "outbound_greeting_suppressed call_id=%s first_speaker=user — "
                    "scheduling silence-handler",
                    call_id[:12],
                )
                # Graduated silence handling for user-first calls:
                # listen → natural opening → reprompt → reprompt → graceful hangup.
                # Each phrase is LLM-generated from the campaign's existing system
                # prompt (company, agent name, persona) — nothing hardcoded except
                # internal cues the LLM reads as instructions.
                # See docstring of _handle_user_first_silence for sources.
                voice_session._user_first_silence_task = asyncio.create_task(
                    _handle_user_first_silence(voice_session, call_id)
                )

        # Tell the adapter to start streaming audio.
        # For Asterisk this is a no-op (audio_callback_url handles it via C++ gateway).
        # For FreeSWITCH this triggers mod_audio_fork which connects the WebSocket,
        # which then triggers _on_ws_session_start to complete the FS pipeline setup.
        if _adapter:
            await _adapter.start_audio_stream(call_id)

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
        orphan = _telephony_sessions.pop(call_id, None)
        if orphan is not None:
            try:
                await _get_orchestrator().end_session(orphan)
            except Exception:
                pass
        if _adapter:
            try:
                await _adapter.hangup(call_id)
            except Exception:
                pass


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

    voice_session = _telephony_sessions.pop(call_id, None)
    if voice_session:
        # Cancel the user-first silence handler if it's still running.
        # Hanging up while a reprompt phase is sleeping would leak the task
        # and waste log noise on a closed gateway.
        _silence_task = getattr(voice_session, "_user_first_silence_task", None)
        if _silence_task is not None and not _silence_task.done():
            _silence_task.cancel()

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

    # Resolve sample rate from the live session so this stays correct after
    # the 8kHz -> 16kHz telephony migration. The voice_session.config holds
    # the gateway sample rate that the recording buffers were written at.
    rec_sample_rate = 16000
    try:
        cfg = getattr(voice_session, "config", None)
        if cfg is not None:
            rec_sample_rate = int(
                getattr(cfg, "gateway_sample_rate", None)
                or getattr(cfg, "stt_sample_rate", None)
                or 16000
            )
    except Exception:
        rec_sample_rate = 16000

    # Mix caller (left) + agent (right) into a stereo WAV
    wav_bytes = mix_stereo_recording(
        caller_chunks=caller_chunks or [],
        agent_chunks=agent_chunks or [],
        sample_rate=rec_sample_rate,
    )

    # Calculate duration from caller side (continuous timeline reference)
    caller_bytes = sum(len(c) for c in (caller_chunks or []))
    agent_bytes = sum(len(chunk) for _, chunk in (agent_chunks or []))
    bytes_per_sec = rec_sample_rate * 2  # 16-bit mono (per channel)
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
        sample_rate=rec_sample_rate,
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
        # GAP 4 — Race: mod_audio_fork WebSocket can connect before _on_new_call
        # stores the session (especially under server load).  Poll for up to 2s
        # (40 × 50ms) before giving up — was 1s (20 × 50ms).
        for _ in range(40):
            await asyncio.sleep(0.05)
            voice_session = _telephony_sessions.get(call_id)
            if voice_session:
                break

    if not voice_session:
        logger.error("FS WebSocket session race timeout — hanging up call %s", call_id[:12])
        if _adapter:
            try:
                await _adapter.hangup(call_id)
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
        pre_warm_session = await orchestrator.create_voice_session(config)
        pre_warm_session._first_speaker = effective_first_speaker
        if agent_name:
            pre_warm_session._agent_name = agent_name

        # TTS + STT WebSocket connections
        warmup_coros = []
        _tts_connect = getattr(pre_warm_session.tts_provider, "connect_for_call", None)
        if _tts_connect is not None:
            warmup_coros.append(_tts_connect(pre_warm_session.call_id))
        if hasattr(pre_warm_session.stt_provider, "pre_connect"):
            warmup_coros.append(
                pre_warm_session.stt_provider.pre_connect(
                    pre_warm_session.call_session.call_id
                )
            )
        if warmup_coros:
            # Strict gate: every STT/TTS WebSocket handshake MUST succeed before
            # we ring the bell. A failure here means the pipeline isn't ready —
            # if we let originate fire anyway, the callee picks up to either
            # silence (TTS dead) or a deaf agent (STT dead). Hard-fail instead.
            results = await asyncio.gather(*warmup_coros, return_exceptions=True)
            failed = [r for r in results if isinstance(r, Exception)]
            if failed:
                raise RuntimeError(
                    f"pre_originate_warmup_handshake_failed: {failed[0]!r}"
                )

        # LLM pool warm (fire-and-forget)
        llm_warm = getattr(pre_warm_session.llm_provider, "warm_up", None)
        if llm_warm is not None:
            asyncio.create_task(llm_warm())

        # Pre-synthesize greeting audio only when the agent speaks first. In
        # "user" mode we still keep the full session / STT / TTS / LLM warm so
        # latency on the first AI response is identical — we just don't burn
        # a TTS round-trip on audio that will never be played.
        if effective_first_speaker == "agent":
            # Strict gate: in agent-first mode we ring only when the greeting
            # is fully synthesized and buffered. If TTS fails or returns no
            # audio, abort — letting originate fire would mean the callee
            # answers to silence while we synthesize on the fly, which is the
            # exact "delay before greeting" symptom we are fixing.
            greeting_text = _build_outbound_greeting(pre_warm_session.call_session)
            chunks: list[bytes] = []
            tts_config = pre_warm_session.config
            async for audio_chunk in pre_warm_session.tts_provider.stream_synthesize(
                text=greeting_text,
                voice_id=tts_config.voice_id if tts_config else "default",
                sample_rate=(
                    tts_config.tts_sample_rate if tts_config else 8000
                ),
                call_id=pre_warm_session.call_id,
            ):
                raw = (
                    audio_chunk.data
                    if hasattr(audio_chunk, "data")
                    else audio_chunk
                )
                if raw:
                    if len(raw) % 2 != 0:
                        raw = raw[:-1]
                    if raw:
                        chunks.append(raw)

            if not chunks:
                raise RuntimeError(
                    "pre_originate_greeting_empty: TTS returned 0 audio chunks"
                )

            pre_warm_session._presynth_greeting_audio = chunks
            pre_warm_session._presynth_greeting_text = greeting_text
            logger.info(
                "pre_originate_greeting_ready chunks=%d bytes=%d text=%r",
                len(chunks), sum(len(c) for c in chunks), greeting_text[:60],
            )
        else:
            logger.info(
                "pre_originate_greeting_skipped first_speaker=user call_id=%s",
                pre_warm_session.call_id[:12],
            )

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
