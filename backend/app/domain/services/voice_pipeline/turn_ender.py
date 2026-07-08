"""Turn finalization: pre-turn guards (turn-0 floor, repetitive/backchannel),
then the full LLM+TTS turn via _run_turn, telemetry, and transcript flush.

Extracted from VoicePipelineService.handle_turn_end (item 2, slice 8). Same
collaborator pattern: holds the pipeline, reads deps at call time. The service
keeps handle_turn_end() as a thin delegator (transcript_handler schedules it)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import WebSocket

from app.core.container import get_container
from app.core.postgres_adapter import Client as PostgresAdapterClient
from app.core.telemetry import pipeline_span, voice_span
from app.domain.models.conversation import MessageRole
from app.domain.models.session import CallSession, CallState
from app.services.scripts.interruption_filter import is_backchannel as _is_backchannel
from app.services.scripts.echo_guard import strip_self_echo
from app.domain.services.voice_pipeline.turn_helpers import (
    _first_speaker_label,
    _persona_label,
    _prompt_kind_label,
    _resolve_turn_0_floors,
    _should_reject_turn_0,
)

logger = logging.getLogger(__name__)


class TurnEnder:
    """Runs the end-of-turn LLM+TTS cycle with all pre-turn guards."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def handle(
        self,
        session: CallSession,
        websocket: Optional[WebSocket] = None,
        source: str = "final",
        user_text: Optional[str] = None,
    ) -> None:
        call_id = session.call_id
        # Prefer the transcript captured at SCHEDULE time. A barge-in can reset
        # session.current_user_input to "" before this (detached) task reads it,
        # which would strand the turn as "Empty transcript, skipping" and
        # silently drop the caller's utterance. Carrying the text makes the turn
        # immune to that reset. Falls back to the session field for callers that
        # don't pass it.
        full_transcript = (
            user_text if user_text is not None else session.current_user_input
        ).strip()
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

        # Caller-first INSTANT opener: the first bare "Hello?" is answered by
        # the ringing-phase pre-synth greeting (~0.3s) instead of a full
        # LLM+TTS round trip (3-14s+ — two of eight live calls lost the human
        # to that silence, 2026-07-08). Only the FIRST turn, only a bare
        # greeting; a real question still gets the LLM. Fail-soft.
        if not _has_prior_user_turn_for_floor and _first_speaker_label(session) == "user":
            from app.domain.services.voice_pipeline.instant_opener import (
                is_bare_greeting, try_instant_opener,
            )
            if is_bare_greeting(full_transcript) and await try_instant_opener(
                session, full_transcript
            ):
                return

        # Guard against the confirmed Deepgram Flux hallucination bug (GitHub #1524)
        # where the STT model outputs repetitive nonsense text ("blah blah blah…").
        # Heuristic: if a single word accounts for >50% of a 6+ word transcript,
        # treat it as a hallucination and skip — avoids sending garbage to the LLM.
        if self._p._is_repetitive_transcript(full_transcript):
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

        # Interruption lifecycle (gap #2). If the caller cut off ACTIVE agent
        # speech (flagged by tts_playback when it silenced the agent), classify
        # what they did and record it. Pure observability — the suppress / echo
        # decisions below are unchanged. A "false" interruption (we stopped
        # speaking for a backchannel / noise) is the signal operators alert on
        # that the barge-in guard is too eager.
        if getattr(session, "_agent_was_interrupted", False):
            session._agent_was_interrupted = False  # consume per-turn
            try:
                from app.services.scripts.interruption_classifier import (
                    classify_interruption,
                    is_false_interruption,
                    InterruptionType,
                )
                from app.infrastructure.metrics.voice_metrics import (
                    record_interruption,
                )

                _itype = classify_interruption(full_transcript)
                record_interruption(
                    _itype.value, false_interrupt=is_false_interruption(_itype),
                )
                if _itype == InterruptionType.ESCALATION:
                    # Surface so the top-interrupted-calls review (Hamming) and
                    # any future human-handoff can find these fast.
                    logger.info(
                        "interruption_escalation transcript=%r call=%s",
                        full_transcript[:80], call_id[:12],
                    )
            except Exception as exc:  # metrics must never break a turn
                logger.debug("interruption_classify_failed err=%s", exc)

        if _is_backchannel(full_transcript) and _has_prior_user_turn:
            logger.info(
                "backchannel_suppressed transcript=%r call=%s",
                full_transcript, call_id[:12],
            )
            # A backchannel IS caller presence. It never enters history, so
            # the silence monitor's turn-count check can't see it — stamp it
            # so "Okay" doesn't get answered with "Sorry, did I lose you?"
            # eight seconds later (Lukaz call, 2026-07-08).
            try:
                from datetime import datetime as _dt, timezone as _tz
                session._last_backchannel_at = _dt.now(_tz.utc)
            except Exception:
                pass
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
        barge_in_event = self._p._barge_in_events.get(call_id)
        if barge_in_event:
            barge_in_event.clear()

        current_task = asyncio.current_task()
        pending_task = self._p._pending_llm_tasks.get(call_id)
        if pending_task and pending_task.done():
            self._p._pending_llm_tasks.pop(call_id, None)
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

        # Self-echo guard: if the agent's own recent words were transcribed back
        # into this caller turn (carrier echo + open mic during TTS), strip them.
        # If nothing real remains, skip the turn — answering our own echo derails
        # the call (observed in production). Short backchannels never match the
        # 5+ word run, so they pass through untouched.
        _agent_last = next(
            (m.content for m in reversed(session.conversation_history)
             if m.role == MessageRole.ASSISTANT),
            "",
        )
        _deechoed = strip_self_echo(full_transcript, _agent_last)
        if _deechoed != full_transcript:
            if not _deechoed.strip():
                logger.info(
                    "turn_skipped_self_echo",
                    extra={
                        "call_id": call_id,
                        "turn_id": session.turn_id,
                        "transcript": full_transcript[:120],
                    },
                )
                return
            logger.info(
                "self_echo_stripped",
                extra={
                    "call_id": call_id,
                    "before": full_transcript[:120],
                    "after": _deechoed[:120],
                },
            )
            full_transcript = _deechoed

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
        barge_in_event = self._p._barge_in_events.get(call_id)
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
            # P1: bump the turn epoch so a barge-in that targeted a PREVIOUS turn
            # (stale signal from an earlier interruption) can't silence this one.
            _epoch = self._p._turn_epochs.get(call_id, 0) + 1
            self._p._turn_epochs[call_id] = _epoch
            session._current_turn_epoch = _epoch
            self._p.latency_tracker.mark_speech_end(call_id)
            self._p.latency_tracker.mark_llm_start(call_id)

            # NOTE: user message is appended inside _run_turn, which owns the
            # history snapshot + rollback on error/cancellation.  Do NOT append
            # here — it would produce a duplicate entry visible to the LLM on
            # every turn, wasting tokens and corrupting conversation context.

            try:
                # ── LLM + TTS (sentence-pipelined) ────────────────
                with pipeline_span("llm_tts", call_id=call_id, provider="groq",
                                   tenant_id=tenant_id) as llm_tts_span:
                    t0 = time.monotonic()
                    response_text, llm_latency, tts_latency = await self._p._run_turn(
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
                tracked = self._p.latency_tracker.get_metrics(call_id)
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
                    self._p.latency_tracker.log_metrics(call_id)
                    # First-turn telemetry — fires exactly once per call, on
                    # the first turn that actually produced audio. Cold-start
                    # costs land here and are otherwise invisible in the
                    # per-turn aggregate.
                    _mode = _first_speaker_label(session)
                    _kind = _prompt_kind_label(session)
                    _persona = _persona_label(session)
                    self._p.latency_tracker.log_first_turn_if_applicable(
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
                        # Feed the rolling-P95 alerter: emits a WARNING log +
                        # gauge when cross-call P95 latency degrades. Fail-soft.
                        from app.domain.services.voice_pipeline.latency_alerter import (
                            record_turn_latency_ms,
                        )
                        record_turn_latency_ms(tracked.total_latency_ms)

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
                        await self._p.transcript_service.flush_to_database(
                            call_id=call_id,
                            db_client=postgres_client,
                            tenant_id=tenant_id,
                            talklee_call_id=session.talklee_call_id,
                        )
                except Exception as e:
                    logger.warning(f"Failed to flush transcript for {call_id}: {e}")

                # Agent END_CALL: the model closed the conversation this turn
                # (goodbye / wrong number / voicemail). Its goodbye audio has
                # already played via the streamed sentences, so hang up now
                # with no extra farewell. Real capability replacing the
                # role-played "[hangs up]" the audit found.
                if getattr(session, "_end_call_requested", False):
                    logger.info(
                        "agent_end_call call_id=%s — model requested hangup",
                        call_id[:12],
                    )
                    try:
                        await self._p._shutdown_session_for_end_action(
                            session, websocket, "agent_end_call", "",
                        )
                    except Exception as _ec_exc:
                        logger.warning(
                            "agent_end_call_failed call_id=%s err=%s",
                            call_id[:12], _ec_exc,
                        )

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
                    await self._p.synthesize_and_send_audio(
                        session,
                        "I'm sorry, I'm having trouble right now. Please try again in a moment.",
                        websocket,
                    )
                except Exception:
                    pass
            finally:
                pending_task = self._p._pending_llm_tasks.get(call_id)
                # Does THIS task still own the call's turn slot? If a newer turn
                # has already claimed it (this task was cancelled by a barge-in
                # and superseded), we must NOT touch the shared turn state below —
                # clobbering the new turn's llm_active / snapshot / counter is how
                # a just-started turn gets dropped or double-run.
                _owns_slot = (
                    pending_task is None
                    or pending_task is current_task
                    or pending_task.done()
                )
                if pending_task is current_task or (pending_task and pending_task.done()):
                    self._p._pending_llm_tasks.pop(call_id, None)
                if _owns_slot:
                    session.llm_active = False
                    # Clear speculative snapshot — turn completed normally so
                    # the messages it appended are valid and must not be rolled back.
                    session._speculative_history_len = None
                    session.increment_turn()

