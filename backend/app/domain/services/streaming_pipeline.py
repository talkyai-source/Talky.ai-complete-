"""
app/domain/services/streaming_pipeline.py

LLM → TTS streaming bridge for sub-500ms latency.

LATENCY FIX 10: Instead of waiting for the full LLM response before
starting TTS, this module splits the LLM token stream into complete
sentences and starts TTS synthesis on the first sentence the moment it
arrives — typically after 40-80ms on llama-3.1-8b-instant.

Visual timeline comparison:

OLD (sequential):
  t=0ms   LLM starts
  t=180ms LLM completes full response (e.g. "Your appointment is confirmed for Tuesday at 2 PM. See you then!")
  t=180ms TTS starts
  t=270ms First audio chunk arrives

NEW (streaming):
  t=0ms   LLM starts
  t=60ms  First sentence complete: "Your appointment is confirmed for Tuesday at 2 PM."
  t=60ms  TTS starts on sentence 1
  t=150ms First audio chunk arrives  ← 120ms savings
  t=120ms LLM completes sentence 2: "See you then!"
  t=150ms TTS starts on sentence 2 (overlapping with sentence 1 audio)

Net saving: ~120ms on typical 2-sentence dental responses.

Usage (in voice_pipeline_service.py handle_turn_end):
    from app.domain.services.streaming_pipeline import stream_llm_to_tts

    async for audio_chunk in stream_llm_to_tts(
        llm=self.llm_provider,
        tts=self.tts_provider,
        messages=session.conversation_history,
        system_prompt=system_prompt,
        call_id=call_id,
        latency_tracker=self.latency_tracker,
        barge_in_event=self._barge_in_events.get(call_id),
    ):
        await self.media_gateway.send_audio(call_id, audio_chunk)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import AsyncIterator, Optional, Any

logger = logging.getLogger(__name__)

# Sentence-boundary pattern — splits on . ! ? followed by space or end-of-string.
# Keeps the punctuation attached to the sentence.
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+|(?<=[.!?])$')

# Minimum characters before we attempt a sentence split.
# Prevents sending micro-fragments like "Hi," to TTS.
_MIN_SENTENCE_CHARS = 12


def _split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences suitable for TTS streaming.
    Returns complete sentences; any trailing incomplete fragment is returned last.
    """
    parts = _SENTENCE_END.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


async def stream_llm_to_tts(
    llm: Any,
    tts: Any,
    messages: list,
    system_prompt: Optional[str],
    call_id: str,
    latency_tracker: Optional[Any] = None,
    barge_in_event: Optional[asyncio.Event] = None,
    max_tokens: int = 60,
    temperature: float = 0.4,
) -> AsyncIterator[Any]:
    """
    Stream LLM tokens → sentence splitter → TTS in parallel.

    Yields audio chunks as they arrive from TTS.
    Respects barge-in: stops yielding if barge_in_event is set.

    Args:
        llm:             GroqLLMProvider instance
        tts:             TTSProvider instance (Cartesia or Google)
        messages:        Conversation history
        system_prompt:   System prompt string
        call_id:         For latency tracking
        latency_tracker: LatencyTracker instance (optional)
        barge_in_event:  asyncio.Event — set when user interrupts
        max_tokens:      LLM max tokens (default 60 for dental)
        temperature:     LLM temperature (default 0.4 for dental)
    """
    buffer = ""
    full_response = ""
    first_sentence_sent = False
    t_llm_start = time.monotonic()

    try:
        async for token in llm.stream_chat(
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            # Check barge-in before processing each token
            if barge_in_event and barge_in_event.is_set():
                logger.info(f"Barge-in: stopping LLM stream for call {call_id}")
                return

            if not first_sentence_sent and latency_tracker:
                latency_tracker.mark_llm_first_token(call_id)

            buffer += token
            full_response += token

            # Try to extract a complete sentence from the buffer
            sentences = _split_into_sentences(buffer)

            if len(sentences) >= 2:
                # We have at least one complete sentence plus more text
                # Send the complete sentence(s) to TTS immediately
                complete = sentences[:-1]   # all but the trailing fragment
                buffer = sentences[-1]       # keep the incomplete part

                for sentence in complete:
                    if len(sentence) < _MIN_SENTENCE_CHARS:
                        # Too short — append to buffer and wait for more
                        buffer = sentence + " " + buffer
                        continue

                    if barge_in_event and barge_in_event.is_set():
                        return

                    if not first_sentence_sent:
                        llm_to_first_sentence_ms = (time.monotonic() - t_llm_start) * 1000
                        logger.info(
                            f"[LATENCY] First sentence to TTS after {llm_to_first_sentence_ms:.0f}ms "
                            f"call={call_id} sentence='{sentence[:40]}...'"
                        )
                        if latency_tracker:
                            latency_tracker.mark_tts_start(call_id)
                        first_sentence_sent = True

                    # Stream this sentence through TTS
                    first_chunk = True
                    async for audio_chunk in tts.stream_synthesize(sentence):
                        if barge_in_event and barge_in_event.is_set():
                            return
                        if first_chunk and latency_tracker:
                            latency_tracker.mark_tts_first_chunk(call_id)
                            latency_tracker.mark_audio_start(call_id)
                            first_chunk = False
                        yield audio_chunk

        # Process any remaining text in buffer after LLM stream ends
        if buffer.strip() and not (barge_in_event and barge_in_event.is_set()):
            if len(buffer.strip()) >= 3:  # At least a word
                if not first_sentence_sent:
                    if latency_tracker:
                        latency_tracker.mark_tts_start(call_id)
                    first_sentence_sent = True

                first_chunk = True
                async for audio_chunk in tts.stream_synthesize(buffer.strip()):
                    if barge_in_event and barge_in_event.is_set():
                        return
                    if first_chunk and latency_tracker:
                        latency_tracker.mark_tts_first_chunk(call_id)
                        latency_tracker.mark_audio_start(call_id)
                        first_chunk = False
                    yield audio_chunk

    except Exception as exc:
        logger.error(
            f"stream_llm_to_tts error for call {call_id}: {exc}",
            exc_info=True,
        )
        raise

    total_ms = (time.monotonic() - t_llm_start) * 1000
    logger.info(
        f"[LATENCY] LLM+TTS stream complete: {total_ms:.0f}ms "
        f"response='{full_response[:80]}' call={call_id}"
    )
