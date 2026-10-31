"""Small shared filter for caller-authored call-control assertions.

An occurrence of a phrase is not enough: quoted, reported, hypothetical and
negated mentions must not authorize external call control. All close/DNC
detectors use this same filter; their phrase vocabularies remain separate.
"""
from __future__ import annotations

import re
from typing import Iterable, Pattern


_QUOTED = re.compile(r'"[^"\n]*"|(?<!\w)\'(?:[^\'\n]|(?<=\w)\'(?=\w))*\'(?!\w)')
_BOUNDARY = re.compile(r'[.!?;\n]|\b(?:but|however|actually|instead)\b', re.I)
_META = re.compile(
    r'\b(?:if|when|whether|suppose|imagine|what|why|how|explain|meaning|word|phrase|example|quote)\b|'
    r'\b(?:will|would|can|could)\s+(?:saying|the\s+word)\b|'
    r'\b(?:did|do|does)\s+(?:i|we|you|he|she|they)\s+(?:just\s+)?say\b', re.I)
_REPORTED = re.compile(
    r'\b(?:you|he|she|they|someone|the\s+(?:agent|caller|recording|customer))\b'
    r'[^.!?;]{0,45}\b(?:said|say|says|saying|told|asked|mentioned|wrote|reads?)\b|'
    r'\b(?:i|we)\s+(?:(?:just|already|previously)\s+)?(?:said|told|heard|read|wrote|asked)\b', re.I)
_NEGATED_PREFIX = re.compile(
    r"\b(?:don't|dont|do\s+not|didn't|did\s+not|haven't|have\s+not|won't|will\s+not|never)\s+"
    r"(?:(?:please|you|ever)\s+)*(?:(?:want|need|wish|intend|plan)\s+(?:you\s+)?to\s+|"
    r"(?:say|said|saying|ask|asked)\s+)?$|"
    r"\bnot\s+(?:(?:ready|going|trying|about)\s+to\s+(?:say\s+)?|"
    r"(?:asking|telling|requesting)\s+(?:you\s+)?to\s+|(?:say|said|saying)\s+)$|\bnot\s+$", re.I)
_META_SUFFIX = re.compile(r'^\s*(?:means?\b|is\s+(?:a\s+word|the\s+word|what\b)|was\s+what\b)', re.I)
_DIRECT_PREFIX = re.compile(
    r'^\s*(?:(?:yes|no|well|sorry|okay|ok|listen|look|to\s+be\s+clear)\b[\s,:]*)*$', re.I
)
_CONTINUE = re.compile(
    r"\b(?:don't|dont|do\s+not|won't|will\s+not|never)\s+(?:(?:you|please)\s+)*"
    r"(?:(?:want|need|wish|intend|plan)\s+(?:you\s+)?to\s+)?"
    r"(?:hang\s*up|end\s+(?:(?:this|the)\s+)?call|say\s+(?:goodbye|bye))\b|"
    r"\b(?:not\s+(?:ready\s+to\s+(?:hang\s*up|end)|done|finished)|haven't\s+finished|have\s+not\s+finished)\b|"
    r"\b(?:wait|hold\s+on|hang\s+on|stay\s+on\s+(?:the\s+)?line|keep\s+(?:this\s+|the\s+)?call\s+(?:open|going))\b|"
    r"\b(?:(?:i|we)\s+(?:still\s+)?(?:have\s+(?:another|one\s+more|a)\s+question|need\s+(?:your\s+)?help)|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:help|explain|tell|show|send|email|check|repeat|clarify|answer)|"
    r"(?:please\s+)?(?:tell\s+me|explain\s+the|answer\s+my))\b|"
    r"\b(?:what|why|how|where|when)\b", re.I)


def phrase_pattern(phrases: Iterable[str]) -> Pattern[str]:
    contractions = {'dont': r"don'?t", 'im': r"i'?m", 'ive': r"i'?ve"}
    return re.compile(r'\b(?:' + '|'.join(
        r'\s+'.join(contractions.get(word, re.escape(word)) for word in phrase.split())
        for phrase in phrases
    ) + r')\b', re.I)


def _text(text: str | None) -> str:
    value = str(text or '').replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    # Preserve offsets and contraction apostrophes, while removing quoted words.
    value = _QUOTED.sub(lambda match: ' ' * len(match[0]), value)
    return value.replace(',', ' ').replace('—', ' ').replace('–', ' ')


def assertion_matches(
    text: str | None, pattern: Pattern[str], *, require_direct: bool = False
) -> list[re.Match]:
    """Return only independently asserted matches, with original-text offsets."""
    value = _text(text)
    accepted = []
    for match in pattern.finditer(value):
        previous = list(_BOUNDARY.finditer(value, 0, match.start()))
        start = previous[-1].end() if previous else 0
        following = _BOUNDARY.search(value, match.end())
        end = following.start() if following else len(value)
        prefix, suffix = value[start:match.start()], value[match.end():end]
        if (_META.search(prefix) or _REPORTED.search(prefix)
                or _NEGATED_PREFIX.search(prefix) or _META_SUFFIX.search(suffix)):
            continue
        # Opt-in for first-person factual state. Embedded claims such as
        # 'the script says I am ...' are not the caller's own assertion. The
        # default call-control policy is intentionally unchanged.
        if require_direct and not _DIRECT_PREFIX.fullmatch(prefix):
            continue
        accepted.append(match)
    return accepted


def continuation_after(text: str | None, position: int = -1) -> bool:
    """A later request to stay/help overrides an earlier close in this turn."""
    return any(match.start() > position for match in assertion_matches(text, _CONTINUE))


def last_asserted_position(text: str | None, pattern: Pattern[str]) -> int:
    matches = assertion_matches(text, pattern)
    return matches[-1].start() if matches else -1
