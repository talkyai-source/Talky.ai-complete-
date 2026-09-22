"""The per-turn sentence cap, with one rule: never cut right before the question.

The prompt asks for the fewest sentences that answer and ONE question at the
end; the cap (``agent_config.response_max_sentences``, 3 on telephony) is the
latency backstop for a model that ignores that. Until 2026-09-02 the two
fought: a reply of three statements plus the question lost the question, and
the caller heard three statements and silence — the dead-end turn everything
else in the prompt exists to prevent.

So the ceiling stays, and it may not fall between a statement and the question
that immediately follows it. Exactly one sentence of grace, only if it is a
question, only when it is the very next sentence. Everything past that is still
dropped.
"""
from __future__ import annotations

import re
from typing import Optional

from app.domain.services.voice_pipeline.sentence_segmentation import (
    find_sentence_end,
)

# Kept for callers/tests that import it. The cap itself now walks the text with
# find_sentence_end so the ceiling counts the SAME boundaries the streamer
# speaks at -- two different notions of "sentence" is how a fabricated
# five-turn exchange counted as one (call c01404ba, 2026-09-22).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentence_spans(text: str) -> list:
    """Offsets at which each sentence ends, using the streamer's own rule."""
    spans, pos = [], 0
    while pos < len(text):
        idx = find_sentence_end(text[pos:])
        if idx < 0:
            spans.append(len(text))
            break
        pos += idx + 1
        spans.append(pos)
    return spans


def _next_sentence_is_question(buf: str) -> bool:
    """True when the buffered text's first complete sentence ends in '?'.

    A buffer without a terminator yet ("What would") is NOT a question — we
    do not speculate mid-stream; the tail flush re-asks once the text is in.
    """
    text = (buf or "").lstrip()
    if not text:
        return False
    for i, ch in enumerate(text):
        if ch in ".!?":
            # ellipsis is not a terminator
            if ch == "." and i + 1 < len(text) and text[i + 1] == ".":
                continue
            return ch == "?"
    return False


def cap_allows_another(
    sentences_done: int,
    max_sentences: Optional[int],
    buf: str,
    *,
    grace_used: bool,
) -> bool:
    """May one more sentence be spoken this turn?"""
    if not max_sentences:
        return True
    if sentences_done < max_sentences:
        return True
    if grace_used:
        return False
    return _next_sentence_is_question(buf)


def truncate_to_cap(full_text: str, max_sentences: Optional[int]) -> str:
    """Apply the same rule to the assembled reply text kept in history.

    Slices the original string rather than splitting and rejoining, so text
    that is kept is returned byte for byte -- the old " ".join collapsed
    newlines and runs of spaces inside a reply it was not truncating at all.
    """
    if not max_sentences or not full_text:
        return full_text
    text = full_text.strip()
    spans = _sentence_spans(text)
    if len(spans) <= max_sentences:
        return text
    cut = spans[max_sentences - 1]
    tail = text[cut:].lstrip()
    nxt = spans[max_sentences] - cut - (len(text[cut:]) - len(tail))
    if tail[:nxt].rstrip().endswith("?"):
        cut = spans[max_sentences]
    return text[:cut].rstrip()
