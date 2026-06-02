"""Sentence-boundary detection for streaming LLM output.

Pure, stateless helpers extracted verbatim from VoicePipelineService
(roadmap item 2). No I/O or session state — they run once per streamed
LLM chunk on the hot path, so they stay plain functions.

``find_sentence_end`` lets TTS start the moment a sentence (or, for long
openers, a clause) is complete, instead of waiting for the LLM's
inter-token stall guard to expire — shaving perceived latency.
"""
from __future__ import annotations

# Coordinating conjunctions that mark a clause boundary right after a comma.
# Trailing space avoids matching "and" inside a word.
CLAUSE_CONJUNCTIONS = ("and ", "but ", "so ", "or ", "yet ", "nor ")

# Tokens whose trailing period is an abbreviation, not a sentence terminator.
COMMON_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "e.g",
    "i.e",
}


def is_terminal_period_boundary(text: str, index: int) -> bool:
    """Return False for common abbreviation/initial periods at buffer end."""
    prefix = text[:index].rstrip()
    if not prefix:
        return True
    token = prefix.rsplit(maxsplit=1)[-1].strip("\"'([{")
    token_lower = token.lower()
    if token_lower in COMMON_ABBREVIATIONS:
        return False
    if len(token) == 1 and token.isalpha() and token.isupper():
        return False
    return True


def find_sentence_end(text: str, allow_clause: bool = False) -> int:
    """
    Return the index of the first sentence-ending character.

    Streaming LLM chunks often end exactly at punctuation ("Hello.")
    before a following space token arrives. Treat that terminal punctuation
    as a boundary so TTS can start immediately instead of waiting for the
    Groq inter-token stall guard to expire.

    Skips ellipsis (...) to avoid splitting mid-thought pauses.

    allow_clause (default False):
        When True **and** len(text) >= 80, also match a comma+conjunction
        boundary (", and", ", but", etc.) that occurs after at least 40
        characters.  This fires TTS on the first clause of a long opening
        sentence instead of waiting for the full sentence terminator,
        cutting perceived latency by 100-250ms on verbose first responses.

        Only activates when the buffer is long enough that we know we are
        stuck waiting — short responses still flush on hard punctuation.
    """
    clause_candidate = -1
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "!?":
            if i + 1 == len(text) or (i + 1 < len(text) and text[i + 1] == " "):
                return i
        elif ch == ".":
            # Skip ellipsis: advance past ALL consecutive dots so the last
            # dot of "..." is not mistaken for a sentence terminator.
            if i + 1 < len(text) and text[i + 1] == ".":
                while i + 1 < len(text) and text[i + 1] == ".":
                    i += 1
                # After the ellipsis just continue scanning — don't return.
            elif i + 1 < len(text) and text[i + 1] == " ":
                return i
            elif i + 1 == len(text) and is_terminal_period_boundary(text, i):
                return i
        elif (
            allow_clause
            and ch == ","
            and i >= 40                          # enough text before the comma
            and i + 2 < len(text)
            and text[i + 1] == " "
            and clause_candidate < 0             # keep the earliest one
        ):
            rest = text[i + 2:]
            if any(rest.startswith(conj) for conj in CLAUSE_CONJUNCTIONS):
                clause_candidate = i
        i += 1

    # Return clause boundary only when no sentence boundary was found AND
    # the total buffer is long enough to justify an early flush.
    if allow_clause and clause_candidate >= 0 and len(text) >= 80:
        return clause_candidate
    return -1
