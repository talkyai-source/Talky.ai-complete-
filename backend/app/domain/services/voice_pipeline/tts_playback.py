"""TTS synthesis + playback for the voice pipeline.

Extracted verbatim from VoicePipelineService.synthesize_and_send_audio
(item 2, slice 3). This is the real-time TTS streaming loop: it streams
TTS chunks to the media gateway, watches the barge-in event to stop
instantly on user interruption, tracks latency, and falls back to a spoken
error once if the provider yields nothing.

Behaviour is identical to the original method. Collaborators
(tts_provider / media_gateway / latency_tracker / tts_sample_rate /
record_silent_turn) are injected at construction; the barge-in event is
passed per call (the service resolves it via _barge_in_event_for).
VoicePipelineService keeps synthesize_and_send_audio() as a thin delegator
— external callers use ``pipeline.synthesize_and_send_audio`` and a test
mocks it, so it stays a method.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import WebSocket

from app.domain.models.session import CallSession
from app.infrastructure.telephony.browser_media_gateway import SessionGoneError

logger = logging.getLogger(__name__)


class TtsPlayback:
    """Streams TTS audio to the caller with barge-in-aware interruption."""

    def __init__(self, pipeline) -> None:
        # Hold the owning VoicePipelineService and read its collaborators
        # (tts_provider / media_gateway / latency_tracker / tts_sample_rate /
        # _record_silent_turn) at CALL time, not construction time. The
        # original method read self.<dep> on each call, and tests (and any
        # runtime reconfiguration) patch those attributes after construction —
        # snapshotting them here would silently ignore such changes.
        self._p = pipeline

    def _record_barge_in_stop(self, session: CallSession) -> None:
        """Observe how long after the barge-in signal we actually silenced the
        caller (output-buffer clear). Called only at active-speech stop points;
        always resets the per-fire stamp so it can't bleed into a later turn.

        Also flags that the agent's speech WAS interrupted this turn, so
        turn_ender can classify the interrupting utterance and record whether
        the stop was warranted (interruption-quality metrics, gap #2)."""
        session._agent_was_interrupted = True
        t0 = getattr(session, "_barge_in_set_monotonic", None)
        session._barge_in_set_monotonic = None
        if t0 is None:
            return
        try:
            from app.infrastructure.metrics.voice_metrics import (
                observe_barge_in_stop_ms,
            )
            observe_barge_in_stop_ms((time.monotonic() - t0) * 1000.0)
        except Exception:  # metrics must never break playback
            pass

    async def synthesize_and_send(
        self,
        session: CallSession,
        text: str,
        websocket: Optional[WebSocket] = None,
        *,
        barge_in_event: Optional[asyncio.Event] = None,
        track_latency: bool = True,
    ) -> bool:
        """
        Synthesize TTS audio and stream it to the media gateway.
        Returns True if TTS was interrupted by barge-in, False on normal completion.
        """
        call_id = session.call_id
        # Is THIS invocation the recovery attempt rather than the turn itself?
        # Both fallback paths set the flag before recursing, and the nested
        # call's finally clears it, so reading it at entry is the one place the
        # answer is unambiguous. A fallback that also produces no audio must not
        # file its own silent-turn record: the turn was already going to be
        # reported by the outer call, and counting both would double every
        # unrecoverable turn in the metric that exists to measure exactly that.
        _is_recovery_attempt = bool(getattr(session, "_tts_fallback_attempted", False))
        # Do not begin speaking on top of a caller who is still mid-sentence.
        # Only holds when handle_barge_in armed it (a barge-in landed while a
        # FINAL answer was still generating), is capped, and fails open — see
        # voice_pipeline.playback_gate for why a hold beats a cancel here.
        from app.domain.services.voice_pipeline.playback_gate import (
            await_caller_pause,
        )
        await await_caller_pause(session, call_id=call_id)

        # Mark TTS as active here so handle_turn_end skips if a greeting
        # or a previous turn is already speaking.
        session.tts_active = True

        interrupted = False
        completed = False
        silent_reason: Optional[str] = None
        first_chunk = True
        first_chunk_sent = False  # track whether any audio reached the gateway
        try:
            # If user spoke during the LLM call, the barge-in event is already set.
            # Don't start TTS — send the stop signal immediately and return.
            if barge_in_event and barge_in_event.is_set():
                interrupted = True
                logger.info(
                    "barge_in_before_tts",
                    extra={"call_id": call_id, "turn_id": session.turn_id},
                )
                barge_in_event.clear()
                try:
                    await self._p.media_gateway.clear_output_buffer(call_id)
                except Exception as _e:
                    logger.debug("barge_in_clear_buffer_failed call_id=%s: %s", call_id[:8], _e)
                # Barge-in landed before this sentence started speaking — not an
                # active-speech stop, so drop the stamp without recording it.
                session._barge_in_set_monotonic = None
                if websocket:
                    try:
                        await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                    except Exception as _e:
                        logger.debug("barge_in_ws_notify_failed call_id=%s: %s", call_id[:8], _e)
                # Must return `interrupted` (True), not bare `return` (None).
                # A bare return gives None to the caller, which is falsy — the
                # sentence loop in _stream_llm_and_tts would not break and would
                # immediately call TTS again with the next sentence, causing the
                # AI to start speaking again right after being interrupted.
                return interrupted

            # TTS hard inter-chunk timeout — protects against silent WS hangs
            # mid-sentence. Pattern adapted from Pipecat
            # (https://github.com/pipecat-ai/pipecat) — same shape they use for
            # Deepgram STT reconnect: convert `async for` into manual
            # `__anext__()` with a per-step deadline so a stuck provider socket
            # ends the turn cleanly instead of freezing the call.
            #
            # 5s is intentionally larger than typical first-chunk latency
            # (~250ms for Cartesia/Chirp/ElevenLabs streaming) so it never
            # fires on healthy traffic. It only catches the rare case where
            # the upstream WS dies without notifying the SDK.
            _TTS_INTER_CHUNK_TIMEOUT_S = 5.0
            _tts_iter = self._p.tts_provider.stream_synthesize(
                text,
                voice_id=session.voice_id,
                sample_rate=self._p.tts_sample_rate,
                call_id=call_id,
            ).__aiter__()
            provider_exhausted = False
            # NOTE: sample-alignment carry across chunk boundaries used to live
            # here (int16-only, 2-byte). It's now centralized in
            # TelephonyMediaGateway.send_audio, keyed on the session's actual
            # _tts_source_format (2 bytes for s16le, 4 for f32le) — a hardcoded
            # 2-byte carry silently let misaligned f32le chunks through into
            # pcm_float32_to_int16(), which raised and dropped the whole chunk.
            # Centralizing in the gateway also covers the greeting path
            # (voice_orchestrator.send_greeting), which calls send_audio
            # directly and never went through this carry.
            # One retry if the provider yields NO audio within the inter-chunk
            # timeout (a brief stall before the sentence starts). Safe — nothing
            # has played yet, so no duplicate audio.
            stall_retried = False
            empty_retried = False
            # Captured at the moment the stream ends empty, NOT re-read in the
            # finally. The fallback below is a nested synthesize_and_send, and
            # its own finally clears session.tts_active — so a finally that
            # re-read the flag would see the NESTED call's teardown and label
            # this turn "interrupted_before_audio" when no caller interrupted
            # anything. Caught by test_the_two_silent_causes_are_labelled_
            # differently; the whole point of the label is to be trustworthy.
            stopped_by_caller_at_end: Optional[bool] = None
            while True:
                try:
                    audio_chunk = await asyncio.wait_for(
                        _tts_iter.__anext__(),
                        timeout=_TTS_INTER_CHUNK_TIMEOUT_S,
                    )
                except StopAsyncIteration:
                    # A PROVIDER THAT RETURNS NOTHING, AND DOES SO CLEANLY.
                    #
                    # 2026-08-17, call b3350aee: a turn was recorded as
                    # `turn_silent_reason reason=provider_empty_stream` — the
                    # agent had a reply and said none of it. The stall retry
                    # below never had a chance: it triggers on TimeoutError,
                    # and a stream that ends immediately with zero chunks does
                    # not time out. It does not raise either. So the one
                    # failure mode with no symptom was also the one with no
                    # recovery, which is the same shape as the STT stream that
                    # went deaf on 2026-08-13 and the TTS exception path that
                    # has had a fallback for months.
                    #
                    # Retrying is safe for exactly the reason the stall retry
                    # is safe: nothing has reached the gateway, so there is no
                    # audio to duplicate. Re-opening the iterator also gets a
                    # fresh provider socket if the old one had quietly died.
                    # ...BUT NOT IF THE CALLER IS THE REASON IT IS EMPTY.
                    #
                    # The production case that started this (b3350aee, 21:02:29)
                    # turned out to be a barge-in landing before the first chunk
                    # played: the interrupt cancelled the turn, the stream ended
                    # with zero chunks, and the finally block labelled it
                    # `provider_empty_stream` — a provider fault it never was.
                    # Retrying THAT would re-synthesise a reply the caller had
                    # just talked over and speak it at them, which is worse than
                    # the silence being fixed. `tts_active` is set False by
                    # interrupt_playback's first step, so it is the precise
                    # question: is this turn still supposed to be speaking?
                    _stopped_by_caller = (
                        (barge_in_event is not None and barge_in_event.is_set())
                        or not getattr(session, "tts_active", True)
                    )
                    if not first_chunk_sent:
                        stopped_by_caller_at_end = _stopped_by_caller
                    if not first_chunk_sent and not empty_retried and not _stopped_by_caller:
                        empty_retried = True
                        logger.warning(
                            "tts_empty_stream call=%s text=%r — provider ended "
                            "with zero audio chunks; retrying synthesis once "
                            "(nothing has played, so no duplication)",
                            call_id[:12], text[:60],
                        )
                        try:
                            await _tts_iter.aclose()
                        except Exception:
                            pass
                        _tts_iter = self._p.tts_provider.stream_synthesize(
                            text,
                            voice_id=session.voice_id,
                            sample_rate=self._p.tts_sample_rate,
                            call_id=call_id,
                        ).__aiter__()
                        continue
                    provider_exhausted = True
                    break
                except asyncio.TimeoutError:
                    # If NOTHING has played yet, the synthesis never really
                    # started (a brief provider stall — Cartesia's docs note
                    # idle WebSockets close after 5 min and transient stalls
                    # happen). Retry the whole synthesis ONCE: safe because no
                    # audio was emitted (no duplication), and a fresh
                    # stream_synthesize reopens the Cartesia WS if it had dropped.
                    if not first_chunk_sent and not stall_retried:
                        stall_retried = True
                        logger.warning(
                            "tts pre-first-chunk stall %.1fs call=%s — retrying synthesis once",
                            _TTS_INTER_CHUNK_TIMEOUT_S, call_id[:12],
                        )
                        try:
                            await _tts_iter.aclose()
                        except Exception:
                            pass
                        _tts_iter = self._p.tts_provider.stream_synthesize(
                            text,
                            voice_id=session.voice_id,
                            sample_rate=self._p.tts_sample_rate,
                            call_id=call_id,
                        ).__aiter__()
                        continue
                    logger.error(
                        "tts_inter_chunk_timeout call_id=%s timeout_s=%.1f "
                        "text=%r — ending turn cleanly to avoid pipeline freeze",
                        call_id[:12], _TTS_INTER_CHUNK_TIMEOUT_S, text[:60],
                    )
                    # Close the provider stream so the Cartesia/ElevenLabs socket
                    # is released now instead of waiting for GC (the first-stall
                    # retry path above already does this; the terminal path must
                    # too, or stalled streams briefly accumulate under load).
                    try:
                        await _tts_iter.aclose()
                    except Exception:
                        pass
                    break
                if barge_in_event and barge_in_event.is_set():
                    logger.info(f"Barge-in interrupted TTS for call {call_id}")
                    interrupted = True
                    barge_in_event.clear()
                    try:
                        await self._p.media_gateway.clear_output_buffer(call_id)
                    except Exception as _exc:
                        logger.debug("clear_output_buffer mid-TTS failed: %s", _exc)
                    # Caller interrupted active speech — measure how fast we went
                    # silent (target <60ms).
                    self._record_barge_in_stop(session)
                    # Tell the browser to stop playing immediately — don't wait for
                    # handle_barge_in to do it after handle_turn_end completes.
                    if websocket:
                        try:
                            await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                        except Exception as _exc:
                            logger.debug("tts_interrupted WS send failed: %s", _exc)
                    break
                if first_chunk:
                    if track_latency:
                        self._p.latency_tracker.mark_tts_first_chunk(call_id)
                        self._p.latency_tracker.mark_response_start(call_id)
                        self._p.latency_tracker.mark_audio_start(call_id)
                    first_chunk = False
                raw = audio_chunk.data if hasattr(audio_chunk, "data") else audio_chunk
                # Sample-alignment carry (a provider can split a sample across
                # chunk boundaries — dropping the orphan byte(s) byte-shifts
                # every following sample, e.g. the ElevenLabs eleven_v3 buzz)
                # is now handled centrally in
                # TelephonyMediaGateway.send_audio, format-aware for both
                # s16le and f32le. See the NOTE above.
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)
                if not raw:
                    continue
                if not first_chunk_sent:
                    # TEMP diagnostic (cartesia/elevenlabs buzz investigation):
                    # the probe proved providers emit clean 16kHz audio, so a
                    # buzz means the gateway's source-format disagrees with the
                    # actual bytes. Log provider + gateway_fmt + rate + first
                    # bytes ONCE per call to catch the runtime mismatch.
                    try:
                        logger.info(
                            "TTS_FMT_DEBUG call=%s provider=%s gateway_fmt=%s req_rate=%s "
                            "first_bytes=%d head=%s",
                            call_id[:8],
                            getattr(self._p.tts_provider, "name", "?"),
                            getattr(self._p.media_gateway, "_tts_source_format", "?"),
                            getattr(self._p, "tts_sample_rate", "?"),
                            len(raw), bytes(raw[:8]).hex(),
                        )
                    except Exception:
                        pass
                await self._p.media_gateway.send_audio(call_id, raw)
                first_chunk_sent = True  # at least one chunk reached the gateway
                # WHEN DID AUDIO LAST LEAVE FOR THE CALLER?
                #
                # `tts_active` answers "is Python still streaming", and it goes
                # False the instant this loop ends. The caller's ear is further
                # downstream: the C++ gateway holds its own queue and paces it
                # in real time — every interrupt today that asked found 240-300ms
                # still sitting in it. So a barge-in landing just after this loop
                # finishes can be told "nothing is playing" while the gateway is
                # still draining.
                #
                # Underscore-prefixed because CallSession is a pydantic model
                # that REJECTS undeclared public attributes (see
                # test_session_scratch_attrs — two features were silently dead
                # for days on exactly that).
                try:
                    session._last_tts_chunk_at = time.monotonic()
                except Exception:
                    pass
                # Check barge-in again immediately after send: barge-in may have
                # fired during the gateway send await before the next TTS chunk arrives.
                if barge_in_event and barge_in_event.is_set():
                    logger.info(f"Barge-in (post-send) interrupted TTS for call {call_id}")
                    interrupted = True
                    barge_in_event.clear()
                    try:
                        await self._p.media_gateway.clear_output_buffer(call_id)
                    except Exception as _exc:
                        logger.debug("clear_output_buffer post-send failed: %s", _exc)
                    self._record_barge_in_stop(session)
                    if websocket:
                        try:
                            await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                        except Exception as _exc:
                            logger.debug("tts_interrupted post-send WS send failed: %s", _exc)
                    break
            if (
                provider_exhausted
                and not interrupted
                and not first_chunk_sent
                and not getattr(session, "_tts_fallback_attempted", False)
                # Same guard as the retry, for the same reason: a caller who
                # just interrupted must not be answered with "could you say
                # that again?".
                and getattr(session, "tts_active", True)
                and not (barge_in_event is not None and barge_in_event.is_set())
            ):
                # Both attempts produced no audio. The turn is otherwise about
                # to end in total silence, which on a phone call is the worst
                # available outcome — the caller hears nothing and cannot tell
                # whether the line dropped. Say something short instead.
                #
                # Deliberately NOT the original text: it has now failed to
                # synthesise twice, so a third attempt at the same string is
                # the least likely thing to work. A brief line is both more
                # likely to come back and more honest about what happened.
                # Guarded by the same _tts_fallback_attempted flag the
                # exception path uses, so a failing fallback cannot recurse.
                session._tts_fallback_attempted = True
                logger.error(
                    "tts_empty_stream_after_retry call=%s text=%r — speaking a "
                    "short fallback so the turn is not silent",
                    call_id[:12], text[:60],
                )
                try:
                    await self.synthesize_and_send(
                        session,
                        "Sorry — could you say that again?",
                        websocket,
                        barge_in_event=barge_in_event,
                        track_latency=False,
                    )
                except Exception:
                    pass

            if provider_exhausted and not interrupted:
                # Normal completion (not interrupted by barge-in) — flush any
                # remaining bytes in the gateway output buffer so the last
                # portion of audio is not silently dropped.
                flush = getattr(self._p.media_gateway, "flush_tts_buffer", None)
                if not flush:
                    flush = getattr(self._p.media_gateway, "flush_audio_buffer", None)
                if flush:
                    try:
                        await flush(call_id)
                    except Exception as _exc:
                        logger.debug("flush buffer failed: %s", _exc)
                completed = True
        except SessionGoneError:
            # Browser WebSocket was torn down while TTS was streaming.
            # Exit the loop silently — this is normal teardown, not an error.
            silent_reason = "session_gone"
            logger.debug("TTS loop stopped: browser session %s already gone", call_id)
        except Exception as e:
            silent_reason = "tts_exception"
            logger.error(f"TTS synthesis error for call {call_id}: {e}", exc_info=True)
            # FIX 4 — If no audio reached the gateway yet, play a one-shot fallback
            # so the caller gets an explicit signal instead of silence.  The
            # _tts_fallback_attempted flag prevents infinite recursion when the
            # fallback itself fails (e.g. TTS provider is fully down).
            if not first_chunk_sent and not getattr(session, "_tts_fallback_attempted", False):
                session._tts_fallback_attempted = True
                try:
                    await self.synthesize_and_send(
                        session,
                        "I'm sorry, I couldn't respond. Please say that again.",
                        websocket,
                        barge_in_event=barge_in_event,
                        track_latency=False,
                    )
                except Exception:
                    pass
        finally:
            if not interrupted and first_chunk:
                if silent_reason is None and completed:
                    # DISTINGUISH THE TWO REASONS A TURN CAN END WITH NO AUDIO.
                    #
                    # Until 2026-08-17 both were filed as `provider_empty_stream`,
                    # and the label cost real time: a turn that produced no audio
                    # because the CALLER interrupted it before playback started
                    # read in the logs as a broken TTS provider. Chasing that
                    # label nearly shipped a retry that would have re-spoken a
                    # reply over the top of the person who had just interrupted.
                    #
                    # `tts_active` is cleared by interrupt_playback's first step,
                    # so a False here means an interrupt ran during this turn.
                    # A turn nobody stopped, that still yielded nothing, is the
                    # genuine provider fault the retry above exists for.
                    if stopped_by_caller_at_end is not None:
                        _caller_stopped_it = stopped_by_caller_at_end
                    else:
                        _caller_stopped_it = (
                            (barge_in_event is not None and barge_in_event.is_set())
                            or not getattr(session, "tts_active", True)
                        )
                    silent_reason = (
                        "interrupted_before_audio" if _caller_stopped_it
                        else "provider_empty_stream"
                    )
                if silent_reason is not None and not _is_recovery_attempt:
                    self._p._record_silent_turn(call_id, silent_reason)
            session._tts_fallback_attempted = False
            if track_latency:
                self._p.latency_tracker.mark_tts_end(call_id)
                if interrupted:
                    self._p.latency_tracker.mark_interrupted(call_id, reason="barge_in")
                elif completed:
                    self._p.latency_tracker.mark_completed(call_id)
            session.tts_active = False
        return interrupted
