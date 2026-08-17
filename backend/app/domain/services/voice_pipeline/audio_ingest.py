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
from app.domain.services.voice_pipeline import turn_director

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


def silence_action(
    *,
    caller_silence_s: float,
    activity_silence_s: float,
    since_last_nudge_s: Optional[float],
    in_grace: bool,
    is_caller_first: bool,
    user_turns: int,
    hangup_s: float,
    opening_s: float,
    mid_s: float,
    nudge_gap_s: float,
    opening_gap_s: Optional[float] = None,
    agent_awaiting_first_reply: bool = False,
    caller_audio_active: bool = False,
) -> str:
    """One silence-monitor tick decision → ``'hangup'`` | ``'nudge'`` | ``'wait'``.

    Pure (no I/O, no clocks) so the natural caller-first flow is unit-testable
    without a live audio pipeline. Mirrors the monitor loop's order exactly:

      1. ``in_grace`` (the AI just finished speaking) suppresses everything;
      2. ``caller_silence_s >= hangup_s`` (60s of no caller speech) → close;
      3. otherwise nudge once the ACTIVITY silence passes the threshold
         (``opening_s`` when caller-first and the caller hasn't spoken yet, else
         ``mid_s``) AND at least the applicable gap has passed since the last
         nudge — ``opening_gap_s`` while opening (a human's re-"Hello?" cadence
         is a couple of seconds, not the ~15s that's reasonable for a mid-call
         check-in), else ``nudge_gap_s``. ``opening_gap_s`` defaults to
         ``None``, which falls back to ``nudge_gap_s`` — keeps every existing
         caller (incl. the pre-2026-08-11 test suite) working unchanged if it
         never passes the new parameter.

    ``caller_audio_active`` is the ACOUSTIC signal — the caller's line is
    carrying speech-level energy right now — and it suppresses nudging only.
    Every other input to this function is derived from transcripts, which is
    why 2026-08-13 went the way it did: on two calls Deepgram accepted the
    audio and returned nothing at all, so by every transcript-derived measure
    the caller was silent, and the ladder talked over someone who was speaking
    at RMS 3504. 28 of 35 nudges that day (80%, across 20 of 40 calls) landed
    on a caller who was audibly mid-sentence.

    It deliberately does NOT block ``hangup``. Energy on the line is not proof
    of a conversation — it is also what a TV in the background looks like — so
    the 60s bound stays absolute and no call can be held open by noise alone.
    A live person whose STT has died is rescued by the failover in
    ``resilient_stt``, not by refusing to ever hang up.
    """
    if in_grace:
        return "wait"
    if caller_silence_s >= hangup_s:
        return "hangup"
    if caller_audio_active:
        return "wait"
    # "Opening" = the callee has not spoken yet AND the agent has not yet
    # delivered a real introduction — it has, at most, said a bare hello.
    #
    # 2026-08-12 REGRESSION FIX. This used to be `is_caller_first and
    # user_turns == 0`, which was correct only while agent-first calls opened
    # with a full introduction ending in a question ("Hi! Alexia calling — got
    # a moment?"). Once turn 1 became a bare two-word pickup greeting, an
    # agent-first call fell into a hole: `opening` was False (not caller-first)
    # so the re-greet ladder never applied, AND `should_suppress_mid_nudge`
    # returned True (not caller-first, callee never spoke) so the mid nudge was
    # skipped too. The agent said "Hi there." and then went silent until the
    # 60s hangup — reported from a live test as "it stops after speaking one
    # time, no follow up".
    #
    # A bare hello and the re-greet ladder are two halves of ONE design: a
    # two-word greeting is only safe if something follows it up. So the trigger
    # is now the state that actually matters — nobody has spoken and we have
    # not introduced ourselves — not which side happened to dial.
    opening = user_turns == 0 and (is_caller_first or agent_awaiting_first_reply)
    threshold = opening_s if opening else mid_s
    if activity_silence_s < threshold:
        return "wait"
    gap = nudge_gap_s
    if opening and opening_gap_s is not None:
        gap = opening_gap_s
    if since_last_nudge_s is not None and since_last_nudge_s < gap:
        return "wait"
    return "nudge"


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
                            # somebody is talking — see silence_action's
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

            # ── Silence monitor (telephony only) ───────────────────────────────
            # After 5-7 seconds of continuous caller silence the agent asks if the
            # caller is still there.  Phrases are varied each time to avoid sounding
            # robotic.  Runs in parallel with the STT consumer loop; cancelled when
            # the pipeline exits.  Disabled for Ask AI (browser sessions).
            # Natural, GENTLE silence handling (product flow, 2026-07-07;
            # opening cadence retuned 2026-08-11):
            #   • agent waits (caller-first sends no greeting);
            #   • after ~2-3s of dead air — how long a human actually pauses
            #     before re-checking a silent line, not the old 10s that read
            #     as a dropped call — one soft "Hello?" nudge, repeated up to
            #     ``_OPENING_MAX_NUDGES`` times a couple of seconds apart
            #     (never the old aggressive "Are you still there?");
            #   • once the caller speaks, the LLM introduces itself and the
            #     conversation proceeds naturally (prompt-driven);
            #   • once the opening ladder is exhausted, no more nudges — the
            #     existing 60s continuous-caller-silence hangup below is what
            #     eventually ends the call, so this can never loop forever;
            #   • after 60s of continuous caller silence, close the call politely.
            _OPENING_HELLO_S = float(os.getenv("VOICE_OPENING_HELLO_S", "2.5"))
            # RAISED 10 -> 16 on 2026-08-12. A traced production call nudged
            # "Still there?" twice while the caller was composing a question,
            # and both times they began speaking 1.6-2.6s AFTER the prod:
            #
            #   20:54:46.881  silence (mid), nudging: 'Still there?'
            #   20:54:49.522  EndOfTurn: 'So what can I do about that ...'
            #   20:55:00.009  silence (mid), nudging: 'Still there?'
            #   20:55:01.639  EndOfTurn: 'So what what I do ...'
            #
            # Ten seconds of quiet mid-conversation is not a dead line, it is
            # someone thinking — and prodding them then is the same naggy
            # behaviour the opening ladder was retuned to avoid, just at the
            # other end of the call. The OPENING threshold stays short (2.5s:
            # a silent pickup really might be a dead line); this one is about
            # a person who has already spoken and is deciding what to say.
            _MID_NUDGE_S = float(os.getenv("VOICE_MID_NUDGE_S", "16"))
            _SILENCE_HANGUP_S = float(os.getenv("VOICE_SILENCE_HANGUP_S", "60"))
            _TTS_GRACE_S = 3.0
            # ACOUSTIC nudge guard (2026-08-13). Same 500 RMS that every
            # audio_level log line already prints as ">500=speech-likely", so
            # the number a human reads while debugging is the number the code
            # acts on. The stash refreshes ~1/s, so 2.0s of tolerance accepts
            # the current reading and rejects a stale one — tighter than the
            # 2.5s opening nudge gap, so a guard can never outlive the decision
            # it was meant to inform.
            _AUDIO_ACTIVE_RMS = float(os.getenv("VOICE_AUDIO_ACTIVE_RMS", "500"))
            _AUDIO_ACTIVE_MAX_AGE_S = float(
                os.getenv("VOICE_AUDIO_ACTIVE_MAX_AGE_S", "2.0")
            )
            # 2026-07-08: widened 12.0 -> 15.0 (env-overridable) so mid
            # check-ins don't stack up as nagging on a caller who is just
            # thinking; the opening ("hello?") gap is unaffected — it now has
            # its own, much shorter, env var below.
            _NUDGE_MIN_GAP_S = float(os.getenv("VOICE_NUDGE_MIN_GAP_S", "15.0"))
            # 2026-08-11: a human re-dialling doesn't wait 15s between
            # "Hello?"s — they say it again after a couple of seconds. This is
            # the OPENING-only repeat gap (mid-call check-ins keep using
            # _NUDGE_MIN_GAP_S above); silence_action falls back to
            # _NUDGE_MIN_GAP_S whenever opening_gap_s is left unset, so mid
            # behaviour is untouched by this change.
            _OPENING_NUDGE_GAP_S = float(os.getenv("VOICE_OPENING_NUDGE_GAP_S", "2.5"))
            # Phrase ladders + suppression rule moved to turn_director.py
            # (2026-07-08) — pure, unit-tested, and shared so this monitor
            # never again picks a random needy line at random tiers. See
            # that module's docstring for the two production bugs this
            # fixes.

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
                try:
                    from app.domain.services.voice_pipeline.turn_helpers import (
                        _first_speaker_label,
                    )
                    # _first_speaker_label only ever returns "user" or "agent"
                    # (see its docstring) — comparing against "inbound" here
                    # was always False, so _is_caller_first was permanently
                    # False: the OPENING "Hello?" ladder never fired and
                    # should_suppress_mid_nudge always saw is_caller_first=False,
                    # so a caller-first call with a silent callee got 60s of
                    # dead air with no nudge at all. "user" is the correct
                    # caller-first sentinel — matches turn_ender.py's own
                    # `_first_speaker_label(session) == "user"` check for the
                    # instant-opener path.
                    _is_caller_first = _first_speaker_label(session) == "user"
                except Exception:
                    _is_caller_first = False

                _now = time.monotonic
                _last_caller_at = _now()   # last caller speech → drives the 60s hangup
                _silence_since = _now()    # last caller OR AI activity → drives nudges
                _last_nudge_at: Optional[float] = None
                _nudge_count = 0
                # Acoustic-guard bookkeeping. `_suppressing` makes the log one
                # line per EPISODE rather than one per tick — a caller talking
                # through a due nudge would otherwise produce a line every tick.
                _nudge_suppressed = 0
                _suppressing = False
                # Opening ("Hello?...Hello?...Hello?") and mid-call
                # ("Still there?") ladders are capped separately — a human
                # re-checking a silent pickup gives it 2-3 tries, which is a
                # different (shorter) budget than a mid-conversation check-in
                # after the caller has already engaged. Both still fall
                # through to the unconditional 60s hangup once exhausted, so
                # neither can nudge forever.
                _MID_MAX_NUDGES = int(os.getenv("VOICE_MID_MAX_NUDGES", "2"))
                _OPENING_MAX_NUDGES = int(os.getenv("VOICE_OPENING_MAX_NUDGES", "3"))
                _prev_user_turns = _count_user_turns()
                _was_active = False
                _tts_ended_at: Optional[float] = None

                while session.stt_active:
                    await asyncio.sleep(1.0)
                    if not session.stt_active:
                        break

                    try:
                        # Caller spoke since last tick → resets BOTH clocks (this is
                        # the real signal that they're present and engaged).
                        _uturns = _count_user_turns()
                        # Suppressed backchannels ("Okay", "Yes") never enter
                        # history but ARE the caller talking — honor the stamp
                        # turn_ender leaves so brief affirmations reset the
                        # clocks exactly like a full turn.
                        _bc_at = getattr(session, "_last_backchannel_monotonic", None)
                        _bc_fresh = (
                            _bc_at is not None
                            and (_now() - _bc_at) < 2.5
                        )
                        if _uturns > _prev_user_turns or _bc_fresh:
                            _prev_user_turns = _uturns
                            _last_caller_at = _now()
                            _silence_since = _now()
                            _last_nudge_at = None
                            _nudge_count = 0  # they're back — fresh nudge budget
                            _was_active = False
                            _tts_ended_at = None
                            # 2026-07-08: mark that the caller has produced real
                            # audio at least once this call — other modules can
                            # read this via getattr(session, "_caller_spoke_since_greeting", False)
                            # without any dependency on this monitor's internals.
                            try:
                                session._caller_spoke_since_greeting = True
                            except Exception:
                                pass
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

                        # Decide this tick with the pure `silence_action` (unit-tested):
                        # grace → wait; 60s caller silence → hangup; else nudge on the
                        # opening/mid threshold + min gap.
                        _in_grace = _tts_ended_at is not None and (
                            _now() - _tts_ended_at
                        ) < _TTS_GRACE_S
                        # ACOUSTIC caller activity, published once a second by
                        # the ingest loop. Read here rather than inside
                        # silence_action so that function stays pure, and
                        # required to be FRESH: the stash is ~1s granular, so
                        # anything older than _AUDIO_ACTIVE_MAX_AGE_S is a
                        # reading about a moment that has passed and must not
                        # suppress a nudge now. Missing attribute → False →
                        # exactly the pre-2026-08-13 behaviour, so a session
                        # object that never carries the field is unaffected.
                        _audio_active = False
                        try:
                            _rms_at = getattr(session, "_last_audio_rms_at", None)
                            if _rms_at is not None and (
                                _now() - _rms_at
                            ) <= _AUDIO_ACTIVE_MAX_AGE_S:
                                _audio_active = (
                                    float(getattr(session, "_last_audio_rms", 0.0) or 0.0)
                                    >= _AUDIO_ACTIVE_RMS
                                )
                        except Exception:
                            _audio_active = False

                        _action = silence_action(
                            caller_audio_active=_audio_active,
                            caller_silence_s=(_now() - _last_caller_at),
                            activity_silence_s=(_now() - _silence_since),
                            since_last_nudge_s=(
                                (_now() - _last_nudge_at)
                                if _last_nudge_at is not None else None
                            ),
                            in_grace=_in_grace,
                            is_caller_first=_is_caller_first,
                            user_turns=_prev_user_turns,
                            hangup_s=_SILENCE_HANGUP_S,
                            opening_s=_OPENING_HELLO_S,
                            mid_s=_MID_NUDGE_S,
                            nudge_gap_s=_NUDGE_MIN_GAP_S,
                            opening_gap_s=_OPENING_NUDGE_GAP_S,
                            # An agent-first call that has only said a bare
                            # pickup greeting is in the OPENING state too —
                            # see silence_action for the regression this fixes.
                            # _has_introduced is set False by agent_first
                            # precisely when the greeting was a bare hello.
                            agent_awaiting_first_reply=(
                                not getattr(session, "_has_introduced", False)
                            ),
                        )
                        if _action == "wait":
                            # DID THE ACOUSTIC GUARD ACTUALLY DO ANYTHING?
                            #
                            # The 2026-08-13 fix stops the ladder shouting over
                            # a caller the STT cannot hear, and its entire
                            # observable effect is a nudge that does NOT happen.
                            # Absence is not evidence: a guard that silently
                            # never fires and a guard that saved twenty calls
                            # produce identical logs, which is the reporting
                            # failure that hid two dead STT streams in the first
                            # place. So say it out loud — but only when the
                            # guard was DECISIVE. Re-running the pure decision
                            # without the acoustic input is the exact test of
                            # that, and it only runs on ticks where the caller
                            # is audibly talking, so it costs nothing per call.
                            if _audio_active:
                                _would_have = silence_action(
                                    caller_audio_active=False,
                                    caller_silence_s=(_now() - _last_caller_at),
                                    activity_silence_s=(_now() - _silence_since),
                                    since_last_nudge_s=(
                                        (_now() - _last_nudge_at)
                                        if _last_nudge_at is not None else None
                                    ),
                                    in_grace=_in_grace,
                                    is_caller_first=_is_caller_first,
                                    user_turns=_prev_user_turns,
                                    hangup_s=_SILENCE_HANGUP_S,
                                    opening_s=_OPENING_HELLO_S,
                                    mid_s=_MID_NUDGE_S,
                                    nudge_gap_s=_NUDGE_MIN_GAP_S,
                                    opening_gap_s=_OPENING_NUDGE_GAP_S,
                                    agent_awaiting_first_reply=(
                                        not getattr(session, "_has_introduced", False)
                                    ),
                                )
                                if _would_have == "nudge":
                                    _nudge_suppressed += 1
                                    # Mirrored onto the session so the per-call
                                    # audit can be written from outside this
                                    # coroutine. The monitor is ended by
                                    # task.cancel() at hangup, so anything that
                                    # relies on falling out of this loop never
                                    # runs.
                                    try:
                                        session._nudges_suppressed = _nudge_suppressed
                                    except Exception:
                                        pass
                                    if not _suppressing:
                                        _suppressing = True
                                        logger.info(
                                            "[SilenceMonitor] %s — nudge SUPPRESSED, "
                                            "caller audio live rms=%.0f n=%d "
                                            "(would have talked over them)",
                                            call_id[:12],
                                            float(getattr(session, "_last_audio_rms", 0.0) or 0.0),
                                            _nudge_suppressed,
                                        )
                                else:
                                    _suppressing = False
                            else:
                                _suppressing = False
                            continue
                        _suppressing = False

                        if _action == "hangup":
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

                        # Never nudge a MACHINE. Once screening/voicemail wording
                        # was heard (machine_detection flags), "Sorry, did I lose
                        # you?" at a recording or a screening hold is pure waste
                        # (observed 3x per voicemail call, 2026-07-08 audit) — and
                        # during a screening hold, silence is the correct
                        # etiquette. The 60s hangup above still applies.
                        if _action == "nudge" and (
                            getattr(session, "_machine_screening", False)
                            or getattr(session, "_amd_voicemail", False)
                        ):
                            _last_nudge_at = _now()  # keep the gap clock sane
                            continue

                        # _action == "nudge": opening (caller-first, not yet spoken)
                        # → a soft "Hello?"; otherwise a light check-in. Computed
                        # before the nudge-count cap below because opening and
                        # mid now have different budgets (_OPENING_MAX_NUDGES vs
                        # _MID_MAX_NUDGES).
                        # MUST match silence_action's `opening` rule exactly.
                        # 2026-08-12: these two drifted apart and the bug was
                        # subtle — silence_action correctly decided to nudge an
                        # agent-first call that had only said a bare hello, but
                        # THIS line still said "not opening", so the phrase came
                        # from the MID ladder: "No rush — I'm still on the line
                        # whenever you're ready." That needy re-offer landing
                        # before the prospect has spoken is precisely the
                        # 2026-07-08 bug should_suppress_mid_nudge exists to
                        # prevent — so a half-applied fix turned dead air into
                        # the wrong words. Deciding WHETHER to nudge and
                        # choosing WHAT to say must read the same state.
                        _awaiting_first_reply = not getattr(
                            session, "_has_introduced", False
                        )
                        _opening = _prev_user_turns == 0 and (
                            _is_caller_first or _awaiting_first_reply
                        )

                        # Cap nudges per call: after the ladder's budget a silent
                        # human isn't coming back, and one more "still with me?"
                        # / "Hello?" reads as nagging (audited calls had up to
                        # SIX). Let the 60s caller-silence hangup finish the call
                        # quietly — this is the bound that keeps the re-greet
                        # ladder from ever looping forever.
                        _max_nudges = _OPENING_MAX_NUDGES if _opening else _MID_MAX_NUDGES
                        if _action == "nudge" and _nudge_count >= _max_nudges:
                            _last_nudge_at = _now()
                            continue

                        # 2026-07-08 guard: on an AGENT-FIRST call where the
                        # caller has NEVER spoken (no real turn, no fresh
                        # backchannel), a MID nudge would be the caller's first
                        # ever line from us — "I'm still here whenever you're
                        # ready" landing before they've said a word. Skip the
                        # nudge entirely; only the 60s hangup still applies.
                        # Fails open (nudges as before) if the check itself errors.
                        if not _opening:
                            try:
                                _caller_spoke = bool(
                                    getattr(session, "_caller_spoke_since_greeting", False)
                                ) or _prev_user_turns > 0
                                # An agent that has only said a bare hello is
                                # in the OPENING ladder, not the MID one — the
                                # suppression below exists to stop "I'm still
                                # here whenever you're ready" landing before
                                # the prospect has spoken, which is a MID
                                # phrase. Suppressing here instead left a bare
                                # "Hi there." with no follow-up at all (see
                                # silence_action, 2026-08-12).
                                # _awaiting_first_reply computed once above,
                                # with _opening — the three decisions (nudge?
                                # / suppress? / which words?) must read one
                                # value, not three copies that can drift.
                                if not _awaiting_first_reply and turn_director.should_suppress_mid_nudge(
                                    is_caller_first=_is_caller_first,
                                    caller_has_ever_spoken=_caller_spoke,
                                ):
                                    _last_nudge_at = _now()
                                    continue
                            except Exception as _guard_exc:
                                logger.debug(
                                    "[SilenceMonitor] mid-nudge suppression check "
                                    "failed, falling through to normal nudge: %s",
                                    _guard_exc,
                                )

                        try:
                            # 2026-08-11: read the per-call LLM-generated
                            # ladder if opening_ladder.py stashed one during
                            # pre-warm (see prewarm._start_opening_ladder_
                            # generation). getattr default is None, which
                            # choose_silence_phrase treats identically to not
                            # passing the argument at all — this line changes
                            # nothing for the (default-off) static path.
                            _phrase = turn_director.choose_silence_phrase(
                                is_opening=_opening, nudge_index=_nudge_count,
                                ladder=getattr(session, "_opening_ladder", None)
                                if _opening else None,
                            )
                        except Exception as _phrase_exc:
                            logger.debug(
                                "[SilenceMonitor] choose_silence_phrase failed, "
                                "falling back to 'Still there?': %s", _phrase_exc,
                            )
                            _phrase = "Hello?" if _opening else "Still there?"
                        logger.info(
                            "[SilenceMonitor] %s — silence (%s), nudging: %r",
                            call_id[:12], "opening" if _opening else "mid", _phrase,
                        )
                        try:
                            await self._p.synthesize_and_send_audio(session, _phrase, websocket)
                            _record_silence_check(self._p, session, _phrase)
                        except Exception as _sm_exc:
                            logger.debug("[SilenceMonitor] TTS failed: %s", _sm_exc)
                        _last_nudge_at = _now()
                        _nudge_count += 1
                        try:
                            session._nudges_spoken = _nudge_count
                        except Exception:
                            pass
                        _silence_since = _now()  # give them room to answer before re-nudging
                    except Exception as exc:
                        logger.warning(
                            "[SilenceMonitor] tick error call=%s err=%s — skipping tick",
                            call_id[:12], exc, exc_info=True,
                        )
                        continue

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

