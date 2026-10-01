"""Sentence-boundary detection for streaming LLM output.

Pure, stateless helpers extracted verbatim from VoicePipelineService
(roadmap item 2). No I/O or session state — they run once per streamed
LLM chunk on the hot path, so they stay plain functions.

``find_sentence_end`` lets TTS start the moment a sentence (or, for long
openers, a clause) is complete, instead of waiting for the LLM's
inter-token stall guard to expire — shaving perceived latency.
"""
from __future__ import annotations

from collections.abc import Iterable

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


def _is_missing_space_boundary(text: str, index: int) -> bool:
    """True when the terminator at ``index`` ends a sentence with no space after.

    The model writes turn boundaries with no separator - "...best option?Yes."
    - and every splitter here required whitespace after the terminator, so a
    whole fabricated exchange counted as one sentence and neither the stream
    nor the cap could break it (call c01404ba, 2026-09-22).

    '?' and '!' are unambiguous: no English construction puts a letter hard
    against them mid-sentence. A '.' is a boundary only before an UPPERCASE
    letter, and only when the abbreviation guard agrees, so "Mr.Smith",
    "e.g.Something", initials and decimals are left alone.
    """
    nxt = text[index + 1] if index + 1 < len(text) else ""
    if not nxt.isalpha():
        return False
    if text[index] in "!?":
        return True
    if not nxt.isupper():
        return False
    if not is_terminal_period_boundary(text, index):
        return False
    prefix = text[:index].rstrip()
    token = prefix.rsplit(maxsplit=1)[-1] if prefix else ""
    return "." not in token


def _period_has_address_context(text: str, index: int, known_hosts: tuple[str, ...]) -> bool:
    """Disambiguate ``.Upper`` only with a URL marker or a factual host.

    A bare, unknown ``word.Upper`` is indistinguishable from the model's
    fabricated turn boundary. Preserve that existing defense, without a TLD
    catalog or a general URI parser. Prefix matching keeps a host together
    while its uppercase suffix is still arriving; the complete address must
    still pass the independent grounding check before speech.
    """
    start = index
    while start and not text[start - 1].isspace() and text[start - 1] not in "\"'“”‘’()[]{}<>,;":
        start -= 1
    prefix = text[start:index + 2].casefold()
    if prefix.startswith(("http://", "https://", "www.")):
        return True
    return any(
        host.startswith(prefix)
        or prefix.startswith(host + ".")
        or prefix.startswith(host + "/")
        for host in known_hosts
    )


def find_sentence_end(
    text: str, allow_clause: bool = False, *, known_hosts: Iterable[str] = (),
) -> int:
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

    known_hosts:
        Factual URL hosts, refreshed from current knowledge evidence by the
        caller. Together with explicit URL markers, these prevent an uppercase
        address suffix from being mistaken for a fabricated caller turn.
        Unknown bare ``word.Upper`` keeps the existing ambiguity defense.
    """
    protected_hosts = tuple(str(host).casefold().strip(".") for host in known_hosts or () if host)
    clause_candidate = -1
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "!?":
            if i + 1 == len(text) or (i + 1 < len(text) and text[i + 1] == " "):
                return i
            if _is_missing_space_boundary(text, i):
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
            elif (
                i + 1 == len(text)
                # "1." / "£11." at the end of what has arrived may be half of
                # a decimal: wait for the next token instead of speaking
                # "one." and "five percent" (or "£11" and "99") separately.
                and not (i > 0 and text[i - 1].isdigit())
                and is_terminal_period_boundary(text, i)
            ):
                return i
            elif _is_missing_space_boundary(text, i) and not _period_has_address_context(
                text, i, protected_hosts,
            ):
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
