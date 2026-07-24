"""
Compose a full-turn transcript from finalized segments plus a pending interim.

Pure and dependency-free, so it can be tested exhaustively without a socket.

WHY THIS EXISTS
---------------
Deepgram finalizes a turn in *segments*. Mid-turn, some segments are final and a
trailing fragment may still be interim. The composition used to be:

    base = " ".join(finals).strip()
    return base or last_interim[0].strip()

-- finals *or* the interim, never both. So when a caller says

    "john at example dot com"

and the turn ends while "dot com" is still interim, the finalized part wins and
the fragment is discarded. The captured address becomes ``john@example`` and the
lead is worthless.

Email addresses and phone numbers are CORE fields on this project: they must be
exactly right, and correctness beats latency. This is a data-integrity defect,
not a transcript-quality nicety.

THE TRAP
--------
Naive concatenation is not the fix. The interim can restate text that is already
final, and ``"example.com" + "com"`` yields ``"example.comcom"`` -- a wrong
address that looks plausible enough to survive a read-back. Overlap must be
de-duplicated, which is what this module does.

See ticket TKT-008.
"""

from __future__ import annotations

from typing import Iterable, Optional


def compose_turn_text(finals: Iterable[str], interim: Optional[str]) -> str:
    """
    Return the best full-turn text from finalized segments plus a pending interim.

    Rules, in order — the order matters, each guards the one after it:

    1. No interim -> the finalized text. No interim, nothing to lose.
    2. No finals -> the interim. Nothing finalized yet.
    3. The interim only restates the end of what is already final -> drop it.
       (A stale interim for a segment that has since finalized.)
    4. The interim restates the whole turn so far -> it *is* the turn; prefer it
       over the sum, which would duplicate the opening.
    5. The tail of the finals overlaps the head of the interim -> merge at the
       longest overlap, so "…example dot" + "dot com" is not "…dot dot com".
    6. Otherwise -> join with a single space.

    Comparisons are case-insensitive; the returned text keeps the original casing.
    """
    base = " ".join(s.strip() for s in finals if s and s.strip()).strip()
    tail = (interim or "").strip()

    if not tail:
        return base
    if not base:
        return tail

    base_l, tail_l = base.lower(), tail.lower()

    # (3) stale interim — already covered by the finalized text.
    if base_l.endswith(tail_l):
        return base

    # (4) the interim restates everything so far and then some.
    if tail_l.startswith(base_l):
        return tail

    # (5) longest word-level overlap between the end of `base` and the start of
    # `tail`. Word-level rather than character-level: speech text is tokenised by
    # words, and a character overlap would happily splice mid-word.
    base_words = base.split()
    tail_words = tail.split()
    for k in range(min(len(base_words), len(tail_words)), 0, -1):
        if [w.lower() for w in base_words[-k:]] == [w.lower() for w in tail_words[:k]]:
            return " ".join(base_words + tail_words[k:])

    # (6) genuinely new text.
    return f"{base} {tail}"
