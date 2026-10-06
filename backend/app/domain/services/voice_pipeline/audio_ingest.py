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
from typing import AsyncIterator, Optional

from fastapi import WebSocket

from app.core.telemetry import pipeline_span, record_latency
from app.domain.models.conversation import AudioChunk, Message, MessageRole
from app.domain.models.session import CallSession

logger = logging.getLogger(__name__)

# ── caller voice-onset anchor ────────────────────────────────────────────────
# The instant the caller STARTED talking, as distinct from the instant we
# noticed. Everything the pipeline knows about interruption timing is derived
# from STT (StartOfTurn), which arrives after the provider has heard enough of a
# word to be confident — so measuring a barge-in from there measures our
# reaction to a decision, not the caller's experience of being talked over.
# Acoustic onset is the only clock the caller shares with us.
#
# Same 500 threshold as everything else in the audio path (see
# resilient_stt._SPEECH_RMS_THRESHOLD) so one number moves them all.
_VOICE_ONSET_RMS = float(os.getenv("VOICE_ONSET_RMS", "500"))
# A pause longer than this ends the current run of speech, so the next voiced
# frame counts as a NEW utterance. 0.4s sits above an inter-word gap and below
# a turn boundary; too short and one sentence reports several onsets, too long
# and a barge-in inherits the onset of the caller's previous turn.
_VOICE_ONSET_GAP_S = float(os.getenv("VOICE_ONSET_GAP_S", "0.4"))
# Beyond this an onset is treated as unusable rather than reported as a
# ludicrous latency. Nothing legitimate keeps one utterance open this long.
_VOICE_ONSET_MAX_AGE_S = 60.0


def note_voice_activity(session, sum_sq: float, sample_count: int) -> None:
    """Record that this frame was voiced, and when the current run began.

    Called per frame on the hot audio path, so it does no work beyond one
    square root and two attribute writes, and it never raises: a failure to
    take a measurement must not cost a call.
    """
    if sample_count <= 0:
        return
    try:
        if (sum_sq / sample_count) ** 0.5 < _VOICE_ONSET_RMS:
            return
        now = time.monotonic()
        last = getattr(session, "_caller_voice_last_at", None)
        if last is None or (now - last) > _VOICE_ONSET_GAP_S:
            session._caller_voice_onset_at = now
        session._caller_voice_last_at = now
    except Exception:
        pass


# ── barge-in margin probe ────────────────────────────────────────────────────
# Buckets for a cheap per-call RMS distribution. Log-ish spacing because the
# interesting question spans two orders of magnitude: the agent's echo measured
# 287-755 on call d068f4b8 while the caller's own voice measured 2360-4890.
_RMS_BUCKETS = (100.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0)


class BargeInMarginProbe:
    """How far apart are the agent's echo and the caller's voice, acoustically?

    MEASUREMENT ONLY — nothing reads this to make a decision, by design.

    2026-08-18: barge-in is detected from Flux's ``StartOfTurn``, and that costs
    a measured p50 of 664ms and a p90 of 3,318ms between the caller opening
    their mouth and us beginning to stop (``detect_ms``, n=252). The obvious
    accelerator is to trigger on acoustic energy instead of waiting for Flux —
    but on PSTN, with no echo cancellation, the line also carries our own TTS,
    and firing on that would make the agent interrupt itself on every turn.

    That trade is only decidable with numbers. One call showed a 5-10x gap
    between echo and caller speech, which would be plenty. One call is not
    evidence: a louder trunk, a speakerphone, or a different handset could
    close it. So this measures the separation on EVERY call and reports it, and
    the decision waits for the distribution rather than the anecdote.

    Costs one comparison and one bucket increment per frame; the RMS it uses is
    already computed by the level accumulator.
    """

    __slots__ = ("echo", "caller")

    def __init__(self) -> None:
        # index 0..len(_RMS_BUCKETS) — one more bucket than edges (the tail).
        self.echo = [0] * (len(_RMS_BUCKETS) + 1)
        self.caller = [0] * (len(_RMS_BUCKETS) + 1)

    def observe(self, rms: float, *, agent_speaking: bool) -> None:
        i = 0
        for edge in _RMS_BUCKETS:
            if rms < edge:
                break
            i += 1
        (self.echo if agent_speaking else self.caller)[i] += 1

    @staticmethod
    def _pct(buckets: list, q: float) -> Optional[float]:
        """Upper edge of the bucket containing the q-th percentile. Coarse on
        purpose — we need to know whether a gap exists, not its third digit."""
        total = sum(buckets)
        if not total:
            return None
        target, seen = total * q, 0
        for i, n in enumerate(buckets):
            seen += n
            if seen >= target:
                return _RMS_BUCKETS[i] if i < len(_RMS_BUCKETS) else float(_RMS_BUCKETS[-1] * 2)
        return None

    def describe(self) -> str:
        e95 = self._pct(self.echo, 0.95)
        c50 = self._pct(self.caller, 0.50)
        c95 = self._pct(self.caller, 0.95)
        margin = (c50 / e95) if (e95 and c50) else None
        return (
            f"echo_frames={sum(self.echo)} echo_rms_p95={'?' if e95 is None else f'{e95:.0f}'} "
            f"caller_frames={sum(self.caller)} "
            f"caller_rms_p50={'?' if c50 is None else f'{c50:.0f}'} "
            f"caller_rms_p95={'?' if c95 is None else f'{c95:.0f}'} "
            f"margin={'?' if margin is None else f'{margin:.1f}x'}"
        )


def voice_onset_age_s(session, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds since the caller began their current utterance, or None.

    None means "no usable measurement" and must be reported as such — never
    substituted with 0.0, which would read as an instantaneous response.
    """
    onset = getattr(session, "_caller_voice_onset_at", None)
    if onset is None:
        return None
    age = (now if now is not None else time.monotonic()) - onset
    if age < 0 or age > _VOICE_ONSET_MAX_AGE_S:
        return None
    return age


def silence_action(*, caller_silence_s: float, in_grace: bool, hangup_s: float) -> str:
    """Enforce the silence deadline without generating conversational speech."""
    if in_grace:
        return "wait"
    return "hangup" if caller_silence_s >= hangup_s else "wait"


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


class AudioIngest:
    """Consumes caller audio -> STT -> transcript dispatch (+ silence monitor)."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def _handle_stt_recovery(self, session, transcript, websocket=None) -> bool:
        """Discard incomplete speech; ask once after the lost turn has ended."""
        recovery = (getattr(transcript, "metadata", None) or {}).get("stt_recovery")
        if recovery not in {"reset", "repeat_required"}:
            return False
        session.current_user_input = ""
        session._last_transcript_confidence = None
        session._last_transcript_alternatives = ()
        if recovery == "reset":
            return True
        if getattr(session, "_stt_recovery_repeat_requested", False):
            return True
        session._stt_recovery_repeat_requested = True
        from app.domain.services.voice_pipeline.playback_gate import mark_caller_stopped
        mark_caller_stopped(session)
        session._caller_turn_open_since = None
        session._caller_turn_closed_at = time.monotonic()
        event = self._p._barge_in_events.get(session.call_id)
        if event is not None:
            event.clear()
        phrase = "The connection cut out. Please say that again."
        interrupted = await self._p.synthesize_and_send_audio(
            session, phrase, websocket, track_latency=False,
        )
        if not interrupted:
            session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content=phrase))
            try:
                self._p.transcript_service.accumulate_turn(
                    call_id=session.call_id, role="assistant", content=phrase,
                    talklee_call_id=getattr(session, "talklee_call_id", None),
                    turn_index=getattr(session, "turn_id", 0), event_type="assistant_response",
                    is_final=True, metadata={"reason": "stt_recovery", "delivery_status": "submitted"},
                )
            except Exception:
                logger.exception("stt_recovery_transcript_failed call_id=%s", session.call_id)
        logger.warning("stt_recovery_repeat_requested call_id=%s submitted=%s", session.call_id, not interrupted)
        return True

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
            _margin = BargeInMarginProbe()
            while session.stt_active:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.02)
                    if chunk:
                        _chunks_yielded += 1
                        raw_bytes = chunk if isinstance(chunk, bytes) else getattr(chunk, "data", b"")
                        if not _first_chunk_logged:
                            _first_chunk_logged = True
                            # The silence monitor measures the CALLER's silence,
                            # and nothing the caller says can be heard before
                            # this moment. See _silence_monitor.
                            try:
                                session._caller_audio_started_at = time.monotonic()
                            except Exception:
                                pass
                            logger.info(
                                "audio_stream_first_chunk call_id=%s "
                                "chunk_len=%d — audio now flowing to STT",
                                call_id, len(raw_bytes),
                            )
                        # Accumulate audio-level stats on 16-bit mono PCM frames
                        if raw_bytes and len(raw_bytes) >= 2 and len(raw_bytes) % 2 == 0:
                            try:
                                samples = _struct.unpack(f"<{len(raw_bytes)//2}h", raw_bytes)
                                _chunk_sum_sq = 0.0
                                for s in samples:
                                    if abs(s) > _level_max:
                                        _level_max = abs(s)
                                    _chunk_sum_sq += s * s
                                _level_sum_sq += _chunk_sum_sq
                                _level_samples += len(samples)
                                # Per-FRAME, not per-second: the once-a-second
                                # audio_level bucket below is far too coarse to
                                # time a barge-in against. Reuses the sum of
                                # squares already computed above, so the onset
                                # anchor costs one square root per frame.
                                note_voice_activity(
                                    session, _chunk_sum_sq, len(samples)
                                )
                                # Same square root, two uses: the onset anchor
                                # above and the echo-vs-caller separation the
                                # early-barge-in decision is waiting on. See
                                # BargeInMarginProbe — measurement only.
                                if samples:
                                    _margin.observe(
                                        (_chunk_sum_sq / len(samples)) ** 0.5,
                                        agent_speaking=bool(
                                            getattr(session, "tts_active", False)
                                        ),
                                    )
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
                            # Stash on the session so the silence monitor can
                            # read ACOUSTIC caller activity. Every other signal
                            # it has is derived from transcripts, so when STT
                            # goes deaf this is the only evidence left that
                            # somebody is talking.
                            # `caller_audio_active` and the 2026-08-13 calls
                            # where the ladder shouted over a live caller.
                            #
                            # Stamped with time.monotonic() explicitly, NOT the
                            # loop clock used for bucketing above. They happen
                            # to be the same clock on the default event loop,
                            # and a freshness check that silently depends on
                            # that is one custom loop away from comparing two
                            # unrelated timebases and reading as "always
                            # stale" — i.e. this guard quietly not existing.
                            try:
                                session._last_audio_rms = rms
                                session._last_audio_peak = _level_max
                                session._last_audio_rms_at = time.monotonic()
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
            # One line per call. `margin` is the ratio between the caller's
            # median speech level and the 95th percentile of the agent's echo:
            # how much acoustic headroom an energy-triggered barge-in would
            # have. Written on every call, including quiet ones, so the
            # distribution can be read rather than the anecdote.
            logger.info(
                "barge_in_margin call_id=%s %s",
                call_id, _margin.describe(),
            )

        # STT span wraps the full transcription stream
        with pipeline_span("stt", call_id=call_id, provider="deepgram",
                           tenant_id=getattr(session, "tenant_id", None)) as stt_span:
            t_stt_start = time.monotonic()

            # Direct barge-in callback: sets the event immediately from the STT
            # background task, even while the pipeline loop is blocked in
            # handle_turn_end.  This is the only reliable way to stop TTS mid-stream.
            def _on_barge_in_direct(transcript_text: Optional[str] = None) -> None:
                # F-10: the instant-opener's own greeting echoes back as a
                # StartOfTurn a beat after playback starts. Distinguish that
                # echo from a real interrupt by CONTENT (bare-greeting text)
                # + a bounded in-flight/grace window, instead of the old
                # (broken) event-parking approach — see instant_opener.py.
                #
                # MUST run before the F-09 seq bump below: if an ignored echo
                # still advanced _utterance_seq, a matching text EndOfTurn
                # ("hello") reaching transcript_handler while the opener task
                # is still running would see current_seq != the opener task's
                # _utterance_seq (F-08's distinctness check) and queue a
                # spurious extra LLM turn — the agent answering its own
                # opener echo. Bailing here before the bump keeps a real
                # StartOfTurn's bump exactly as before (is_opener_echo
                # returns False immediately outside the opener window).
                from app.domain.services.voice_pipeline.instant_opener import (
                    is_opener_echo,
                )
                if is_opener_echo(session, transcript_text):
                    logger.info(
                        "instant_opener_echo_ignored call=%s text=%r",
                        call_id[:12], (transcript_text or "")[:24],
                    )
                    return
                # The caller now holds the floor. Recorded so a FINAL answer that
                # is still generating does not begin speaking on top of them —
                # see voice_pipeline.playback_gate. Set only AFTER the echo gate
                # above, so our own greeting echo never counts as the caller
                # taking the floor.
                from app.domain.services.voice_pipeline.playback_gate import (
                    mark_caller_speaking,
                )
                mark_caller_speaking(session)
                # Explicit "the caller's STT turn is still open" state — set on
                # EVERY real StartOfTurn, not just the tts_active-gated one
                # below. That gated flag (`_barge_in_events[call_id]`) never
                # arms when the agent has already finished speaking, which is
                # exactly the state right after an opening "Hello?" — the
                # silence monitor then had only a once-a-second RMS bucket
                # left, and a single quiet inter-word gap read as silence and
                # released the next re-greet rung on top of a still-open
                # utterance (7dbf415f, 4a9dd845 — 2026-09-23). Cleared on
                # EndOfTurn in the STT consumer loop below; bounded by a
                # safety max age there so a lost EndOfTurn can't silence
                # nudges for the rest of the call.
                session._caller_turn_open_since = time.monotonic()
                # F-09: bump the per-call utterance counter on every StartOfTurn
                # so transcript_handler can tag a suppressed backchannel with
                # the utterance it belongs to (see _utterance_seq docstring).
                self._p._utterance_seq[call_id] = self._p._utterance_seq.get(call_id, 0) + 1
                event = self._p._barge_in_events.get(call_id)
                if event:
                    if session.tts_active:
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
                    else:
                        # F-08: the caller started a SECOND utterance while turn
                        # 1 is still "thinking" (LLM in flight, nothing audible
                        # yet — tts_active is False). There is no playback to
                        # stop, so arming the event here would only pre-empt
                        # turn 1's TTS the instant it tries to speak
                        # (synthesize_and_send sees a pre-armed event and
                        # returns immediately, silencing a reply that was never
                        # actually interrupted). Record presence only.
                        session._last_caller_activity_monotonic = time.monotonic()
                current_metrics = self._p.latency_tracker.get_metrics(call_id)
                if not current_metrics or current_metrics.turn_id != session.turn_id:
                    self._p.latency_tracker.start_turn(call_id, session.turn_id)
                self._p.latency_tracker.mark_listening_start(call_id)

            # Silence owns only the configured disconnection deadline. Model
            # turns and caller audio own speech; there is no nudge generator.
            _SILENCE_HANGUP_S = float(os.getenv("VOICE_SILENCE_HANGUP_S", "60"))
            _TTS_GRACE_S = 3.0
            _CALLER_TURN_OPEN_MAX_AGE_S = float(
                os.getenv("VOICE_CALLER_TURN_OPEN_MAX_AGE_S", "12.0")
            )
            _CALLER_TURN_NO_TEXT_S = float(
                os.getenv("VOICE_CALLER_TURN_NO_TEXT_S", "3.0")
            )

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
                now = time.monotonic
                last_caller_at = now()
                previous_user_turns = _count_user_turns()
                was_active = False
                tts_ended_at = None
                while session.stt_active:
                    await asyncio.sleep(1.0)
                    if not session.stt_active:
                        break
                    try:
                        user_turns = _count_user_turns()
                        backchannel_at = getattr(session, "_last_backchannel_monotonic", None)
                        if user_turns > previous_user_turns or (
                            backchannel_at is not None and now() - backchannel_at < 2.5
                        ):
                            previous_user_turns = user_turns
                            last_caller_at = now()
                            was_active = False
                            tts_ended_at = None
                            session._caller_spoke_since_greeting = True
                            continue
                        if session.tts_active or session.llm_active:
                            was_active = True
                            tts_ended_at = None
                            continue
                        if was_active:
                            tts_ended_at = now()
                            last_caller_at = tts_ended_at
                            was_active = False
                            continue

                        barge = self._p._barge_in_events.get(call_id)
                        turn_open_since = getattr(session, "_caller_turn_open_since", None)
                        last_text_at = getattr(session, "_caller_last_text_at", None)
                        turn_open = (
                            isinstance(turn_open_since, (int, float))
                            and now() - turn_open_since < _CALLER_TURN_OPEN_MAX_AGE_S
                            and (
                                now() - turn_open_since < _CALLER_TURN_NO_TEXT_S
                                or (isinstance(last_text_at, (int, float))
                                    and now() - last_text_at < _CALLER_TURN_NO_TEXT_S)
                            )
                        )
                        closed_at = getattr(session, "_caller_turn_closed_at", None)
                        words_open = (
                            isinstance(last_text_at, (int, float))
                            and now() - last_text_at < _CALLER_TURN_NO_TEXT_S
                            and (not isinstance(closed_at, (int, float)) or last_text_at > closed_at)
                        )
                        if (barge and barge.is_set()) or turn_open or words_open:
                            last_caller_at = now()
                            continue
                        if silence_action(
                            caller_silence_s=now() - last_caller_at,
                            in_grace=tts_ended_at is not None and now() - tts_ended_at < _TTS_GRACE_S,
                            hangup_s=_SILENCE_HANGUP_S,
                        ) == "hangup":
                            await self._p._shutdown_session_for_end_action(
                                session, websocket, "silence_timeout", "",
                            )
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning("silence_monitor_tick_failed call_id=%s", call_id, exc_info=True)

            _gw_type = getattr(getattr(session, "config", None), "gateway_type", "telephony")
            _opt_in = bool(getattr(session, "_enable_silence_monitor", False))
            _silence_task: Optional[asyncio.Task] = (
                asyncio.create_task(_silence_monitor())
                if (_gw_type == "telephony" or _opt_in)
                else None
            )

            try:
                # Tell the STT failover watchdog how to ask "is the agent
                # talking right now?".
                #
                # 2026-08-18: without this the watchdog counted our own TTS,
                # echoing back on a 2-wire line, as unanswered caller speech,
                # and abandoned Flux on six of fourteen answered calls within
                # seconds of the greeting. It cannot use the provider's `muted`
                # flag because telephony deliberately never mutes (barge-in),
                # and it cannot hold its own copy of the state because a
                # mirrored copy of a signal that changes several times per
                # second is guaranteed to drift. So it reads the one source of
                # truth, live, through this probe.
                #
                # Installed HERE because this is the first point that has both
                # the session and the provider. Absence is reported per call by
                # `resilient_stt_echo_guard probe=ABSENT` rather than assumed
                # harmless — an uninstalled guard is how the previous version
                # of this protection managed to be dead code for four days.
                _install_probe = getattr(
                    self._p.stt_provider, "set_agent_speaking_probe", None
                )
                if _install_probe is not None:
                    _install_probe(
                        lambda: bool(getattr(session, "tts_active", False))
                    )

                async for transcript in self._p.stt_provider.stream_transcribe(
                    audio_stream(),
                    # The tenant's saved STT language (F09). Deepgram Nova
                    # honours it per stream; Flux is English-only and the
                    # orchestrator already routed non-English to Nova.
                    language=getattr(session, "stt_language", None) or "en",
                    call_id=call_id,
                    on_barge_in=_on_barge_in_direct,
                ):
                    if await self._handle_stt_recovery(session, transcript, websocket):
                        continue
                    # Close the "caller turn open" window the moment the
                    # PROVIDER says the turn ended — provider-agnostic
                    # (detect_turn_end is a plain is_final-and-no-text check),
                    # so this covers the Nova/failover path through the same
                    # loop, not just Flux. Runs before dispatch so a turn that
                    # gets suppressed downstream (backchannel, duplicate, etc.)
                    # still closes the window.
                    try:
                        # Caller WORDS (interim or final) — what separates real
                        # speech from a word-less SpeechStarted. Read by the
                        # silence monitor and by the false-barge-in recovery.
                        if (getattr(transcript, "text", "") or "").strip():
                            session._caller_last_text_at = time.monotonic()
                        if self._p.stt_provider.detect_turn_end(transcript):
                            session._caller_turn_open_since = None
                            session._caller_turn_closed_at = time.monotonic()
                    except Exception:
                        pass
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
                if _silence_task is not None:
                    # One verdict per call, written whether or not the acoustic
                    # guard ever fired. "suppressed=0" is a measurement;
                    # an absent line is not, and the difference is exactly what
                    # made a dead STT stream look like a quiet caller.
                    logger.info(
                        "[SilenceMonitor] %s — nudge_audit nudges=%d suppressed=%d",
                        call_id[:12],
                        int(getattr(session, "_nudges_spoken", 0) or 0),
                        int(getattr(session, "_nudges_suppressed", 0) or 0),
                    )
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
