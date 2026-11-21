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


# After an opt-out the default is to close. The call stays open only for a tight,
# high-precision request; a hostile or rhetorical form in the same sentence
# overrides it. When unsure: close.
_OPT_OUT_WINDOW = 1000  # newest characters of a turn these decisions look at
_REQUEST = re.compile(
    r"\b(?:what|when|where|how|which|who)\s+(?:is|are|was|were|do|does|did|can|could|will|would|should|"
    r"much|many|long|time|about)\b|"
    r"\b(?:what|where|when|how)'s\b|"
    r"\b(?:can|could|may|will|would)\s+(?:i|you|we|someone)\b|"
    r"\bi\s+(?:need|want)\b|\bi\s+would\s+like\b|\bi'?d\s+like\b|"
    r"\bi(?:'m|\s+am)\s+calling\s+about\b|\bhelp\s+with\b|"
    r"\bplease\s+(?:send|tell|email|help|give|check|show|explain|update|cancel|answer|repeat|clarify)\b|"
    r"\btell\s+me\b|\bexplain\s+the\b|\banswer\s+my\b|"
    r"\bi\s+have\s+(?:a|another|one\s+more)\s+question\b|\b(?:wait|hold\s+on|hang\s+on|stay\s+on\s+the\s+line)\b|"
    r"\b(?:don't|do\s+not)\s+hang\s+up\b|\bnot\s+(?:done|finished)\b", re.I)
_HOSTILE = re.compile(
    r"\bwhy\b|\bhow\s+dare\b|\bhow\s+many\s+times\b|\bhow\s+hard\s+is\b|\bwhat\s+is\s+it\s+with\b|"
    r"\bwhat(?:'s|\s+is)\s+wrong\s+with\b|\bwhat's\s+more\b|\bwhat\s+part\s+of\b|\bget\s+off\b|"
    r"\bwhen\s+will\s+you\b[^.!?;\n]*\blearn\b|\bwhen\s+i\s+say\b|"
    r"\b(?:want|need)\s+(?:you\s+|it\s+)?to\s+stop\b|"
    r"\bwhat\s+(?:are|do|did)\s+you\s+(?:going|gonna|want|even|think|people|doing|thinking)\b", re.I)
_SENTENCE = re.compile(r"[.!?;\n]")


def _window(text: str | None, position: int) -> tuple[str, int, int]:
    """The newest 1000 characters, the position inside them, and the offset cut off."""
    value = str(text or "")
    offset = max(0, len(value) - _OPT_OUT_WINDOW)
    return value[offset:], (position - offset if position >= 0 else -1), offset


def request_after_opt_out(text: str | None, opt_out_position: int = -1) -> int:
    """Start of the last real request AFTER the opt-out, in ``text``'s own offsets, or -1.

    Only the newest 1000 characters are read: the caller's latest words carry the
    decision, and the bound keeps the passes below cheap on the call's event loop.
    """
    window, position, offset = _window(text, opt_out_position)
    value = _text(window)
    found = -1
    for match in assertion_matches(window, _REQUEST):
        if match.start() <= position:
            continue
        ends = [m.end() for m in _SENTENCE.finditer(value, 0, match.start())]
        after = _SENTENCE.search(value, match.end())
        sentence = value[(ends[-1] if ends else 0):(after.start() if after else len(value))]
        if not _HOSTILE.search(sentence):
            found = match.start() + offset
    return found


def continues_after_opt_out(text: str | None, opt_out_position: int = -1) -> bool:
    """True when the caller asked for something after the opt-out (see above)."""
    return request_after_opt_out(text, opt_out_position) >= 0


_COURTESY = re.compile(
    r"(?:(?:okay|ok|right|fine|yes|yeah|yep|thanks(?: a lot| very much| so much)?|"
    r"thank you(?: very much| so much)?|cheers|great|lovely|perfect|cool|brilliant|mate|ta|then|"
    r"no|no thanks|nothing else|that's all|that's it|that's great|that is everything|that is all|"
    r"alright|sure|got it|understood|bye|goodbye|fair enough|will do|much appreciated|"
    r"appreciated|appreciate it)\s+)+")
_BARE_ANSWER = frozenset("yes yeah yep no okay ok sure right fine alright".split())
_COURTESY_MAX_WORDS = 8
_COURTESY_MAX_CHARS = 100


def courtesy_only(text: str | None, previous_assistant_text: str | None = None) -> bool:
    """The whole turn is only acknowledgement or courtesy words (at most 8).

    Refused: any digit (a number is an answer), and a bare yes/no/okay when the
    agent's last turn ended with a question (it answers that question). The input
    is bounded before the regex runs, so a long turn cannot make the overlapping
    alternatives backtrack.
    """
    raw = str(text or "")
    if len(raw) > _COURTESY_MAX_CHARS or any(c.isdigit() for c in raw):
        return False
    words = re.findall(r"[a-z']+", raw.lower().replace("\u2019", "'"))
    if not words or len(words) > _COURTESY_MAX_WORDS:
        return False
    if _COURTESY.fullmatch(" ".join(words) + " ") is None:
        return False
    if all(w in _BARE_ANSWER for w in words):
        from app.domain.services.end_session_action import _reply_scoped_to_question
        if _reply_scoped_to_question(previous_assistant_text):
            return False
    return True


def last_asserted_position(text: str | None, pattern: Pattern[str]) -> int:
    matches = assertion_matches(text, pattern)
    return matches[-1].start() if matches else -1
