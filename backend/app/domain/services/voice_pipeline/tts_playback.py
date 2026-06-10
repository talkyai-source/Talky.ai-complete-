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
            # Orphan byte carried across chunk boundaries to keep Int16 samples
            # aligned when a provider splits a sample between two chunks.
            pending_byte = b""
            # One retry if the provider yields NO audio within the inter-chunk
            # timeout (a brief stall before the sentence starts). Safe — nothing
            # has played yet, so no duplicate audio.
            stall_retried = False
            while True:
                try:
                    audio_chunk = await asyncio.wait_for(
                        _tts_iter.__anext__(),
                        timeout=_TTS_INTER_CHUNK_TIMEOUT_S,
                    )
                except StopAsyncIteration:
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
                        pending_byte = b""
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
                # Int16 PCM = 2 bytes/sample. A provider streaming raw PCM can
                # split a sample ACROSS chunk boundaries: a chunk arrives odd-
                # length with the sample's other byte in the NEXT chunk. CARRY
                # that orphan byte forward (prepend it to the next chunk) instead
                # of dropping it — dropping byte-shifts every following sample
                # (high/low swapped) → loud buzz. This was the ElevenLabs
                # eleven_v3 buzz: frequent odd chunks, worse on long RAG-backed
                # answers (more chunks → more split samples).
                if not isinstance(raw, (bytes, bytearray)):
                    raw = bytes(raw)
                if pending_byte:
                    raw = pending_byte + raw
                    pending_byte = b""
                if len(raw) % 2 != 0:
                    pending_byte = raw[-1:]
                    raw = raw[:-1]
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
                    if websocket:
                        try:
                            await websocket.send_json({"type": "tts_interrupted", "reason": "barge_in"})
                        except Exception as _exc:
                            logger.debug("tts_interrupted post-send WS send failed: %s", _exc)
                    break
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
                    silent_reason = "provider_empty_stream"
                if silent_reason is not None:
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
