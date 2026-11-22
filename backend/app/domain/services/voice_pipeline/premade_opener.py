"""The caller-first opener, played from audio made while the phone rings.

On an outbound call where the person speaks first, the agent's opening line (the
"I am an AI" words and, where the platform's recording notice is owed,
", on a recorded line") used to be synthesized LIVE after the person said hello:
a median 756 ms of silence on the 213 calls of 20 November 2026. The audio is now
made during the pre-originate step and handed to the SAME speaking function as
live speech (TtsPlayback.synthesize_and_send, keyword ``premade_chunks``), where
it replaces ONLY the provider stream. The begin/finish playback receipt (the
recording notice's proof), the barge-in checks, the pacing, the recording copy
and the delivery status are therefore the same code as for live speech. The
agent-first fast path that pumps chunks directly (it gets no receipt) is never
used for this.

The stored audio is used only when the text, the spoken form after
pronunciations, the voice id, the sample rate and the provider object all match,
and at most once; in every other case the live path runs unchanged.

Kill switch: ``OPENER_PREMADE_ENABLED`` (default on), read once per call at
pre-originate; off means no audio is stored and everything is live as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional, Sequence, Tuple

from app.domain.services.voice_pipeline.spoken_pronunciations import apply_pronunciations

logger = logging.getLogger(__name__)

# The notice line is made in the background while the phone rings; nothing waits
# for it. 8 s = about 2.5x the ~3 s the production voice (ElevenLabs eleven_v4)
# needed for the shorter plain greeting on 20 Nov, so the longer line normally
# finishes, while a stuck provider is released well before a typical ring ends.
OPENER_PREMADE_BACKGROUND_TIMEOUT_S = 8.0
_STORE_ATTR = "_premade_opener_lines"
_PENDING_ATTR = "_premade_opener_pending"
_CONSUMED_ATTR = "_premade_opener_consumed"


@dataclass(frozen=True)
class PremadeLine:
    kind: str  # "opener" | "notice_opener"
    text: str
    spoken_form: str  # exactly what was sent to the provider
    chunks: Tuple[bytes, ...]
    voice_id: Any
    sample_rate: Any
    provider: Any


def premade_enabled() -> bool:
    return os.getenv("OPENER_PREMADE_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


async def iter_premade(chunks: Sequence[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _log_text(text: str) -> str:
    from app.domain.services.telephony.config import opener_for_log

    return opener_for_log(text)[:80]


async def _collect(provider, spoken: str, voice_id, sample_rate, call_id) -> list:
    """The same loop as agent_first.prepare_pre_originate_greeting (2-byte carry)."""
    chunks: list = []
    pending = b""
    async for audio_chunk in provider.stream_synthesize(
        text=spoken, voice_id=voice_id, sample_rate=sample_rate, call_id=call_id,
    ):
        raw = audio_chunk.data if hasattr(audio_chunk, "data") else audio_chunk
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
    return chunks


async def _make_notice_line(pre_warm_session, store, consumed, notice_text, spoken,
                            provider, voice_id, sample_rate, call_id) -> None:
    """Background task: make the notice opener's audio and store it. Never raises.

    A result that arrives after the opener was already spoken live (its text is in
    ``consumed``) is dropped, never stored. A provider closed under the task by a
    call that ended early is one calm INFO line, not a fault.
    """
    started = time.monotonic()
    try:
        chunks = await asyncio.wait_for(
            _collect(provider, spoken, voice_id, sample_rate,
                     getattr(pre_warm_session, "call_id", None)),
            timeout=OPENER_PREMADE_BACKGROUND_TIMEOUT_S,
        )
    except asyncio.CancelledError:
        logger.debug("opener_premade_cancelled call_id=%s", call_id)
        raise
    except asyncio.TimeoutError:
        logger.info("opener_premade_failed call_id=%s kind=notice_opener reason=timeout", call_id)
        return
    except Exception as exc:  # noqa: BLE001 - a background task never raises
        logger.info("opener_premade_failed call_id=%s kind=notice_opener reason=error:%s",
                    call_id, type(exc).__name__)
        return
    finally:
        getattr(pre_warm_session, _PENDING_ATTR, {}).pop(notice_text, None)
    if not chunks:
        logger.info("opener_premade_failed call_id=%s kind=notice_opener reason=empty", call_id)
        return
    if notice_text in consumed:
        logger.info("opener_premade_dropped call_id=%s kind=notice_opener reason=late_after_live", call_id)
        return
    store[notice_text] = PremadeLine(
        "notice_opener", notice_text, spoken, tuple(chunks), voice_id, sample_rate, provider,
    )
    logger.info(
        "opener_premade_ready call_id=%s kind=notice_opener chunks=%d bytes=%d synth_ms=%d text=%r",
        call_id, len(chunks), sum(len(c) for c in chunks),
        int((time.monotonic() - started) * 1000), _log_text(notice_text),
    )


async def premake_caller_first_lines(
    pre_warm_session, effective_first_speaker, greeting_text, greeting_chunks,
) -> None:
    """Register the plain opener's audio and START the notice opener's synthesis.

    Returns at once: the phone rings no later than without this feature. Never fatal.
    """
    if effective_first_speaker != "user":
        return
    notice_text = getattr(pre_warm_session, "_opener_with_notice_text", None)
    if not notice_text:
        return  # inbound: no pre-made opener audio
    call_id = str(getattr(pre_warm_session, "call_id", ""))[:12]
    enabled = premade_enabled()  # read once per call
    logger.info("opener_premade_wiring call_id=%s enabled=%s", call_id, enabled)
    if not enabled:
        logger.info("opener_premade_off call_id=%s", call_id)
        return
    try:
        call_session = pre_warm_session.call_session
        provider = pre_warm_session.tts_provider
        config = getattr(pre_warm_session, "config", None)
        voice_id = config.voice_id if config else "default"
        sample_rate = config.tts_sample_rate if config else 8000
        store: Dict[str, PremadeLine] = {}
        pending: Dict[str, Any] = {}
        consumed: set = set()
        setattr(pre_warm_session, _STORE_ATTR, store)
        setattr(pre_warm_session, _PENDING_ATTR, pending)
        setattr(pre_warm_session, _CONSUMED_ATTR, consumed)
        store[greeting_text] = PremadeLine(
            "opener", greeting_text, greeting_text, tuple(greeting_chunks),
            voice_id, sample_rate, provider,
        )
        if notice_text == greeting_text:
            return
        spoken = apply_pronunciations(notice_text, getattr(call_session, "tts_pronunciations", None))
        task = asyncio.ensure_future(_make_notice_line(
            pre_warm_session, store, consumed, notice_text, spoken,
            provider, voice_id, sample_rate, call_id))
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # never "not retrieved"
        pending[notice_text] = task
        setattr(pre_warm_session, "_premade_opener_task", task)  # keeps it alive mid-flight
    except Exception as exc:  # noqa: BLE001 - the call must ring whatever happens here
        logger.warning("opener_premade_failed call_id=%s kind=any reason=error:%s",
                       call_id, type(exc).__name__)


def premade_kwargs(voice_session, session, text) -> dict:
    """``{"premade_chunks": [...]}`` when a stored line matches in every respect, else ``{}``.

    Pops the line (used at most once). Never raises.
    """
    try:
        store = getattr(voice_session, _STORE_ATTR, None)
        if not isinstance(store, dict):
            logger.debug("opener_premade_no_store")
            return {}
        call_id = str(getattr(session, "call_id", ""))[:12]
        line = store.pop(text, None)
        left = ",".join(sorted(v.kind for v in store.values())) or "-"
        # Whatever happens next, this text is spoken now: a result that arrives
        # later is dropped by the background task and can never serve a later sentence.
        consumed = getattr(voice_session, _CONSUMED_ATTR, None)
        if isinstance(consumed, set):
            consumed.add(text)
        if line is None:
            pending = getattr(voice_session, _PENDING_ATTR, None)
            reason = "not_ready_yet" if isinstance(pending, dict) and text in pending else "no_matching_text"
            logger.info("opener_premade_skipped call_id=%s kind=%s reason=%s stored=%s",
                        call_id, "notice_opener" if reason == "not_ready_yet" else "other", reason, left)
            return {}
        pipeline = getattr(voice_session, "pipeline", None)
        reason = None
        if line.spoken_form != apply_pronunciations(text, getattr(session, "tts_pronunciations", None)):
            reason = "spoken_form_differs"
        elif line.voice_id != getattr(session, "voice_id", None):
            reason = "voice_differs"
        elif line.sample_rate != getattr(pipeline, "tts_sample_rate", None):
            reason = "rate_differs"
        elif line.provider is not getattr(pipeline, "tts_provider", None):
            reason = "provider_differs"
        if reason:
            logger.info("opener_premade_skipped call_id=%s kind=%s reason=%s stored=%s",
                        call_id, line.kind, reason, left)
            return {}
        logger.info("opener_premade_used call_id=%s kind=%s chunks=%d",
                    call_id, line.kind, len(line.chunks))
        return {"premade_chunks": list(line.chunks)}
    except Exception as exc:  # noqa: BLE001 - fall back to live speech
        logger.debug("opener_premade_lookup_failed err=%s", exc)
        return {}
