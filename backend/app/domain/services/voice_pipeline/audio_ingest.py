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
import os
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
            # Natural, GENTLE silence handling (product flow, 2026-07-07):
            #   • agent waits (caller-first sends no greeting);
            #   • after ~10s of no caller speech, one soft "Hello?" nudge — never
            #     the old aggressive "Are you still there?";
            #   • once the caller speaks, the LLM introduces itself and the
            #     conversation proceeds naturally (prompt-driven);
            #   • after 60s of continuous caller silence, close the call politely.
            _OPENING_HELLO_S = float(os.getenv("VOICE_OPENING_HELLO_S", "10"))
            _MID_NUDGE_S = float(os.getenv("VOICE_MID_NUDGE_S", "10"))
            _SILENCE_HANGUP_S = float(os.getenv("VOICE_SILENCE_HANGUP_S", "60"))
            _TTS_GRACE_S = 3.0
            _NUDGE_MIN_GAP_S = 12.0
            # Opening nudges (caller hasn't spoken yet) — just a natural "hello".
            _OPENING_PHRASES = ["Hello?", "Hi, are you there?", "Hello, can you hear me?"]
            # Mid-conversation check-ins — gentle, no "are you still there".
            _GENTLE_PHRASES = [
                "Hello?",
                "I'm still here whenever you're ready.",
                "Take your time — I'm here.",
                "Sorry, did I lose you?",
            ]

            def _count_user_turns() -> int:
                n = 0
                try:
                    for _m in getattr(session, "conversation_history", []) or []:
                        _role = getattr(_m, "role", None)
                        if getattr(_role, "value", _role) == "user":
                            n += 1
                except Exception:
                    pass
                return n

            async def _silence_monitor() -> None:
                import random
                try:
                    from app.domain.services.voice_pipeline.turn_helpers import (
                        _first_speaker_label,
                    )
                    _is_caller_first = _first_speaker_label(session) == "inbound"
                except Exception:
                    _is_caller_first = False

                _now = datetime.utcnow
                _last_caller_at = _now()   # last caller speech → drives the 60s hangup
                _silence_since = _now()    # last caller OR AI activity → drives nudges
                _last_nudge_at: Optional[datetime] = None
                _prev_user_turns = _count_user_turns()
                _was_active = False
                _tts_ended_at: Optional[datetime] = None

                while session.stt_active:
                    await asyncio.sleep(1.0)
                    if not session.stt_active:
                        break

                    # Caller spoke since last tick → resets BOTH clocks (this is
                    # the real signal that they're present and engaged).
                    _uturns = _count_user_turns()
                    if _uturns > _prev_user_turns:
                        _prev_user_turns = _uturns
                        _last_caller_at = _now()
                        _silence_since = _now()
                        _last_nudge_at = None
                        _was_active = False
                        _tts_ended_at = None
                        continue

                    # AI speaking / thinking (incl. our own nudge) → resets the
                    # NUDGE clock only, never the caller-silence (hangup) clock.
                    _active = session.tts_active or session.llm_active
                    if _active:
                        _silence_since = _now()
                        _tts_ended_at = None
                        _was_active = True
                        continue
                    if _was_active:
                        _tts_ended_at = _now()
                        _silence_since = _tts_ended_at
                        _was_active = False
                        continue

                    # Caller mid-utterance (StartOfTurn before the transcript).
                    _barge = self._p._barge_in_events.get(call_id)
                    if _barge and _barge.is_set():
                        _last_caller_at = _now()
                        _silence_since = _now()
                        continue

                    # Grace right after the AI finished speaking.
                    if _tts_ended_at is not None and (
                        _now() - _tts_ended_at
                    ).total_seconds() < _TTS_GRACE_S:
                        continue

                    # 60s of continuous caller silence → close the call politely.
                    if (_now() - _last_caller_at).total_seconds() >= _SILENCE_HANGUP_S:
                        logger.info(
                            "[SilenceMonitor] %s — %.0fs caller silence, closing call",
                            call_id[:12], _SILENCE_HANGUP_S,
                        )
                        try:
                            await self._p._shutdown_session_for_end_action(
                                session, websocket, "silence_timeout",
                                "I'll let you go for now — feel free to reach out anytime. Take care.",
                            )
                        except Exception as _close_exc:
                            logger.debug("[SilenceMonitor] close-on-silence failed: %s", _close_exc)
                        break

                    # Gentle nudge. Opening (caller-first, caller hasn't spoken
                    # yet) → a soft "Hello?"; otherwise a light check-in.
                    _opening = _is_caller_first and _prev_user_turns == 0
                    _threshold = _OPENING_HELLO_S if _opening else _MID_NUDGE_S
                    _silence = (_now() - _silence_since).total_seconds()
                    if _silence < _threshold:
                        continue
                    if _last_nudge_at is not None and (
                        _now() - _last_nudge_at
                    ).total_seconds() < _NUDGE_MIN_GAP_S:
                        continue

                    _phrase = random.choice(_OPENING_PHRASES if _opening else _GENTLE_PHRASES)
                    logger.info(
                        "[SilenceMonitor] %s — %.0fs silence (%s), nudging: %r",
                        call_id[:12], _silence, "opening" if _opening else "mid", _phrase,
                    )
                    try:
                        await self._p.synthesize_and_send_audio(session, _phrase, websocket)
                        _record_silence_check(self._p, session, _phrase)
                    except Exception as _sm_exc:
                        logger.debug("[SilenceMonitor] TTS failed: %s", _sm_exc)
                    _last_nudge_at = _now()
                    _silence_since = _now()  # give them room to answer before re-nudging

            # Run for real phone calls AND for any session that explicitly opts
            # in — the campaign Test-agent WebSocket sets `_enable_silence_monitor`
            # so the test call behaves like a real one (10s hello, 60s auto-close).
            # A plain Ask-AI widget never opts in, so it is never nagged. Missing
            # gateway_type defaults to telephony so a real phone session always
            # keeps its silence handling.
            _gw_type = getattr(getattr(session, "config", None), "gateway_type", "telephony")
            _opt_in = bool(getattr(session, "_enable_silence_monitor", False))
            _silence_task: Optional[asyncio.Task] = (
                asyncio.create_task(_silence_monitor())
                if (_gw_type == "telephony" or _opt_in)
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

