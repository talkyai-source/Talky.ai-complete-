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

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


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
    """Apply the same rule to the assembled reply text kept in history."""
    if not max_sentences or not full_text:
        return full_text
    parts = _SENTENCE_SPLIT.split(full_text.strip())
    keep = parts[:max_sentences]
    if len(parts) > max_sentences and parts[max_sentences].rstrip().endswith("?"):
        keep = parts[: max_sentences + 1]
    return " ".join(keep)
