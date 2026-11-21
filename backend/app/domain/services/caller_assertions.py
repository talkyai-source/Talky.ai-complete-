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
    r'\b(?:i|we)\s+(?:(?:just|already|previously)\s+)?(?:heard|read|wrote)\b|'
    # "I told you, ..." / "I said ..." to the agent is the caller's own request,
    # not reported speech: it stays an assertion when nothing but "you"/"to" follows.
    r'\b(?:i|we)\s+(?:(?:just|already|previously)\s+)?(?:said|told|asked)\b'
    r'(?!\s+(?:you\s+)?(?:to\s+)?[\s,:]*$)', re.I)
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


_CONTINUE_REQUEST = re.compile(_CONTINUE.pattern.replace(r'|\b(?:what|why|how|where|when)\b', ''), re.I)
_OPT_OUT_CONNECTOR = re.compile(r'\b(?:but|first|also|before|however|actually|instead)\b', re.I)
_OPT_OUT_QUESTION = re.compile(r'\b(?:what|how|where|when)\b', re.I)
_HOSTILE_QUESTION = re.compile(
    r"^\s*(?:many\s+times|part\b|the\b|is\s+wrong|else\b|do\s+i\s+have\s+to)", re.I)


def continues_after_opt_out(text: str | None, opt_out_position: int = -1) -> bool:
    """After an opt-out, only a request for help that follows it keeps the call open.

    Request vocabulary (_CONTINUE without bare question words) after the opt-out
    counts; so does a what/how/where/when question that follows a connector
    ("but first, what are your hours?") unless it is a hostile idiom ("how many
    times", "what part", "what the ..."). "Why" never does, and "and" is not a
    connector ("stop calling me and what's wrong with you people?" closes).
    """
    if any(m.start() > opt_out_position for m in assertion_matches(text, _CONTINUE_REQUEST)):
        return True
    value = _text(text)
    connector = _OPT_OUT_CONNECTOR.search(value, opt_out_position + 1)
    if connector is None:
        return False
    for m in assertion_matches(text, _OPT_OUT_QUESTION):
        if m.start() > connector.start() and not _HOSTILE_QUESTION.match(value[m.end():]):
            return True
    return False


_COURTESY = re.compile(
    r"(?:(?:okay|ok|right|fine|thanks(?: a lot| very much| so much)?|thank you(?: very much| so much)?|"
    r"cheers|great|lovely|perfect|no|no thanks|nothing else|that's all|that's it|that is everything|"
    r"that is all|alright|sure|got it|understood|bye|goodbye)\s+)+")
_COURTESY_MAX_WORDS = 8
_COURTESY_MAX_CHARS = 100


def courtesy_only(text: str | None) -> bool:
    """The whole turn is only acknowledgement or courtesy words (at most 8).

    The input is bounded before the regex runs, so a long turn cannot make the
    overlapping alternatives backtrack.
    """
    raw = str(text or "")
    if len(raw) > _COURTESY_MAX_CHARS:
        return False
    words = re.findall(r"[a-z']+", raw.lower().replace("\u2019", "'"))
    if not words or len(words) > _COURTESY_MAX_WORDS:
        return False
    return _COURTESY.fullmatch(" ".join(words) + " ") is not None


def last_asserted_position(text: str | None, pattern: Pattern[str]) -> int:
    matches = assertion_matches(text, pattern)
    return matches[-1].start() if matches else -1
