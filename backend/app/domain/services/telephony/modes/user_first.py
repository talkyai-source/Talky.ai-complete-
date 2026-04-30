"""User-first (caller-speaks-first) call-flow handler.

Used when the campaign owner selects ``first_speaker = "user"``. The bridge
plays no greeting; instead it waits for the callee to speak first and arms a
silence safety net that fires a fallback prompt (and eventually hangs up) if
the callee never says anything.

This module is the home for the comment block, helper predicates, and the
async ``_handle_user_first_silence`` task that the lifecycle layer schedules
when ``resolve_first_speaker(voice_session) == "user"``.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Internal-cue prefix used to differentiate synthetic silence-handler
# instructions from real callee speech in conversation_history. Real
# user messages will never start with this token.
_USER_FIRST_CUE_PREFIX = "[CALLEE_"


def _user_first_open_seconds() -> float:
    """
    Initial silence window for outbound user-first calls.

    This must be a real safety-net delay, not a sub-second beat. If it fires
    too early, the pre-synthesized fallback greeting starts playing before
    Flux has observed a normal "Hello?", which makes the first interaction
    feel delayed even though the STT/LLM/TTS turn itself is fast.
    """
    raw = os.getenv("TELEPHONY_USER_FIRST_OPEN_S")
    try:
        value = float(raw) if raw is not None else 5.0
    except (TypeError, ValueError):
        logger.warning(
            "invalid TELEPHONY_USER_FIRST_OPEN_S=%r — using 5.0s",
            raw,
        )
        return 5.0

    if value < 2.0:
        logger.warning(
            "TELEPHONY_USER_FIRST_OPEN_S=%.1f is too low for caller-speaks-first; "
            "clamping to 2.0s to avoid fallback greeting racing caller speech",
            value,
        )
        return 2.0
    return value


def _user_first_fallback_enabled() -> bool:
    """
    Whether caller-speaks-first mode may play an automatic opener.

    Default is false because the production requirement for this mode is a
    truly silent listener. The fallback greeting is useful for demos, but it
    races real callers and is the observed source of the first-turn delay.
    """
    return (os.getenv("TELEPHONY_USER_FIRST_FALLBACK_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _handle_user_first_silence(voice_session, pbx_call_id: str) -> None:
    """Safety-net silence handler for user-first outbound calls.

    In user-first mode the AI is COMPLETELY SILENT after the callee answers.
    Flux (STT) is already connected and the pipeline is running — audio is
    flowing from the media gateway into Flux the instant the callee picks up.

    If the callee says anything ("Hello?"), Flux fires EndOfTurn, the
    pipeline's normal handle_turn_end processes it, and the LLM responds
    naturally.  This handler is NOT involved in that happy path.

    This handler is a SAFETY NET only: if the callee stays completely
    silent for `open_s` seconds (default 5.0), it opens the call so the
    session does not deadlock. The handler is not scheduled unless
    TELEPHONY_USER_FIRST_FALLBACK_ENABLED is explicitly enabled.
    """
    from app.domain.models.conversation import Message, MessageRole

    session = voice_session.call_session
    call_id = voice_session.call_id

    # True safety-net window. Callees often answer, move the phone to their
    # ear, then say "Hello?" around the first second. A sub-second fallback
    # races that normal behavior and plays the pre-synthesized greeting over
    # the caller's first utterance.
    open_s = _user_first_open_seconds()
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

    def _flux_heard_speech() -> bool:
        """Check if Flux has detected speech-in-progress (StartOfTurn fired)
        even before a complete EndOfTurn message appears in history.
        This is the barge-in event set by _on_barge_in_direct in the pipeline."""
        barge_ev = getattr(session, "barge_in_event", None)
        if barge_ev is not None and barge_ev.is_set():
            return True
        # Also check if the pipeline is actively processing a user turn
        if session.llm_active or session.tts_active:
            return True
        return False

    async def _wait_or_speech(timeout: float) -> bool:
        """Sleep up to `timeout`s. Return True if the callee spoke (a real
        user message arrived OR Flux detected speech-in-progress) so the
        caller can bail out of the state machine. Polls every 50ms for
        minimal detection latency."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if _real_user_count() > initial_user_msgs:
                return True
            if _flux_heard_speech():
                logger.info(
                    "user_first_speech_detected call=%s — Flux heard speech, "
                    "cancelling silence handler",
                    call_id[:12],
                )
                return True
            await asyncio.sleep(0.05)
        # Final check before returning False
        if _real_user_count() > initial_user_msgs or _flux_heard_speech():
            return True
        return False

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
        history_len_before = len(session.conversation_history)
        try:
            session.current_user_input = cue
            logger.info(
                "user_first_phase phase=%s call=%s",
                phase, call_id[:12],
            )
            await voice_session.pipeline.handle_turn_end(
                session, websocket=None, source=f"user_first_{phase}"
            )
            # Scrub the cue from conversation_history. handle_turn_end appends
            # current_user_input as a USER message before calling the LLM, so
            # the bracketed instruction sits permanently in history unless we
            # remove it here. The LLM reading "[CALLEE_SILENT_AT_PICKUP — ...]"
            # as real callee speech causes re-introduction on every subsequent turn.
            h = session.conversation_history
            for i in range(len(h) - 1, history_len_before - 1, -1):
                if (
                    getattr(h[i], "role", None) == MessageRole.USER
                    and (h[i].content or "").startswith(_USER_FIRST_CUE_PREFIX)
                ):
                    del h[i]
                    break
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
        # Phase 1 — listen for the callee to speak.  Flux is already
        # connected and the pipeline is streaming audio into it.  The AI
        # stays COMPLETELY SILENT during this window.  If the callee says
        # ANYTHING ("Hello?"), Flux fires EndOfTurn → handle_turn_end
        # runs → LLM responds naturally.  We just exit.
        if await _wait_or_speech(open_s):
            return

        # Phase 2 — callee hasn't spoken after the initial window.
        # FAST PATH: play pre-synthesized greeting directly (~5ms).
        # SLOW PATH: fall back to LLM + TTS (~2.5s) if no pre-synth.
        presynth_chunks = getattr(voice_session, "_presynth_greeting_audio", None)
        presynth_text = getattr(voice_session, "_presynth_greeting_text", None)

        if presynth_chunks and presynth_text:
            # Play pre-synthesized fallback greeting INSTANTLY.
            # This is the same greeting that agent-first mode would play,
            # but here it only fires after a real silence safety-net window.
            import time as _time
            _t0 = _time.monotonic()
            logger.info(
                "user_first_presynth_fallback call=%s chunks=%d text=%r",
                call_id[:12], len(presynth_chunks), presynth_text[:60],
            )

            if not (session.tts_active or session.llm_active):
                from app.domain.models.conversation import Message as _Msg, MessageRole as _MR
                session.llm_active = True
                session.tts_active = True
                barge_in_event = getattr(session, "barge_in_event", None)
                was_interrupted = False
                chunks_sent = 0

                try:
                    for chunk in presynth_chunks:
                        if barge_in_event and barge_in_event.is_set():
                            was_interrupted = True
                            barge_in_event.clear()
                            try:
                                await voice_session.media_gateway.clear_output_buffer(
                                    voice_session.call_id
                                )
                            except Exception:
                                pass
                            logger.info(
                                "user_first_presynth_barge_in call=%s at_chunk=%d",
                                call_id[:12], chunks_sent,
                            )
                            break
                        await voice_session.media_gateway.send_audio(
                            voice_session.call_id, chunk
                        )
                        chunks_sent += 1

                    if not was_interrupted:
                        try:
                            await voice_session.media_gateway.flush_tts_buffer(
                                voice_session.call_id
                            )
                        except Exception:
                            pass

                    _elapsed_ms = (_time.monotonic() - _t0) * 1000
                    logger.info(
                        "user_first_presynth_done call=%s chunks=%d "
                        "elapsed_ms=%.0f interrupted=%s",
                        call_id[:12], chunks_sent, _elapsed_ms, was_interrupted,
                    )

                    # Append the greeting to conversation history so the LLM
                    # has context for subsequent turns.
                    session.conversation_history.append(
                        _Msg(role=_MR.ASSISTANT, content=presynth_text)
                    )
                finally:
                    session.llm_active = False
                    session.tts_active = False
        else:
            # Slow path: no pre-synth available, use LLM + TTS.
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
        # Look up the live adapter via the bridge module — it owns the
        # singleton and will continue to until lifecycle.py takes over in a
        # later refactor step.
        from app.api.v1.endpoints import telephony_bridge as _tb
        _adapter_ref = getattr(_tb, "_adapter", None)
        if _adapter_ref is not None:
            try:
                await _adapter_ref.hangup(pbx_call_id)
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
