"""Agent-first (assistant-speaks-first) call-flow handler.

Used when the campaign owner selects ``first_speaker = "agent"`` (the default,
and what most campaigns pick). On answer:

* **Fast path** — pre-synthesized greeting chunks (built during the ringing
  phase by lifecycle's ``_on_ringing``) are pumped directly into the media
  gateway. First audio reaches the callee within ~5ms.
* **Slow path** — if pre-synth is unavailable, falls back to realtime TTS via
  ``voice_session.pipeline.synthesize_and_send_audio``.

Barge-in: if the callee speaks during playback, only the spoken portion of the
greeting is persisted to ``conversation_history`` so the LLM does not echo the
unspoken tail on the next turn.
"""
from __future__ import annotations

import asyncio
import logging

from app.domain.services.telephony.config import (
    _build_outbound_greeting,
    _build_call_greeting,
)

logger = logging.getLogger(__name__)

# 2026-07-08: opener-content guard. Prod surfaced 2 calls where the spoken
# opener was a bare "Hello?" — the pre-synth greeting text was empty/
# whitespace or degraded to a filler word, so the callee heard what sounded
# like a wrong-number call instead of a real introduction. These are the
# filler-only strings we refuse to speak as a whole opener (punctuation is
# stripped before the comparison, so "Hello?", "Hello.", "hi" etc. all match).
_BARE_FILLER_OPENERS = {"hello", "hi", "hey", "hiya", "yo"}


def _has_real_opener_content(text) -> bool:
    """True when `text` is genuine greeting content — not empty/whitespace
    and not just a bare filler word standing in for the whole opener."""
    if not text or not isinstance(text, str):
        return False
    stripped = text.strip().strip("?!.,;: ").strip()
    if not stripped:
        return False
    return stripped.lower() not in _BARE_FILLER_OPENERS


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

        # 2026-07-08: opener-content guard — never speak a bare "Hello?" (or
        # empty text) as the whole opener. If the pre-synth text is degraded,
        # discard it (and its matching audio chunks, since they were
        # synthesized FROM that bad text) and fall through to the slow path,
        # which rebuilds real greeting content via _build_call_greeting.
        # Wrapped defensively so any failure here just falls through to the
        # existing pre-synth behaviour rather than breaking the greeting.
        try:
            if presynth_chunks and presynth_text and not _has_real_opener_content(
                presynth_text
            ):
                logger.warning(
                    "outbound_greeting_presynth_rejected call_id=%s text=%r "
                    "— falling back to real-time greeting build",
                    call_id[:12], presynth_text,
                )
                presynth_chunks = None
                presynth_text = None
        except Exception as _guard_exc:
            logger.debug(
                "opener-content guard failed for %s: %s", call_id[:12], _guard_exc
            )

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
            # Pick the greeting that matches the call direction so a
            # user-first call falling through to slow-path doesn't
            # accidentally read the outbound consent-first opener.
            # Falls back to "agent" framing when first_speaker is unset
            # (legacy code paths or non-telephony sessions).
            first_speaker = (
                getattr(voice_session, "_first_speaker", None) or "agent"
            )
            greeting = _build_call_greeting(session, first_speaker=first_speaker)

            # 2026-07-08: last-resort opener-content guard. If even the
            # real-time greeting builder produced empty/filler-only text
            # (e.g. a persona template misconfiguration), never let the
            # spoken opener be a bare "Hello?" — build a minimal branded
            # line from whatever agent/company name is already on the
            # session. Wrapped so any failure here falls through to the
            # original (possibly degraded) greeting rather than crashing.
            try:
                if not _has_real_opener_content(greeting):
                    from app.domain.services.telephony.config import (
                        _resolve_greeting_context,
                    )
                    agent_name, company = _resolve_greeting_context(session)
                    greeting = (
                        f"Hello, this is {agent_name} calling from {company}."
                    )
                    logger.warning(
                        "outbound_greeting_realtime_rejected call_id=%s "
                        "— using safe minimal branded opener",
                        call_id[:12],
                    )
            except Exception as _guard_exc:
                logger.debug(
                    "final opener-content guard failed for %s: %s",
                    call_id[:12], _guard_exc,
                )

            logger.info(
                "outbound_greeting_realtime call_id=%s first_speaker=%s text=%r",
                call_id[:12], first_speaker, greeting[:60],
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


async def prepare_pre_originate_greeting(
    pre_warm_session,
    effective_first_speaker: str,
) -> None:
    """Pre-synthesize the greeting during the ringing phase.

    Runs only when the agent will actually play audio on answer:
    * ``first_speaker == "agent"`` always pre-synths.
    * ``first_speaker == "user"`` pre-synths when EITHER the silence
      safety net is enabled (default after T1.3) OR instant-pickup mode
      is enabled (opt-in via ``TELEPHONY_USER_FIRST_GREET_ON_PICKUP``).

    On success, attaches ``_presynth_greeting_audio`` (list of PCM chunks)
    and ``_presynth_greeting_text`` to ``pre_warm_session`` so the
    on-answer handler in ``lifecycle._on_new_call`` can pump audio into
    the gateway with no TTS round-trip.

    Raises ``RuntimeError`` when pre-synth runs but produces zero audio
    chunks, so the caller can refuse to ring a cold pipeline.
    """
    # Both first-speaker modes always play a greeting:
    # * agent-first speaks immediately on pickup
    # * caller-first waits 2s then speaks the SAME greeting
    # So pre-synth is unconditional — the gate that used to skip it for
    # "silent caller-first" mode was removed when the silence handler
    # was retired.
    #
    # The greeting text is direction-aware: caller-first uses the inbound
    # receiver phrasing ("Hello, this is {agent} from {company} — how can
    # I help?") so the AI sounds like someone picking up the phone after
    # the callee said hello, rather than reading a cold-call opener.
    greeting_text = _build_call_greeting(
        pre_warm_session.call_session,
        first_speaker=effective_first_speaker,
    )
    chunks: list[bytes] = []
    # Carry the orphan byte across chunks. Int16 (s16le) providers like
    # ElevenLabs HTTP-stream PCM that can split a 2-byte sample across two
    # chunks (odd-length chunk). DROPPING that trailing byte (raw[:-1])
    # byte-shifts every following sample → white-noise buzz on the whole
    # greeting. Instead, hold the orphan and prepend it to the next chunk so
    # sample alignment is preserved. This is the same fix applied to the
    # conversation path in voice_pipeline/tts_playback.py — the pre-synth
    # greeting is a SEPARATE path that pumps chunks straight to the media
    # gateway, so it needed the fix independently.
    pending = b""
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
        if not raw:
            continue
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)
        raw = pending + bytes(raw)
        pending = b""
        if len(raw) % 2 != 0:
            pending = raw[-1:]
            raw = raw[:-1]
        if raw:
            chunks.append(raw)
    # A single trailing orphan byte at stream end has no partner sample —
    # it's a genuinely incomplete final sample (not a misalignment source),
    # so it is dropped rather than carried.

    if not chunks:
        raise RuntimeError(
            "pre_originate_greeting_empty: TTS returned 0 audio chunks"
        )

    pre_warm_session._presynth_greeting_audio = chunks
    pre_warm_session._presynth_greeting_text = greeting_text
    logger.info(
        "pre_originate_greeting_ready first_speaker=%s chunks=%d bytes=%d text=%r",
        effective_first_speaker,
        len(chunks), sum(len(c) for c in chunks), greeting_text[:60],
    )


async def warm_tts_inference_path(pre_warm_session) -> None:
    """Force the TTS voice model to load on the provider's inference worker.

    ``connect_for_call`` opens the WebSocket but most cloud TTS providers
    only load the voice model when they receive the first synthesize
    request. Without this, turn 0 pays full model-load latency
    (~2s on Google/ElevenLabs) — which is what user-first mode saw before
    this warmup existed (no greeting synth = no model load during ringing =
    cold first turn).

    We synthesize a real phrase, drain the chunks, throw the audio away.
    A single space proved too short on Google TTS — the provider appears
    to fast-path trivial inputs without fully priming the streaming voice
    pipeline. A real sentence ensures the model-load + streaming buffer
    pool are exercised end-to-end before turn 0 fires.
    """
    tts_config = pre_warm_session.config
    async for _chunk in pre_warm_session.tts_provider.stream_synthesize(
        text="Warming up the voice pipeline now.",
        voice_id=tts_config.voice_id if tts_config else "default",
        sample_rate=tts_config.tts_sample_rate if tts_config else 8000,
        call_id=pre_warm_session.call_id,
    ):
        pass  # discard
    logger.info(
        "tts_inference_warmed call_id=%s",
        pre_warm_session.call_id[:12],
    )


async def warm_llm_stream(pre_warm_session) -> None:
    """Drain a tiny LLM streaming generation to prime the inference path.

    ``llm_provider.warm_up()`` opens the HTTP/WS connection but does not
    run a real streaming generation. Most providers' first streaming
    request after warm_up still pays a one-time setup cost (KV-cache
    scheduling, inference-worker assignment). In agent-first mode the
    pre-synthesized greeting indirectly exercises this on the LLM side
    via the system-prompt cache lookup; user-first mode has no such
    cover and turn 0 pays the cold cost in full.

    Fire a minimal "hi" through ``stream_chat`` with ``max_tokens=2`` and
    drain whatever comes back. Discard the result. Both modes run this
    so pickup always lands on a fully-warmed LLM streaming path.
    """
    from app.domain.models.conversation import Message, MessageRole

    msgs = [Message(role=MessageRole.USER, content="hi")]
    try:
        async for _token in pre_warm_session.llm_provider.stream_chat(
            msgs,
            system_prompt="Reply with a single word only.",
            max_tokens=2,
        ):
            pass  # discard
        logger.info(
            "llm_stream_warmed call_id=%s",
            pre_warm_session.call_id[:12],
        )
    except Exception as exc:
        # Re-raise so the strict warmup gate refuses to ring rather than
        # letting the call originate with a half-warm LLM streaming path.
        raise RuntimeError(
            f"llm_stream_warmup_failed: {exc!r}"
        ) from exc
