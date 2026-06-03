"""
Voice Pipeline Service
Orchestrates the full voice AI pipeline: STT → LLM → TTS

Now instrumented with OpenTelemetry distributed tracing.
Every turn produces a parent span covering the full STT→LLM→TTS cycle,
with child spans per stage and latency attributes on each.
"""
import asyncio
import logging
import os
import time
from dataclasses import is_dataclass
from typing import Optional, AsyncIterator
from datetime import datetime

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation import AudioChunk, TranscriptChunk, Message, MessageRole, BargeInSignal
from app.domain.models.conversation_state import ConversationState, CallOutcomeType
from app.domain.interfaces.stt_provider import STTProvider
from app.infrastructure.llm.groq import GroqLLMProvider, LLMTimeoutError
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.interfaces.media_gateway import MediaGateway
from app.domain.services.conversation_engine import ConversationEngine
from app.domain.services.prompt_manager import PromptManager
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.llm_guardrails import LLMGuardrails, LLMGuardrailsConfig, get_guardrails
from app.services.scripts.interruption_filter import is_backchannel as _is_backchannel
from app.domain.services.latency_tracker import get_latency_tracker
from app.domain.services.global_ai_config import get_global_config
from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO as _ASK_AI_PRODUCT_INFO, PRODUCT_KEYWORDS as _ASK_AI_PRODUCT_KEYWORDS
from app.domain.services.end_session_action import (
    build_end_session_tool_instructions,
    parse_end_session_action,
)
from app.domain.services.voice_pipeline import (
    find_sentence_end as _find_sentence_end_impl,
    is_terminal_period_boundary as _is_terminal_period_boundary_impl,
    is_repetitive_transcript as _is_repetitive_transcript_impl,
)
from app.domain.services.voice_pipeline.llm_response import (
    generate_llm_response as _generate_llm_response_impl,
    response_max_sentences_for_turn as _response_max_sentences_for_turn_impl,
)
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.domain.services.voice_pipeline.turn_runner import TurnRunner
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer
from app.core.container import get_container
from app.core.postgres_adapter import Client as PostgresAdapterClient
from app.core.telemetry import get_tracer, pipeline_span, record_latency, voice_span
from app.core.telephony_observability import record_turn_silent_reason
from app.services.scripts import (
    CallState as CapturedSlotsState,
    compose_system_prompt,
    update_state_from_user_turn,
)

logger = logging.getLogger(__name__)

# History truncation + end-session-tool instructions moved to
# voice_pipeline.turn_streamer (item 2, slice 5) — they were only used by
# the streaming loop that now lives there.


def _first_speaker_label(session) -> str:
    """Return ``"agent"`` or ``"user"`` for telemetry. The bridge stashes
    the per-call first-speaker on call_session at session creation; this
    helper just reads it with a safe default for legacy code paths that
    haven't been updated yet."""
    raw = getattr(session, "_first_speaker", None) or "agent"
    value = str(raw).strip().lower()
    return "user" if value == "user" else "agent"


def _persona_label(session) -> Optional[str]:
    """Return ``session.config.persona_type`` if set, else None.

    Used for metric labelling — bounded to {lead_gen, customer_support,
    receptionist, none} downstream so cardinality stays sane.
    """
    config = getattr(session, "config", None)
    if config is None:
        return None
    raw = getattr(config, "persona_type", None)
    return str(raw) if raw else None


def _prompt_kind_label(session) -> str:
    """Return ``"inbound"`` or ``"outbound"`` for telemetry.

    Preferred source: ``session.config.direction`` — a typed
    ``Direction`` enum set by ``build_telephony_session_config``. This
    is the contract path and never lies about the call direction.

    Fallback: substring search for the inbound directive sentinel in
    the active system_prompt. This covers two edge cases:
    1. Sessions created via the legacy code path that never set
       ``direction`` on the config (older browser/ask_ai entry points).
    2. Persona-composed prompts where the bridge applied a runtime
       directive prepend without updating the config — a transitional
       state we'll eliminate when persona templates gain direction
       awareness in a future change.
    """
    config = getattr(session, "config", None)
    if config is not None and getattr(config, "direction", None) is not None:
        # Direction is a string-backed enum; comparing the value works
        # for both the enum instance and the bare string form.
        return str(config.direction.value).lower()

    # Local import keeps the latency_tracker callable on its own without
    # importing the telephony modes (used in non-telephony contexts too).
    from app.domain.services.telephony.modes.caller_first import (
        INBOUND_DIRECTIVE_SENTINEL,
    )
    prompt = getattr(session, "system_prompt", "") or ""
    return "inbound" if INBOUND_DIRECTIVE_SENTINEL in prompt else "outbound"


# Default turn-0 floor — used when the session.config doesn't carry an
# explicit tuning value (legacy code paths, ask_ai sessions). Production
# telephony reads its values from the per-tenant voice_tuning resolver
# via VoiceSessionConfig at session-build time. See voice_tuning.py.
_TURN_0_MIN_CONFIDENCE = 0.4
_TURN_0_MIN_ALPHA_CHARS = 2


def _alpha_char_count(text: str) -> int:
    """Count letters in a string (ignores digits, whitespace, punctuation)."""
    return sum(1 for ch in text if ch.isalpha())


def _should_reject_turn_0(
    transcript: str,
    confidence: Optional[float],
    *,
    min_confidence: float = _TURN_0_MIN_CONFIDENCE,
    min_alpha_chars: int = _TURN_0_MIN_ALPHA_CHARS,
) -> Optional[str]:
    """Return a short reason string if a turn-0 transcript should be
    dropped, or ``None`` if it should pass.

    Only applies when this is the first user turn — callers must check
    that before invoking this function. Splitting the predicate out keeps
    handle_turn_end readable and lets the rule be tested in isolation.

    The floors are passed in (rather than read from the module constants)
    so per-tenant tuning at T3.9 reaches this rule. Callers default the
    kwargs to the module constants when running outside a configured
    session.
    """
    if _alpha_char_count(transcript) < min_alpha_chars:
        return "too_short"
    if confidence is not None and confidence < min_confidence:
        return "low_confidence"
    return None


def _resolve_turn_0_floors(session) -> tuple[float, int]:
    """Return ``(min_confidence, min_alpha_chars)`` for the active session.

    Reads the per-tenant tuning that landed on ``session.config`` when
    the session was built; falls back to the module defaults when those
    fields are missing (legacy or non-telephony sessions)."""
    config = getattr(session, "config", None)
    if config is None:
        return _TURN_0_MIN_CONFIDENCE, _TURN_0_MIN_ALPHA_CHARS
    min_conf = getattr(config, "turn_0_min_confidence", _TURN_0_MIN_CONFIDENCE)
    min_chars = getattr(config, "turn_0_min_alpha_chars", _TURN_0_MIN_ALPHA_CHARS)
    return float(min_conf), int(min_chars)


class VoicePipelineService:
    """
    Orchestrates the full voice AI pipeline.

    Pipeline Flow:
    1. Audio Queue (from media gateway)
    2. STT Provider (streaming transcription — Deepgram)
    3. Turn Detection (EndOfTurn event)
    4. Groq LLM (streaming response generation)
    5. Google TTS (streaming audio synthesis)
    6. Output Queue (back to media gateway)

    Each turn is wrapped in an OTel trace spanning the full STT→LLM→TTS cycle.
    """

    def __init__(
        self,
        stt_provider: STTProvider,
        llm_provider: GroqLLMProvider,
        tts_provider: TTSProvider,
        media_gateway: MediaGateway,
        *,
        stt_sample_rate: int = 16000,
        tts_sample_rate: int = 24000,
        mute_during_tts: bool = True,
    ):
        self.stt_provider = stt_provider
        self.llm_provider = llm_provider
        self.tts_provider = tts_provider
        self.media_gateway = media_gateway
        self.stt_sample_rate = stt_sample_rate
        self.tts_sample_rate = tts_sample_rate
        self.mute_during_tts = mute_during_tts

        self.prompt_manager = PromptManager()
        self.transcript_service = TranscriptService()
        self.latency_tracker = get_latency_tracker()

        # Extracted collaborators (TtsPlayback/TurnRunner) are exposed as
        # lazy properties below — they read their deps off this service at
        # call time, and lazy creation keeps them working even when a test
        # builds the service via __new__ (bypassing __init__).
        self._barge_in_events: dict[str, asyncio.Event] = {}
        self._pending_llm_tasks: dict[str, asyncio.Task] = {}
        self._tracer = get_tracer()

    @property
    def _tts_playback(self) -> TtsPlayback:
        """TTS streaming/playback collaborator (item 2, slice 3). Lazily
        created so it works even when the service is built via __new__."""
        inst = self.__dict__.get("_tts_playback_inst")
        if inst is None:
            inst = TtsPlayback(self)
            self.__dict__["_tts_playback_inst"] = inst
        return inst

    @property
    def _turn_runner(self) -> TurnRunner:
        """Per-turn LLM+TTS execution collaborator (item 2, slice 4). Lazily
        created so it works even when the service is built via __new__."""
        inst = self.__dict__.get("_turn_runner_inst")
        if inst is None:
            inst = TurnRunner(self)
            self.__dict__["_turn_runner_inst"] = inst
        return inst

    @property
    def _turn_streamer(self) -> TurnStreamer:
        """LLM-token streaming + sentence-paced TTS collaborator (item 2,
        slice 5). Lazily created so it works under __new__-built services."""
        inst = self.__dict__.get("_turn_streamer_inst")
        if inst is None:
            inst = TurnStreamer(self)
            self.__dict__["_turn_streamer_inst"] = inst
        return inst

    async def _await_task_after_cancel(self, task: asyncio.Task, call_id: str, label: str) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "%s task raised before cancellation completed for call %s: %s",
                label,
                call_id,
                exc,
                exc_info=True,
            )
            return

        exc = task.exception()
        if exc is not None:
            logger.warning(
                "%s task completed with exception for call %s: %s",
                label,
                call_id,
                exc,
                exc_info=True,
            )

    def _record_silent_turn(self, call_id: str, reason: str) -> None:
        record_turn_silent_reason(reason)
        logger.warning(
            "turn_silent_reason call_id=%s reason=%s",
            call_id,
            reason,
            extra={"call_id": call_id, "turn_silent_reason": reason},
        )

    # Sentence-budget logic extracted to voice_pipeline.llm_response (item 2,
    # slice 2). Uses no instance state, so it's a static delegator — call
    # sites (self._response_max_sentences_for_turn(...)) and the tests that
    # call it on the instance are unchanged.
    _response_max_sentences_for_turn = staticmethod(_response_max_sentences_for_turn_impl)

    def _barge_in_event_for(self, session: CallSession) -> asyncio.Event:
        event = self._barge_in_events.get(session.call_id)
        if event is None:
            event = getattr(session, "barge_in_event", None)
        if event is None:
            event = asyncio.Event()
            try:
                session.barge_in_event = event
            except Exception:
                pass
        self._barge_in_events[session.call_id] = event
        return event

    def _register_active_turn_task(self, call_id: str, task: asyncio.Task) -> None:
        self._pending_llm_tasks[call_id] = task

    # Implementation extracted to voice_pipeline.transcript_heuristics
    # (item 2). Kept as a static method so the existing public interface
    # and call sites are unchanged.
    _is_repetitive_transcript = staticmethod(_is_repetitive_transcript_impl)

    @staticmethod
    def _is_ask_ai_session(session: CallSession) -> bool:
        return getattr(session, "campaign_id", None) == "ask-ai"

    @staticmethod
    def _supports_llm_end_session_action(session: CallSession) -> bool:
        return getattr(session, "campaign_id", None) != "voice-demo"

    @staticmethod
    def _parse_ask_ai_end_session_action(text: str) -> Optional[dict[str, str]]:
        return parse_end_session_action(text)

    async def _shutdown_session_for_end_action(
        self,
        session: CallSession,
        websocket: Optional[WebSocket],
        reason: str,
        farewell: str,
    ) -> None:
        call_id = session.call_id
        is_ask_ai = self._is_ask_ai_session(session)
        logger.info(
            "llm_end_session_action call_id=%s reason=%s session_type=%s",
            call_id[:12],
            reason,
            "ask_ai" if is_ask_ai else "telephony",
        )
        session.current_user_input = ""
        session.llm_active = False
        session.tts_active = False
        session.state = CallState.ENDING

        if farewell:
            try:
                if hasattr(self.media_gateway, "start_playback_tracking"):
                    maybe_awaitable = self.media_gateway.start_playback_tracking(call_id)
                    if hasattr(maybe_awaitable, "__await__"):
                        await maybe_awaitable

                interrupted = await self.synthesize_and_send_audio(
                    session,
                    farewell,
                    websocket,
                    track_latency=False,
                )
                if (
                    not interrupted
                    and websocket
                    and hasattr(self.media_gateway, "wait_for_playback_complete")
                ):
                    await websocket.send_json({"type": "tts_audio_complete"})
                    await self.media_gateway.wait_for_playback_complete(call_id)
                elif not interrupted and not is_ask_ai:
                    await asyncio.sleep(0.8)
            except Exception as exc:
                logger.debug("End-session farewell playback failed before close: %s", exc)

        hangup = getattr(self.media_gateway, "hangup_call", None)
        if callable(hangup):
            try:
                await hangup(call_id, reason)
            except Exception as exc:
                logger.debug("End-session telephony hangup failed: %s", exc)

        try:
            await self.media_gateway.on_call_ended(call_id, reason)
        except Exception as exc:
            logger.debug("End-session gateway shutdown failed: %s", exc)

        if websocket:
            try:
                await websocket.send_json(
                    {
                        "type": "session_ending",
                        "reason": reason,
                    }
                )
            except Exception as exc:
                logger.debug("End-session notification failed: %s", exc)

            if is_ask_ai:
                try:
                    await websocket.close(code=1000, reason=reason)
                except Exception as exc:
                    logger.debug("Ask AI end-session websocket close failed: %s", exc)

        session.state = CallState.ENDED

    # Sentence-segmentation logic (+ its CLAUSE_CONJUNCTIONS /
    # COMMON_ABBREVIATIONS constants) extracted to
    # voice_pipeline.sentence_segmentation (item 2). Kept as static methods
    # so call sites — self._find_sentence_end(...) and the
    # VoicePipelineService._find_sentence_end(...) characterization tests —
    # are unchanged.
    _is_terminal_period_boundary = staticmethod(_is_terminal_period_boundary_impl)
    _find_sentence_end = staticmethod(_find_sentence_end_impl)

    # ── Pipeline lifecycle ─────────────────────────────────────────

    async def start_pipeline(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id
        # Reuse the CallSession's barge-in event if one was already created
        # (e.g. by the orchestrator for the greeting).  This ensures Flux's
        # StartOfTurn callback sets the SAME event the greeting TTS loop is
        # watching — without this, greeting playback cannot be interrupted.
        existing = getattr(session, "barge_in_event", None)
        if existing is not None:
            self._barge_in_events[call_id] = existing
        else:
            self._barge_in_events[call_id] = asyncio.Event()
            session.barge_in_event = self._barge_in_events[call_id]

        # Wire barge-in event into TelephonyMediaGateway so its pacing loop can
        # exit early instead of draining a full TTS chunk before detection.
        set_barge_in = getattr(self.media_gateway, "set_barge_in_event", None)
        if set_barge_in:
            set_barge_in(call_id, self._barge_in_events[call_id])

        with voice_span("pipeline.start", call_id=call_id,
                        tenant_id=getattr(session, "tenant_id", None)) as span:
            span.set_attribute("voice.call_id", call_id)
            logger.info(
                "pipeline_start",
                extra={"call_id": call_id, "timestamp": datetime.utcnow().isoformat()},
            )
            try:
                session.stt_active = True
                await self.process_audio_stream(session, agent_config, websocket)
            except Exception as e:
                span.record_exception(e)
                logger.error(
                    f"Pipeline error: {e}",
                    extra={"call_id": call_id},
                    exc_info=True,
                )
            finally:
                # Cancel orphaned LLM task — asyncio children are NOT auto-cancelled
                # when their parent task is cancelled.
                pending_task = self._pending_llm_tasks.pop(call_id, None)
                if pending_task:
                    if not pending_task.done():
                        pending_task.cancel()
                    await self._await_task_after_cancel(pending_task, call_id, "orphaned_llm")
                # Remove barge-in event so a future session cannot inherit stale state.
                self._barge_in_events.pop(call_id, None)
                session.stt_active = False
                self.latency_tracker.cleanup_call(call_id)
                logger.info("pipeline_end", extra={"call_id": call_id})

    async def process_audio_stream(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        async def audio_stream() -> AsyncIterator[AudioChunk]:
            queue = self.media_gateway.get_audio_queue(call_id)
            if queue is None:
                logger.error(
                    "audio_stream_no_queue call_id=%s — media gateway has no "
                    "session registered; ALL caller audio will be lost!",
                    call_id,
                )
                return
            logger.info(
                "audio_stream_started call_id=%s queue_size=%d stt_active=%s",
                call_id, queue.qsize(), session.stt_active,
            )
            _first_chunk_logged = False
            _chunks_yielded = 0
            # Diagnostic: track audio level to distinguish silence from speech
            # in cases where Deepgram never fires StartOfTurn. Logged every
            # ~1s so we can see whether real voice is on the wire.
            import struct as _struct
            _level_bucket_t0 = asyncio.get_event_loop().time()
            _level_max = 0
            _level_sum_sq = 0.0
            _level_samples = 0
            while session.stt_active:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.02)
                    if chunk:
                        _chunks_yielded += 1
                        raw_bytes = chunk if isinstance(chunk, bytes) else getattr(chunk, "data", b"")
                        if not _first_chunk_logged:
                            _first_chunk_logged = True
                            logger.info(
                                "audio_stream_first_chunk call_id=%s "
                                "chunk_len=%d — audio now flowing to STT",
                                call_id, len(raw_bytes),
                            )
                        # Accumulate audio-level stats on 16-bit mono PCM frames
                        if raw_bytes and len(raw_bytes) >= 2 and len(raw_bytes) % 2 == 0:
                            try:
                                samples = _struct.unpack(f"<{len(raw_bytes)//2}h", raw_bytes)
                                for s in samples:
                                    if abs(s) > _level_max:
                                        _level_max = abs(s)
                                    _level_sum_sq += s * s
                                _level_samples += len(samples)
                            except Exception:
                                pass
                        # Emit a level log roughly once per second
                        _now = asyncio.get_event_loop().time()
                        if _now - _level_bucket_t0 >= 1.0 and _level_samples > 0:
                            import math as _math
                            rms = _math.sqrt(_level_sum_sq / _level_samples)
                            # Speech ~ rms > 500; quiet room ~ rms < 100; pure silence ~ 0
                            logger.info(
                                "audio_level call_id=%s window_s=%.1f chunks=%d "
                                "rms=%.0f peak=%d samples=%d "
                                "(>500=speech-likely, <100=silence-likely)",
                                call_id, _now - _level_bucket_t0,
                                _chunks_yielded, rms, _level_max, _level_samples,
                            )
                            # Stash on the session so user-first's silence
                            # handler can read pre-Flux audio activity. Without
                            # this signal it fires the fallback greeting on
                            # top of the caller's first "Hello?" because Flux
                            # hadn't yet committed StartOfTurn.
                            try:
                                session.last_audio_rms = rms
                                session.last_audio_peak = _level_max
                                session.last_audio_rms_at = _now
                            except Exception:
                                pass
                            _level_bucket_t0 = _now
                            _level_max = 0
                            _level_sum_sq = 0.0
                            _level_samples = 0
                        yield AudioChunk(data=raw_bytes) if isinstance(chunk, bytes) else chunk
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Audio stream error: {e}", extra={"call_id": call_id})
                    break
            logger.info(
                "audio_stream_ended call_id=%s chunks_yielded=%d stt_active=%s",
                call_id, _chunks_yielded, session.stt_active,
            )

        # STT span wraps the full transcription stream
        with pipeline_span("stt", call_id=call_id, provider="deepgram",
                           tenant_id=getattr(session, "tenant_id", None)) as stt_span:
            t_stt_start = time.monotonic()

            # Direct barge-in callback: sets the event immediately from the STT
            # background task, even while the pipeline loop is blocked in
            # handle_turn_end.  This is the only reliable way to stop TTS mid-stream.
            def _on_barge_in_direct() -> None:
                event = self._barge_in_events.get(call_id)
                if event:
                    event.set()
                current_metrics = self.latency_tracker.get_metrics(call_id)
                if not current_metrics or current_metrics.turn_id != session.turn_id:
                    self.latency_tracker.start_turn(call_id, session.turn_id)
                self.latency_tracker.mark_listening_start(call_id)

            # ── Silence monitor (telephony only) ───────────────────────────────
            # After 5-7 seconds of continuous caller silence the agent asks if the
            # caller is still there.  Phrases are varied each time to avoid sounding
            # robotic.  Runs in parallel with the STT consumer loop; cancelled when
            # the pipeline exits.  Disabled for Ask AI (browser sessions).
            _SILENCE_PHRASES = [
                "Are you still there?",
                "Still with me?",
                "Hello — you still on the line?",
                "Hey, just checking — can you hear me?",
                "Did I lose you?",
                "You still there? I'm here.",
                "Just making sure I haven't lost you — you there?",
                "Hello? Are you still with me?",
            ]

            async def _silence_monitor() -> None:
                import random
                # Minimum pause after AI finishes speaking before silence counts.
                # Prevents firing immediately after TTS ends while caller is
                # drawing breath to respond.
                _TTS_GRACE_S = 3.0
                _last_event_at = datetime.utcnow()
                _tts_ended_at: Optional[datetime] = None
                _was_active: bool = False
                # Only arm after the FIRST complete AI response — user-speaks-first
                # mode means there is natural silence at call start that must not
                # trigger the monitor.
                _had_first_exchange: bool = False
                consecutive = 0
                _MAX_CONSECUTIVE = 2  # give up after 2 unanswered checks

                while session.stt_active and consecutive < _MAX_CONSECUTIVE:
                    await asyncio.sleep(1.0)  # poll every second

                    if not session.stt_active:
                        break

                    currently_active = session.tts_active or session.llm_active

                    # AI is speaking or processing — reset baseline
                    if currently_active:
                        _last_event_at = datetime.utcnow()
                        _tts_ended_at = None
                        _was_active = True
                        consecutive = 0
                        continue

                    # TTS/LLM just went inactive — start grace period clock
                    if _was_active and not currently_active:
                        _tts_ended_at = datetime.utcnow()
                        _last_event_at = _tts_ended_at
                        _had_first_exchange = True  # AI has spoken at least once
                        _was_active = False
                        consecutive = 0
                        continue  # give the caller the full grace period first

                    _was_active = False

                    # Don't arm until after the first AI response (user-speaks-first)
                    if not _had_first_exchange:
                        _last_event_at = datetime.utcnow()
                        continue

                    # Enforce post-TTS grace period before counting silence
                    if _tts_ended_at is not None:
                        if (datetime.utcnow() - _tts_ended_at).total_seconds() < _TTS_GRACE_S:
                            continue

                    # User is already speaking (StartOfTurn detected before transcript)
                    _barge_ev = self._barge_in_events.get(call_id)
                    if _barge_ev and _barge_ev.is_set():
                        _last_event_at = datetime.utcnow()
                        consecutive = 0
                        continue

                    # User spoke since our last baseline — reset
                    if session.last_activity_at > _last_event_at:
                        _last_event_at = session.last_activity_at
                        consecutive = 0
                        continue

                    # How long has it been since any activity?
                    elapsed = (datetime.utcnow() - _last_event_at).total_seconds()
                    silence_limit = random.uniform(5.0, 7.0)
                    if elapsed < silence_limit:
                        continue

                    # Silence threshold exceeded — ask with a varied phrase
                    phrase = random.choice(_SILENCE_PHRASES)
                    consecutive += 1
                    logger.info(
                        "[SilenceMonitor] %s — %.1fs silence, asking (%d/%d): %r",
                        call_id[:12], elapsed, consecutive, _MAX_CONSECUTIVE, phrase,
                    )
                    try:
                        await self.synthesize_and_send_audio(session, phrase, websocket)
                    except Exception as _sm_exc:
                        logger.debug("[SilenceMonitor] TTS failed: %s", _sm_exc)

                    # Reset baseline with grace period after our own TTS phrase
                    _tts_ended_at = datetime.utcnow()
                    _last_event_at = _tts_ended_at

                logger.debug(
                    "[SilenceMonitor] %s exiting (consecutive=%d stt_active=%s)",
                    call_id[:12], consecutive, session.stt_active,
                )

            # Start silence monitor only for telephony (not Ask AI browser sessions)
            _silence_task: Optional[asyncio.Task] = None
            if getattr(session, "campaign_id", "ask-ai") != "ask-ai":
                _silence_task = asyncio.create_task(_silence_monitor())

            try:
                async for transcript in self.stt_provider.stream_transcribe(
                    audio_stream(),
                    call_id=call_id,
                    on_barge_in=_on_barge_in_direct,
                ):
                    await self.handle_transcript(session, transcript, websocket)
            except Exception as e:
                stt_span.record_exception(e)
                logger.error(f"STT stream error: {e}", extra={"call_id": call_id})
            finally:
                if _silence_task and not _silence_task.done():
                    _silence_task.cancel()
                    try:
                        await _silence_task
                    except asyncio.CancelledError:
                        pass
                record_latency(stt_span, "stt", (time.monotonic() - t_stt_start) * 1000)
                get_stats = getattr(self.stt_provider, "get_stream_stats", None)
                if get_stats:
                    stats = get_stats(call_id)
                    if stats:
                        for k, v in stats.items():
                            try:
                                stt_span.set_attribute(f"stt.{k}", v)
                            except Exception as _e:
                                logger.debug("stt_span_attr k=%s: %s", k, _e)

    # ── Transcript handling ────────────────────────────────────────

    async def handle_transcript(
        self,
        session: CallSession,
        transcript,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        if isinstance(transcript, BargeInSignal):
            await self.handle_barge_in(session, websocket)
            return

        if transcript.metadata and transcript.metadata.get("resumed"):
            logger.info(f"TurnResumed for call {call_id} — cancelling speculative LLM")
            session.llm_active = False
            if call_id in self._pending_llm_tasks:
                task = self._pending_llm_tasks.pop(call_id)
                if not task.done():
                    task.cancel()
                await self._await_task_after_cancel(task, call_id, "speculative_llm")
            # Roll back any messages the speculative handle_turn_end appended
            # before being cancelled.  Without this, orphaned user/assistant
            # messages corrupt the conversation context for subsequent turns.
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            return

        metadata = transcript.metadata or {}
        self.transcript_service.bind_call_identity(call_id, session.talklee_call_id)

        # Ensure latency tracker is aligned with current turn ID.
        # Guard: do NOT reset tracker while LLM/TTS is actively processing.
        # session.turn_id is pre-incremented before _run_turn is created, so a
        # tracker turn_id mismatch during active processing is expected — it is
        # NOT an indication the tracker is stale.
        current_metrics = self.latency_tracker.get_metrics(call_id)
        if (not current_metrics or current_metrics.turn_id != session.turn_id) and not session.llm_active:
            self.latency_tracker.start_turn(call_id, session.turn_id)
            self.latency_tracker.mark_listening_start(call_id)

        logger.info(
            "transcript_received",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "text": transcript.text,
                "is_final": transcript.is_final,
                "confidence": transcript.confidence,
                "eager": metadata.get("eager", False),
            },
        )

        if websocket and transcript.text:
            try:
                msg_type = "transcript_eager" if metadata.get("eager") else "transcript"
                await websocket.send_json({
                    "type": msg_type,
                    "text": transcript.text,
                    "is_final": transcript.is_final,
                    "confidence": transcript.confidence,
                })
            except Exception as e:
                logger.warning(f"Failed to send transcript to websocket: {e}")

        if self.stt_provider.detect_turn_end(transcript):
            # Run as a task (not awaited) so the consumer stays unblocked and
            # can process a TurnResumed that arrives before the LLM completes.
            #
            # Why this matters: Deepgram's barge-in state machine occasionally
            # sends EndOfTurn → TurnResumed in that order (e.g. user pauses
            # mid-phrase → EndOfTurn fires → user continues → TurnResumed).
            # With the old `await handle_turn_end(...)` pattern the consumer
            # was blocked for the full LLM+TTS duration (~2-10s) — TurnResumed
            # sat in the queue and arrived too late to cancel the LLM call.
            # Result: AI responded to a partial/stale transcript ("But") while
            # the user's real question ("But what is your offering?") was split
            # across two EndOfTurns, producing a totally off-topic answer.
            existing = self._pending_llm_tasks.get(call_id)
            if existing and not existing.done():
                # A speculative or prior final task is still in flight — skip.
                logger.debug(
                    "final turn_end skipped: pending task already running for %s",
                    call_id[:12],
                )
                return
            session._speculative_history_len = len(session.conversation_history)
            task = asyncio.create_task(
                self.handle_turn_end(session, websocket, source="final")
            )
            self._pending_llm_tasks[call_id] = task
            return

        if metadata.get("eager") and transcript.text:
            if not session.llm_active and call_id not in self._pending_llm_tasks:
                session.current_user_input = transcript.text
                # Stash the transcript's confidence alongside the text so
                # handle_turn_end can apply a turn-0 floor on garbled
                # mishears without re-acquiring the transcript object.
                session._last_transcript_confidence = transcript.confidence
                # Snapshot history length so TurnResumed can roll back any
                # messages the speculative task appends before cancellation.
                session._speculative_history_len = len(session.conversation_history)
                # Speculatively start LLM now (EagerEndOfTurn fired — 150–250ms before
                # EndOfTurn). If user keeps talking, TurnResumed cancels this task via
                # the handle_transcript "resumed" branch above (session.llm_active=False
                # + task.cancel()).
                task = asyncio.create_task(
                    self.handle_turn_end(session, websocket, source="speculative")
                )
                self._pending_llm_tasks[call_id] = task
            return

        if transcript.text:
            event_type = "eager_end_of_turn" if metadata.get("eager") else "update"
            if transcript.is_final:
                event_type = "end_of_turn"

            self.transcript_service.accumulate_turn(
                call_id=call_id,
                role="user",
                content=transcript.text,
                confidence=transcript.confidence,
                talklee_call_id=session.talklee_call_id,
                turn_index=session.turn_id,
                event_type=event_type,
                is_final=transcript.is_final,
                audio_window_start=metadata.get("audio_window_start"),
                audio_window_end=metadata.get("audio_window_end"),
                include_in_plaintext=transcript.is_final,
                metadata=metadata,
            )
            self.latency_tracker.mark_stt_first_transcript(call_id)
            session.current_user_input = transcript.text
            session._last_transcript_confidence = transcript.confidence
            session.update_activity()

    # ── Turn end — the full LLM + TTS cycle ───────────────────────

    async def handle_turn_end(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
        source: str = "final",
    ) -> None:
        call_id = session.call_id
        full_transcript = session.current_user_input.strip()
        tenant_id = getattr(session, "tenant_id", None)

        if not full_transcript:
            logger.debug("Empty transcript, skipping turn", extra={"call_id": call_id})
            return

        # Turn-0 floor — protects the first AI reply (the one that "anchors"
        # the conversation) from being driven by a misheard fragment. A bad
        # turn 0 is uniquely costly: the LLM commits to a wrong topic and
        # subsequent turns inherit that drift. A bad turn N+1 is a normal
        # disfluency the model can recover from.
        # Only the very first user utterance is gated; once the conversation
        # is open we trust the existing repetitive/backchannel filters below.
        _has_prior_user_turn_for_floor = any(
            m.role == MessageRole.USER for m in session.conversation_history
        )
        if not _has_prior_user_turn_for_floor:
            confidence = getattr(session, "_last_transcript_confidence", None)
            min_conf, min_chars = _resolve_turn_0_floors(session)
            reject_reason = _should_reject_turn_0(
                full_transcript,
                confidence,
                min_confidence=min_conf,
                min_alpha_chars=min_chars,
            )
            if reject_reason is not None:
                logger.info(
                    "turn_0_transcript_rejected reason=%s call=%s "
                    "transcript=%r confidence=%s min_conf=%s min_chars=%d "
                    "— letting Flux re-emit",
                    reject_reason, call_id[:12], full_transcript[:40],
                    confidence, min_conf, min_chars,
                )
                try:
                    from app.infrastructure.metrics.voice_metrics import (
                        record_turn_0_rejection,
                    )
                    record_turn_0_rejection(reject_reason)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "voice_metrics_rejection_record_failed err=%s", exc,
                    )
                # Clear so the next real transcript isn't merged with this one.
                try:
                    session.current_user_input = ""
                except AttributeError:
                    pass
                return

        # Guard against the confirmed Deepgram Flux hallucination bug (GitHub #1524)
        # where the STT model outputs repetitive nonsense text ("blah blah blah…").
        # Heuristic: if a single word accounts for >50% of a 6+ word transcript,
        # treat it as a hallucination and skip — avoids sending garbage to the LLM.
        if self._is_repetitive_transcript(full_transcript):
            logger.warning(
                "Repetitive STT transcript likely hallucination, skipping turn",
                extra={"call_id": call_id, "transcript": full_transcript[:80]},
            )
            return

        # Backchannel suppression — short listening sounds ("hmm",
        # "yeah", "uh huh", "mm") are NOT real turns. Without this, the
        # LLM generates a full response to a non-event and loses the
        # conversation's thread. The persona prompts also instruct the
        # model on this at the language level — belt AND braces.
        #
        # Exception: never suppress the callee's FIRST utterance of the
        # call. In user-first mode that utterance IS the conversation
        # opener (a "Hello?" that the STT may briefly mis-hear as "No.")
        # — suppressing it leaves the agent silent, the callee repeats
        # themselves, and 5–6 seconds of perceived dead air pile up
        # before Flux finally lands a clean transcript. In agent-first
        # mode the first user utterance is their reply to the greeting
        # ("yeah", "sure", "uh-huh") and must reach the LLM as a real
        # affirmative, not be filtered out as noise.
        _has_prior_user_turn = any(
            m.role == MessageRole.USER for m in session.conversation_history
        )
        if _is_backchannel(full_transcript) and _has_prior_user_turn:
            logger.info(
                "backchannel_suppressed transcript=%r call=%s",
                full_transcript, call_id[:12],
            )
            # Clear the session's pending input so the old transcript
            # doesn't carry into the next real turn.
            try:
                session.current_user_input = ""
            except AttributeError:
                pass
            return
        elif _is_backchannel(full_transcript):
            logger.info(
                "backchannel_allowed_turn0 transcript=%r call=%s — "
                "first user utterance, never suppressed",
                full_transcript, call_id[:12],
            )

        # Clear any barge-in event that was set by the user's own StartOfTurn that
        # triggered this turn.  Deepgram Flux fires StartOfTurn for ALL speech —
        # including normal listening-phase input — which sets barge_in_event via
        # _on_barge_in_direct().  Without this clear, synthesize_and_send_audio sees
        # the stale event as a "barge-in during LLM" and returns immediately without
        # playing any audio, leaving the caller in silence.
        # If the user speaks AGAIN while the LLM is generating, Deepgram fires a new
        # StartOfTurn → event is set again → TTS is correctly suppressed at that point.
        barge_in_event = self._barge_in_events.get(call_id)
        if barge_in_event:
            barge_in_event.clear()

        current_task = asyncio.current_task()
        pending_task = self._pending_llm_tasks.get(call_id)
        if pending_task and pending_task.done():
            self._pending_llm_tasks.pop(call_id, None)
            pending_task = None

        if pending_task and pending_task is not current_task:
            # Elevated to INFO from DEBUG — when this guard fires, a turn
            # is silently dropped, which has historically masked "the
            # agent went silent" mysteries during latency triage. INFO
            # keeps it visible without polluting hot-path logs.
            logger.info(
                "turn_skipped_pending_task",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "source": source,
                    "transcript": full_transcript[:80],
                },
            )
            return

        # Guard: skip if a concurrent LLM/TTS (e.g. greeting) is already running.
        # session.llm_active is set True in _send_outbound_greeting and in this
        # function; it is reset to False in the finally block below.
        if session.llm_active and pending_task is not current_task:
            # Elevated to INFO from DEBUG — same reason as above. If this
            # ever fires on turn 0 of a real call, it indicates llm_active
            # leaked True from a previous flow (e.g. a greeting that
            # raised before its finally-reset ran).
            logger.info(
                "turn_skipped_llm_busy",
                extra={
                    "call_id": call_id,
                    "turn_id": session.turn_id,
                    "source": source,
                    "transcript": full_transcript[:80],
                },
            )
            return

        logger.info(
            "turn_end",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "source": source,
                "transcript": full_transcript,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # Clear barge-in event now that EndOfTurn has fired (user stopped speaking).
        # Stale barge-in signals from the user's own speech turn are now irrelevant.
        # Any NEW barge-in signal that fires AFTER this point means the user started
        # speaking again WHILE the AI is processing/responding — and must NOT be wiped.
        barge_in_event = self._barge_in_events.get(call_id)
        if barge_in_event:
            barge_in_event.clear()

        # Parent span for the complete LLM+TTS turn
        with voice_span(
            "turn",
            call_id=call_id,
            tenant_id=tenant_id,
            **{"voice.turn.id": session.turn_id, "voice.turn.transcript": full_transcript[:200]},
        ) as turn_span:
            session.state = CallState.PROCESSING
            session.llm_active = True
            self.latency_tracker.mark_speech_end(call_id)
            self.latency_tracker.mark_llm_start(call_id)

            # NOTE: user message is appended inside _run_turn, which owns the
            # history snapshot + rollback on error/cancellation.  Do NOT append
            # here — it would produce a duplicate entry visible to the LLM on
            # every turn, wasting tokens and corrupting conversation context.

            try:
                # ── LLM + TTS (sentence-pipelined) ────────────────
                with pipeline_span("llm_tts", call_id=call_id, provider="groq",
                                   tenant_id=tenant_id) as llm_tts_span:
                    t0 = time.monotonic()
                    response_text, llm_latency, tts_latency = await self._run_turn(
                        session, full_transcript, websocket, session.turn_id
                    )
                    total_wall = (time.monotonic() - t0) * 1000

                    llm_tts_span.set_attribute("llm.response_chars", len(response_text))
                    llm_tts_span.set_attribute("llm.latency_ms", round(llm_latency, 1))
                    llm_tts_span.set_attribute("tts.latency_ms", round(tts_latency, 1))
                    session.add_latency_measurement("llm", llm_latency)
                    session.add_latency_measurement("tts", tts_latency)

                logger.info(
                    "llm_response",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "response": response_text,
                        "llm_latency_ms": round(llm_latency, 1),
                        "tts_latency_ms": round(tts_latency, 1),
                    },
                )

                # total_wall is the actual wall-clock time (LLM and TTS overlap with pipelining)
                session.add_latency_measurement("total_turn", total_wall)

                # Attach full breakdown to parent turn span
                turn_span.set_attribute("voice.turn.llm_ms", round(llm_latency, 1))
                turn_span.set_attribute("voice.turn.tts_ms", round(tts_latency, 1))
                turn_span.set_attribute("voice.turn.total_ms", round(total_wall, 1))

                # Pull detailed sub-metrics from LatencyTracker and attach to span
                tracked = self.latency_tracker.get_metrics(call_id)
                if tracked:
                    for attr, val in [
                        ("stt_first_transcript", tracked.stt_first_transcript_ms),
                        ("llm_first_token",      tracked.llm_first_token_ms),
                        ("tts_first_chunk",      tracked.tts_first_chunk_ms),
                        ("response_start",       tracked.response_start_latency_ms),
                        ("total",                tracked.total_latency_ms),
                    ]:
                        if val is not None and val >= 0:
                            session.add_latency_measurement(attr, val)
                            turn_span.set_attribute(f"voice.latency.{attr}_ms", round(val, 1))
                    self.latency_tracker.log_metrics(call_id)
                    # First-turn telemetry — fires exactly once per call, on
                    # the first turn that actually produced audio. Cold-start
                    # costs land here and are otherwise invisible in the
                    # per-turn aggregate.
                    _mode = _first_speaker_label(session)
                    _kind = _prompt_kind_label(session)
                    _persona = _persona_label(session)
                    self.latency_tracker.log_first_turn_if_applicable(
                        call_id,
                        mode=_mode,
                        prompt_kind=_kind,
                        persona=_persona,
                    )
                    # Per-turn Prometheus observation (T4-B2). Mirrors the
                    # log_metrics structured log so dashboards and logs
                    # never disagree on what happened. Local import keeps
                    # the pipeline callable when prometheus_client isn't
                    # available (tests, lightweight scripts).
                    if tracked.total_latency_ms is not None:
                        try:
                            from app.infrastructure.metrics.voice_metrics import (
                                observe_turn_latency_seconds,
                            )
                            observe_turn_latency_seconds(
                                tracked.total_latency_ms / 1000.0,
                                mode=_mode,
                                prompt_kind=_kind,
                                persona=_persona,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "voice_metrics_turn_observe_failed err=%s", exc,
                            )

                logger.info(
                    "turn_complete",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "llm_latency_ms": round(llm_latency, 1),
                        "tts_latency_ms": round(tts_latency, 1),
                        "total_latency_ms": round(total_wall, 1),
                    },
                )

                # Slow-turn marker. Per Hamming.ai's 2026 production benchmarks
                # (P50 1.4s, P95 4.3s, P99 8.4s) and Twilio's mouth-to-ear
                # upper limit of 1400ms, anything past 1500ms response_start
                # is what callees feel as "this call sounds different". Tag
                # the span and emit a structured log so outliers can be
                # grepped from the firehose without averaging variance away.
                _response_start = (
                    tracked.response_start_latency_ms if tracked else None
                )
                if _response_start is not None and _response_start > 1500:
                    turn_span.set_attribute("voice.turn.slow", True)
                    logger.warning(
                        "voice_slow_turn call_id=%s turn_id=%d "
                        "response_start_ms=%.1f stt_first_ms=%s "
                        "llm_first_token_ms=%s tts_first_chunk_ms=%s "
                        "llm_total_ms=%.1f tts_total_ms=%.1f transcript=%r",
                        call_id[:12],
                        session.turn_id,
                        _response_start,
                        round(tracked.stt_first_transcript_ms, 1) if tracked.stt_first_transcript_ms else "n/a",
                        round(tracked.llm_first_token_ms, 1) if tracked.llm_first_token_ms else "n/a",
                        round(tracked.tts_first_chunk_ms, 1) if tracked.tts_first_chunk_ms else "n/a",
                        round(llm_latency, 1),
                        round(tts_latency, 1),
                        full_transcript[:80],
                    )

                if websocket:
                    try:
                        await websocket.send_json({
                            "type": "turn_complete",
                            "llm_latency_ms": round(llm_latency, 1),
                            "tts_latency_ms": round(tts_latency, 1),
                            "total_latency_ms": round(total_wall, 1),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send turn_complete to websocket: {e}")

                # Flush transcript to DB incrementally
                try:
                    container = get_container()
                    if container.is_initialized:
                        postgres_client = PostgresAdapterClient(container.db_pool)
                        await self.transcript_service.flush_to_database(
                            call_id=call_id,
                            db_client=postgres_client,
                            tenant_id=tenant_id,
                            talklee_call_id=session.talklee_call_id,
                        )
                except Exception as e:
                    logger.warning(f"Failed to flush transcript for {call_id}: {e}")

            except Exception as e:
                turn_span.record_exception(e)
                logger.error(
                    f"Error processing turn: {e}",
                    extra={"call_id": call_id, "error": str(e)},
                    exc_info=True,
                )
                # GAP 7 — LLM failure apology: play a short TTS apology so the
                # caller knows something went wrong rather than hearing silence.
                # Use a bare try so an apology TTS failure never masks the original error.
                try:
                    await self.synthesize_and_send_audio(
                        session,
                        "I'm sorry, I'm having trouble right now. Please try again in a moment.",
                        websocket,
                    )
                except Exception:
                    pass
            finally:
                pending_task = self._pending_llm_tasks.get(call_id)
                if pending_task is current_task or (pending_task and pending_task.done()):
                    self._pending_llm_tasks.pop(call_id, None)
                session.llm_active = False
                # Clear speculative snapshot — turn completed normally so
                # the messages it appended are valid and must not be rolled back.
                session._speculative_history_len = None
                session.increment_turn()

    # ── Barge-in ──────────────────────────────────────────────────

    async def handle_barge_in(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id
        logger.info(
            "barge_in_detected",
            extra={
                "call_id": call_id,
                "turn_id": session.turn_id,
                "timestamp": datetime.utcnow().isoformat(),
                "tts_active": session.tts_active,
            },
        )
        if call_id in self._barge_in_events:
            self._barge_in_events[call_id].set()

        # Cancel any in-flight speculative LLM task (EagerEndOfTurn fired before
        # the user finished speaking).  Without this, the speculative task keeps
        # running until TurnResumed arrives — wasting LLM compute and potentially
        # writing a stale user+assistant message pair to conversation history.
        speculative_task = self._pending_llm_tasks.pop(call_id, None)
        if speculative_task:
            if not speculative_task.done():
                speculative_task.cancel()
            await self._await_task_after_cancel(speculative_task, call_id, "speculative_llm")
            # Roll back any history the speculative task may have appended
            # before being cancelled (mirrors the TurnResumed rollback path).
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            logger.debug(
                "barge_in: cancelled speculative LLM task for %s", call_id[:12]
            )

        # Annotate the last assistant message so the LLM knows the caller
        # did not hear the full response.  This prevents the LLM from
        # referencing content from the unheard portion and guides it to
        # respond to the caller's interruption instead.
        #
        # IMPORTANT: only annotate when TTS was actively playing at the
        # moment the barge-in fired.  StartOfTurn also fires when the user
        # simply starts their next question after the AI has already finished
        # speaking (session.tts_active=False).  Annotating in that case
        # falsely tells the LLM "you were interrupted" — it then behaves as
        # if the previous response was incomplete and loses conversational
        # context, making replies feel disconnected.
        if session.tts_active and session.conversation_history:
            last_msg = session.conversation_history[-1]
            if (
                last_msg.role == MessageRole.ASSISTANT
                and "[interrupted by caller]" not in last_msg.content
            ):
                last_msg.content = (
                    last_msg.content.rstrip() + " [interrupted by caller]"
                )

        session.current_ai_response = ""
        session.current_user_input = ""  # Reset so stale transcript never reaches LLM
        session.tts_active = False
        session.state = CallState.LISTENING
        # Immediately tell the media gateway (and downstream C++ gateway) to
        # discard any buffered TTS audio so the caller stops hearing the AI.
        try:
            await self.media_gateway.clear_output_buffer(call_id)
        except Exception as _exc:
            logger.debug("clear_output_buffer on barge-in failed: %s", _exc)
        # Tell the TTS provider to cancel any server-side buffered audio.
        # For Deepgram this sends a Clear message that stops further audio
        # chunks from being generated — critical for fast barge-in (<200ms).
        clear_tts = getattr(self.tts_provider, "clear_queue", None)
        if clear_tts:
            try:
                await clear_tts()
            except Exception as _exc:
                logger.debug("tts clear_queue on barge-in failed: %s", _exc)
        if websocket:
            try:
                await websocket.send_json({
                    "type": "barge_in",
                    "message": "User started speaking, stopping TTS",
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to send barge_in to websocket: {e}")

    def clear_barge_in_event(self, session: CallSession) -> None:
        """Clear the barge-in event so pending TTS is not immediately interrupted."""
        event = self._barge_in_events.get(session.call_id)
        if event:
            event.clear()

    # ── Sentence-pipelined LLM → TTS ──────────────────────────────

    async def _stream_llm_and_tts(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> tuple[str, float, float]:
        """Stream LLM tokens and pipeline TTS per sentence.

        Implementation extracted to voice_pipeline.turn_streamer (item 2,
        slice 5). Kept as a method so callers and tests that mock it are
        unchanged. Returns (full_response_text, llm_latency_ms, tts_latency_ms).
        """
        return await self._turn_streamer.stream(session, websocket)

    async def _run_turn(
        self,
        session: CallSession,
        full_transcript: str,
        websocket: Optional[WebSocket] = None,
        turn_id: int = 0,
    ) -> tuple[str, float, float]:
        """
        Execute the LLM+TTS cycle for one user turn (atomic history mgmt).

        Implementation extracted to voice_pipeline.turn_runner (item 2,
        slice 4). Kept as a method so tests that call _run_turn directly are
        unchanged.
        """
        return await self._turn_runner.run(session, full_transcript, websocket, turn_id)

    # ── LLM helper ────────────────────────────────────────────────

    async def get_llm_response(self, session: CallSession, user_input: str) -> str:
        """Get LLM response with guardrails applied.

        Implementation extracted to voice_pipeline.llm_response (item 2,
        slice 2). Kept as a method so call sites — and tests that mock
        ``service.get_llm_response`` — are unchanged.
        """
        return await _generate_llm_response_impl(
            self.llm_provider, self.latency_tracker, session, user_input
        )

    # ── TTS helper ────────────────────────────────────────────────

    async def synthesize_and_send_audio(
        self,
        session: CallSession,
        text: str,
        websocket: Optional[WebSocket] = None,
        *,
        track_latency: bool = True,
    ) -> bool:
        """
        Synthesize TTS audio and stream it to the media gateway.
        Returns True if TTS was interrupted by barge-in, False on normal completion.

        Implementation extracted to voice_pipeline.tts_playback (item 2, slice 3).
        Kept as a method so external callers (pipeline.synthesize_and_send_audio)
        and tests that mock it are unchanged; the barge-in event is resolved here
        and passed in.
        """
        return await self._tts_playback.synthesize_and_send(
            session,
            text,
            websocket,
            barge_in_event=self._barge_in_event_for(session),
            track_latency=track_latency,
        )
