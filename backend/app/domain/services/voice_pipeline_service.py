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
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.llm_guardrails import LLMGuardrails, LLMGuardrailsConfig, get_guardrails
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
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest, TerminalSTTError
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline.turn_ender import TurnEnder
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


# Turn-0 floor + telemetry-label helpers moved to voice_pipeline.turn_helpers
# (item 2, slice 8). Re-imported so they stay importable from this module
# (a test imports _alpha_char_count / _should_reject_turn_0 from here).
from app.domain.services.voice_pipeline.turn_helpers import (  # noqa: F401
    _alpha_char_count,
    _should_reject_turn_0,
    _resolve_turn_0_floors,
    _first_speaker_label,
    _prompt_kind_label,
    _persona_label,
    _TURN_0_MIN_CONFIDENCE,
    _TURN_0_MIN_ALPHA_CHARS,
)


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

        self.transcript_service = TranscriptService()
        self.latency_tracker = get_latency_tracker()

        # Extracted collaborators (TtsPlayback/TurnRunner) are exposed as
        # lazy properties below — they read their deps off this service at
        # call time, and lazy creation keeps them working even when a test
        # builds the service via __new__ (bypassing __init__).
        self._barge_in_events: dict[str, asyncio.Event] = {}
        self._pending_llm_tasks: dict[str, asyncio.Task] = {}
        # P1 (barge-in epoch): a monotonic per-call turn counter. Each new turn
        # bumps it; a barge-in records which turn-epoch it targeted. The streamer
        # ignores a barge-in that targeted an OLDER turn (a stale signal left over
        # from a previous interruption) so it can't silence a fresh reply.
        self._turn_epochs: dict[str, int] = {}
        self._barge_in_epoch: dict[str, int] = {}
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

    @property
    def _audio_ingest(self) -> AudioIngest:
        """Caller-audio ingestion + STT + silence monitor collaborator (item 2,
        slice 6). Lazily created so it works under __new__-built services."""
        inst = self.__dict__.get("_audio_ingest_inst")
        if inst is None:
            inst = AudioIngest(self)
            self.__dict__["_audio_ingest_inst"] = inst
        return inst

    @property
    def _transcript_handler(self) -> TranscriptHandler:
        """Per-transcript dispatch collaborator (item 2, slice 7). Lazily
        created so it works under __new__-built services."""
        inst = self.__dict__.get("_transcript_handler_inst")
        if inst is None:
            inst = TranscriptHandler(self)
            self.__dict__["_transcript_handler_inst"] = inst
        return inst

    @property
    def _turn_ender(self) -> TurnEnder:
        """End-of-turn LLM+TTS cycle collaborator (item 2, slice 8). Lazily
        created so it works under __new__-built services."""
        inst = self.__dict__.get("_turn_ender_inst")
        if inst is None:
            inst = TurnEnder(self)
            self.__dict__["_turn_ender_inst"] = inst
        return inst

    async def _cancel_turn_task(self, task: asyncio.Task, call_id: str, label: str) -> None:
        """Cancel an in-flight turn task and wait BRIEFLY for it to unwind —
        WITHOUT ever freezing the single STT consumer loop.

        A cancelled turn parked in a TTS send can take 100s of ms to fully
        unwind; awaiting it unboundedly on the consumer (the old behaviour) let
        rapid back-to-back barge-ins pile up and every queued turn got dropped →
        ~20-30s of dead air (the multi-barge-in silence bug). We wait at most
        BARGE_IN_CANCEL_WAIT_S; if the task is slower it finishes in the
        background (shielded) while the consumer stays responsive."""
        if not task.done():
            task.cancel()
        timeout = float(os.getenv("BARGE_IN_CANCEL_WAIT_S", "0.25"))
        try:
            await asyncio.wait_for(
                asyncio.shield(self._await_task_after_cancel(task, call_id, label)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.info(
                "barge_in_cancel_slow call=%s label=%s — detached, consumer not blocked",
                call_id[:12], label,
            )

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

    async def cancel_active_turn(self, call_id: str) -> None:
        """Cancel any in-flight turn task for a call. Called on hangup/teardown
        so a reply still streaming TTS stops sending audio to a gateway/channel
        that's already gone (otherwise: 'no gateway session' warnings + wasted
        synthesis on a dead call)."""
        task = self._pending_llm_tasks.pop(call_id, None)
        if task and not task.done():
            task.cancel()
            await self._await_task_after_cancel(task, call_id, "teardown")

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
        hangup_requested = False
        if callable(hangup):
            try:
                hangup_requested = bool(await hangup(call_id, reason))
            except Exception as exc:
                logger.debug("End-session telephony hangup failed: %s", exc)

        # When we ask the PBX to hang up (Asterisk telephony), the authoritative
        # call-ended teardown (lifecycle._on_call_ended -> _save_call_recording ->
        # end_session) fires on ChannelDestroyed and owns BOTH the recording save
        # AND the gateway-session cleanup. Tearing the gateway session down here
        # would pop the recording_buffer before that path can read it — the root
        # cause of dropped recordings on agent-ended ("user_goodbye") calls: the
        # PBX save path then sees an empty buffer and silently skips the save.
        # For sessions with no PBX hangup (browser / ask_ai), hangup_requested is
        # False, so we still tear the gateway session down locally as before.
        if not hangup_requested:
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

        # Correlation: WebSockets/telephony callbacks never pass through the
        # HTTP request-id middleware, so voice logs used to show `req=-`
        # with no way to grep one call's logs together. Mirrors the same
        # ContextVar + logging.Filter pattern used for tenant_id (see
        # assistant_ws.py's set_current_tenant_id call) — set once here at
        # pipeline start; asyncio.create_task copies the current context,
        # so turn/STT/LLM/TTS tasks spawned from within this call's
        # execution tree inherit it automatically.
        from app.core.request_id_middleware import set_call_id
        set_call_id(call_id)

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
            except TerminalSTTError as e:
                # FIX #1b — a terminal STT failure (primary + failover
                # secondary both down) must propagate out of this task
                # (after the finally block below runs) instead of being
                # absorbed like a generic pipeline error. Re-raising lets
                # telephony/lifecycle.py's _pipeline_done_cb see
                # task.exception() and force-end + hang up the call within
                # seconds, instead of the caller sitting on dead air until
                # the ~300s inactivity watchdog notices.
                span.record_exception(e)
                logger.error(
                    f"Pipeline error: terminal STT failure — {e}",
                    extra={"call_id": call_id},
                    exc_info=True,
                )
                raise
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
                self._turn_epochs.pop(call_id, None)
                self._barge_in_epoch.pop(call_id, None)
                session.stt_active = False
                self.latency_tracker.cleanup_call(call_id)
                logger.info("pipeline_end", extra={"call_id": call_id})

    async def process_audio_stream(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        """Consume caller audio -> STT -> transcripts.

        Implementation extracted to voice_pipeline.audio_ingest (item 2,
        slice 6). Kept as a method so callers/tests are unchanged.
        """
        return await self._audio_ingest.process(session, agent_config, websocket)

    # ── Transcript handling ────────────────────────────────────────

    async def handle_transcript(
        self,
        session: CallSession,
        transcript,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        """Route one STT transcript.

        Implementation extracted to voice_pipeline.transcript_handler
        (item 2, slice 7). Kept as a method so callers/tests are unchanged.
        """
        return await self._transcript_handler.handle(session, transcript, websocket)

    # ── Turn end — the full LLM + TTS cycle ───────────────────────

    async def handle_turn_end(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
        source: str = "final",
        user_text: Optional[str] = None,
    ) -> None:
        """Run the end-of-turn LLM+TTS cycle.

        Implementation extracted to voice_pipeline.turn_ender (item 2,
        slice 8). Kept as a method so transcript dispatch can schedule it.

        ``user_text`` carries the transcript captured at SCHEDULE time so a
        concurrent barge-in resetting session.current_user_input can't strand
        this turn (the dropped-turn half of the silence bug).
        """
        return await self._turn_ender.handle(
            session, websocket, source, user_text=user_text
        )

    # ── Barge-in ──────────────────────────────────────────────────

    async def handle_barge_in(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        # Protect an in-flight FINAL answer that has not started playing yet.
        # A StartOfTurn here means the caller began a NEW utterance before we
        # finished answering the previous (completed) one — it is NOT an
        # interruption of audio they can hear. Cancelling it would delete the
        # answer and leave the caller in silence. Per Deepgram/LiveKit/Pipecat,
        # a barge-in targets PLAYBACK, never a committed "thinking" turn: the
        # final answer must finish and speak; the caller's new words become the
        # next turn. (Speculative tasks, and any task while TTS is actually
        # playing, fall through below and are cancelled as before.)
        pending = self._pending_llm_tasks.get(call_id)
        if (
            pending is not None
            and not pending.done()
            and getattr(pending, "_turn_type", "final") == "final"
            and not session.tts_active
        ):
            logger.info(
                "barge_in_ignored_final_pre_tts call=%s — protecting in-flight answer",
                call_id[:12],
            )
            return

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
            # P1: stamp which turn-epoch this barge-in targets so the streamer
            # can ignore it if a newer turn has already superseded that one.
            self._barge_in_epoch[call_id] = getattr(session, "_current_turn_epoch", 0)

        # Cancel the in-flight turn task. Reaching here means it is either a
        # SPECULATIVE task (tentative — user may still be talking) or a task
        # whose TTS is actively playing (a genuine interruption of audible
        # speech). Both are correct to cancel. A final task that has NOT begun
        # playing was already protected by the early-return guard above.
        cancelled_task = self._pending_llm_tasks.pop(call_id, None)
        if cancelled_task:
            # Bounded, NON-blocking cancel: never freeze the single STT consumer
            # waiting for the cancelled turn to unwind. Unbounded awaiting here let
            # rapid barge-ins pile up and dropped every backlogged turn → seconds
            # of dead air (multi-barge-in silence bug). Detaches if the task is slow.
            await self._cancel_turn_task(cancelled_task, call_id, "barge_in_llm")
            # asyncio cancellation is cooperative — the task's finally block
            # (which resets llm_active) may not have run yet. Clear it now so a
            # follow-up EndOfTurn is not skipped by turn_ender's llm_active
            # guard, which would silently drop the caller's next utterance.
            session.llm_active = False
            # Roll back any history the cancelled task may have appended before
            # being cancelled (mirrors the TurnResumed rollback path).
            restore_len = getattr(session, "_speculative_history_len", None)
            if restore_len is not None and len(session.conversation_history) > restore_len:
                session.conversation_history = session.conversation_history[:restore_len]
            session._speculative_history_len = None
            logger.debug(
                "barge_in: cancelled in-flight turn task for %s", call_id[:12]
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

        NOTE on the END_CALL sentinel: this used to be the site that stripped
        it (`extract_end_call`), but that ran AFTER turn_streamer had already
        piped the sentence through `guardrails.clean_response` — whose
        audio-tag stripper erases a bracketed sentinel before it ever got
        here, so the flag was silently never set (2026-07-13 root-cause fix).
        Extraction now happens in turn_streamer, on the RAW model text,
        before clean_response runs — see
        `voice_pipeline.end_call.strip_and_flag`, the one authoritative site.
        Every other caller of this method (greetings, canned nudges/apologies,
        end-session farewells) hands it text that was never LLM output
        carrying this token, so there is nothing left for this method to
        strip.
        """
        return await self._tts_playback.synthesize_and_send(
            session,
            text,
            websocket,
            barge_in_event=self._barge_in_event_for(session),
            track_latency=track_latency,
        )
