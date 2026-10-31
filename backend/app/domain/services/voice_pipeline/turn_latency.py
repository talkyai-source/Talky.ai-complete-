"""Per-turn reply time, from when the caller actually stopped talking (2026-10-20).

The turn's own clock (``LatencyMetrics.speech_end_time``) starts once the
end-of-turn decision is made, so the time spent deciding the caller had
finished was invisible. Like Pipecat's UserBotLatencyObserver, the reply time
is back-dated to the real end of speech (the last voiced frame, see
``audio_ingest.note_voice_activity``) and split into parts that add up to it.
"""
from __future__ import annotations

from typing import Optional

# An end-of-turn wait outside this range is not this turn's: a voiced frame
# after the decision is caller noise during the reply, and a very old one
# predates the utterance.
_MAX_END_OF_TURN_S = 10.0


def _ms(start: Optional[float], end: Optional[float]) -> Optional[int]:
    if start is None or end is None or end < start:
        return None
    return round((end - start) * 1000)


def turn_latency_report(metrics, caller_voice_last_at: Optional[float]) -> Optional[dict]:
    """``reply_ms`` is end of speech to first agent audio; the other values are
    its parts (``end_of_turn_ms`` waiting to be sure the caller had finished,
    ``ai_first_word_ms``, ``voice_first_audio_ms``, ``other_ms`` for the rest).
    A part that was not measured is None. None until the agent spoke."""
    if metrics is None:
        return None
    response = _ms(metrics.speech_end_time, metrics.response_start_time)
    if response is None:
        return None
    end_of_turn = None
    if caller_voice_last_at is not None and metrics.speech_end_time is not None:
        wait = metrics.speech_end_time - caller_voice_last_at
        if 0 <= wait <= _MAX_END_OF_TURN_S:
            end_of_turn = round(wait * 1000)
    ai_first_word = _ms(metrics.llm_start_time, metrics.llm_first_token_time)
    voice_first_audio = _ms(metrics.tts_start_time, metrics.tts_first_chunk_time)
    other = max(0, response - (ai_first_word or 0) - (voice_first_audio or 0))
    return {
        "reply_ms": response + (end_of_turn or 0),
        "end_of_turn_ms": end_of_turn,
        "ai_first_word_ms": ai_first_word,
        "voice_first_audio_ms": voice_first_audio,
        "other_ms": other,
    }
