"""Caller-audio ingestion: pull frames from the media gateway, run STT,
dispatch transcripts, and run the telephony silence monitor.

Extracted from VoicePipelineService.process_audio_stream (item 2, slice 6).
Same collaborator pattern: holds the pipeline and reads its deps
(media_gateway / stt_provider / latency_tracker / synthesize_and_send_audio /
handle_transcript / _barge_in_events) at call time. The service keeps
process_audio_stream() as a thin delegator (a test calls it directly).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import AsyncIterator, Optional

from fastapi import WebSocket

from app.core.telemetry import pipeline_span, record_latency
from app.domain.models.conversation import AudioChunk, Message, MessageRole
from app.domain.models.session import CallSession

logger = logging.getLogger(__name__)


class TerminalSTTError(RuntimeError):
    """Raised when the caller-audio STT stream ends via an unrecoverable
    provider error instead of a normal pipeline shutdown.

    FIX #1b — previously any exception out of ``stream_transcribe`` (e.g.
    Deepgram's primary AND failover-secondary both failing) was logged and
    swallowed here, so ``AudioIngest.process`` — and therefore
    ``VoicePipelineService.start_pipeline`` and its ``pipeline_task`` —
    returned *cleanly*.  That meant the done-callback in
    ``telephony/lifecycle.py`` (``_pipeline_done_cb``) never saw an
    exception and never forced teardown, leaving the caller on dead air
    until the ~300s inactivity watchdog (or the gateway's ~2h hard cap)
    finally noticed. Raising this instead lets the real exception propagate
    out of the pipeline task so the done-callback fires within seconds.

    Deliberately NOT raised for ``asyncio.CancelledError`` (a
    ``BaseException``, already unaffected by the ``except Exception`` below)
    so a normal hangup — which cancels ``pipeline_task`` — is unaffected.
    """


def _record_silence_check(pipeline, session, phrase: str) -> None:
    """Record a spoken silence-check as an assistant turn (issue #8).

    Writes to BOTH the live conversation_history (so the LLM knows it just asked
    "you there?" and doesn't re-ask) AND the persisted transcript (so post-call
    QA/compliance records match what was actually spoken on the line). Mirrors
    turn_runner's assistant-turn append. Never raises — bookkeeping must not
    break a call.
    """
    try:
        session.conversation_history.append(
            Message(role=MessageRole.ASSISTANT, content=phrase)
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[SilenceMonitor] history append failed: %s", exc)
    try:
        ts = getattr(pipeline, "transcript_service", None)
        if ts is not None:
            ts.accumulate_turn(
                call_id=session.call_id,
                role="assistant",
                content=phrase,
                talklee_call_id=getattr(session, "talklee_call_id", None),
                turn_index=getattr(session, "turn_id", 0),
                event_type="assistant_response",
                is_final=True,
                include_in_plaintext=True,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[SilenceMonitor] transcript accumulate failed: %s", exc)


class AudioIngest:
    """Consumes caller audio -> STT -> transcript dispatch (+ silence monitor)."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def process(
        self,
        session: CallSession,
        agent_config=None,
        websocket: Optional[WebSocket] = None,
    ) -> None:
        call_id = session.call_id

        async def audio_stream() -> AsyncIterator[AudioChunk]:
            queue = self._p.media_gateway.get_audio_queue(call_id)
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
                event = self._p._barge_in_events.get(call_id)
                if event:
                    # Stamp the moment of the barge-in signal so tts_playback can
                    # measure how fast we actually silence the caller (target
                    # <60ms). Overwrite (not first-wins) so a never-consumed
                    # stamp from an earlier turn can't skew a later measurement.
                    session._barge_in_set_monotonic = time.monotonic()
                    event.set()
                    # P1 (audit #13): stamp the turn-epoch this barge-in targets,
                    # mirroring handle_barge_in. Without it the epoch kept a STALE
                    # value from a previous turn's handle_barge_in, so the streamer's
                    # _barged() could compare a freshly-set event against an old
                    # epoch and wrongly SUPPRESS a genuine interruption — i.e. the
                    # agent keeps talking over the caller. Single writer for both
                    # the event and the epoch closes the race.
                    self._p._barge_in_epoch[call_id] = getattr(session, "_current_turn_epoch", 0)
                current_metrics = self._p.latency_tracker.get_metrics(call_id)
                if not current_metrics or current_metrics.turn_id != session.turn_id:
                    self._p.latency_tracker.start_turn(call_id, session.turn_id)
                self._p.latency_tracker.mark_listening_start(call_id)

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
                # Give up after this many unanswered check-ins. 3 (not 2) gives a
                # briefly-quiet caller — thinking, muted, or STT missing them — one
                # more chance before we auto-close on silence (re-audit flow #5).
                _MAX_CONSECUTIVE = 3

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
                    _barge_ev = self._p._barge_in_events.get(call_id)
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
                        await self._p.synthesize_and_send_audio(session, phrase, websocket)
                        # Record it (history + transcript) so the LLM knows it
                        # already asked and the transcript matches what was spoken.
                        _record_silence_check(self._p, session, phrase)
                    except Exception as _sm_exc:
                        logger.debug("[SilenceMonitor] TTS failed: %s", _sm_exc)

                    # Reset baseline with grace period after our own TTS phrase
                    _tts_ended_at = datetime.utcnow()
                    _last_event_at = _tts_ended_at

                logger.debug(
                    "[SilenceMonitor] %s exiting (consecutive=%d stt_active=%s)",
                    call_id[:12], consecutive, session.stt_active,
                )

                # Gave up after MAX unanswered check-ins while the call is still
                # live → the caller is gone. Close politely with a brief goodbye
                # instead of leaving a silent line open until the session timeout
                # (reuses the proven end-session teardown: farewell + hangup).
                if session.stt_active and consecutive >= _MAX_CONSECUTIVE:
                    logger.info(
                        "[SilenceMonitor] %s — no response after %d check-ins, closing call",
                        call_id[:12], consecutive,
                    )
                    try:
                        await self._p._shutdown_session_for_end_action(
                            session,
                            websocket,
                            "silence_timeout",
                            "I'll let you go for now — feel free to give us a call back anytime. Take care.",
                        )
                    except Exception as _close_exc:
                        logger.debug("[SilenceMonitor] close-on-silence failed: %s", _close_exc)

            # Silence monitor is a TELEPHONY concern: a phone caller who goes
            # quiet may have physically walked away, so we check in and, after a
            # few unanswered check-ins, close politely. But a BROWSER session —
            # the Ask-AI widget and the campaign Test-agent WebSocket — has a
            # PRESENT user who reads / thinks / takes notes between turns; nagging
            # "Are you still there?" mid-conversation is noise and was reported as
            # aggressive. Gate it to the telephony gateway only (restores this
            # monitor's original telephony-only scope). Missing gateway_type
            # defaults to telephony (real calls always set it) so a malformed
            # phone session never loses its silence handling.
            _gw_type = getattr(getattr(session, "config", None), "gateway_type", "telephony")
            _silence_task: Optional[asyncio.Task] = (
                asyncio.create_task(_silence_monitor())
                if _gw_type == "telephony"
                else None
            )

            try:
                async for transcript in self._p.stt_provider.stream_transcribe(
                    audio_stream(),
                    call_id=call_id,
                    on_barge_in=_on_barge_in_direct,
                ):
                    await self._p.handle_transcript(session, transcript, websocket)
            except Exception as e:
                stt_span.record_exception(e)
                logger.error(f"STT stream error: {e}", extra={"call_id": call_id})
                # FIX #1b — re-raise as a distinguishable terminal-failure
                # type so it propagates through process_audio_stream /
                # start_pipeline instead of being absorbed here. See
                # TerminalSTTError's docstring for the full chain.
                raise TerminalSTTError(str(e)) from e
            finally:
                if _silence_task and not _silence_task.done():
                    _silence_task.cancel()
                    try:
                        await _silence_task
                    except asyncio.CancelledError:
                        pass
                record_latency(stt_span, "stt", (time.monotonic() - t_stt_start) * 1000)
                get_stats = getattr(self._p.stt_provider, "get_stream_stats", None)
                if get_stats:
                    stats = get_stats(call_id)
                    if stats:
                        for k, v in stats.items():
                            try:
                                stt_span.set_attribute(f"stt.{k}", v)
                            except Exception as _e:
                                logger.debug("stt_span_attr k=%s: %s", k, _e)

