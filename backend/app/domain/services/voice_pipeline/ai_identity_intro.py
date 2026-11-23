"""The agent's own introductions always say it is an AI.

Live calls 20 and 23 Nov 2026: every time the language model introduced the
agent in its own words ("Hi there, this is Grace from All State Estimation.")
it left "AI" out (42 of 42 introductions), although its instructions say to be
honest about being an AI assistant. That happens whenever the code-built
opening was cut off, a screening assistant puts the real person through, or the
model re-introduces the agent later.

``add_ai_identity`` runs as the last step of ``guard_spoken_sentence``. When a
sentence introduces the agent BY ITS STORED NAME ("this is Grace", "it's Grace",
"I'm Grace", "my name is / my name's Grace", "Hi there, Grace from ...",
"Grace here"), it puts ", an AI assistant" right after the name, unless this
sentence or an earlier sentence of the same reply already says the SPEAKER is an
AI ("I'm an AI", "your virtual assistant", ", an AI assistant"; a product
mention such as "our AI takeoff tool", "AI-powered" or a denial does not count).

Rules that keep a caller who shares the agent's name from being called an AI:
a unit that ends in a question mark is amended only when the name is directly
followed by from / with / calling; the "it's" form is an introduction only when
the name is followed by from / with / at / here / calling / speaking / again, or
when it opens the unit (only a greeting or interjection before it) and the name
ends the clause. Only the first four words of the stored name are matched.

Every pattern is fixed words plus the escaped name, with only bounded optional
words (no unbounded repetition of a group), so matching is linear in the input.
The patterns are compiled once per name, and the step never raises: on any
error the sentence is returned unchanged.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_SAY = ", an AI assistant"
_MAX_NAME_WORDS = 4
_GREET = (
    r"(?:hi|hey|hello|morning|afternoon|evening|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+there)?"
)
# After the name: punctuation other than "?", the end, or a lowercase word. A
# possessive ("Grace's"), a longer word, a capitalised surname or a question
# is not an introduction.
_AFTER = r"(?![\w'-])(?!\s*\?)(?=\s*$|\s*[,.;:!\u2014\u2013-]|\s+[a-z])"
# No comma is added when the next text already starts a phrase or punctuation.
_NO_COMMA = re.compile(r"\s+(?i:from|with|at|for|of)\b|\s*[,.;:!?\u2014\u2013-]|\s*$")
_QUOTED = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')
# A unit that ends in "?" is an introduction only with one of these after the name.
_QUESTION_OK = re.compile(r"\s+(?i:from|with|calling)\b")
# "it's <Name>" is an introduction with one of these after the name ...
_IT_FOLLOW = re.compile(r"\s+(?i:from|with|at|here|calling|speaking|again)\b")
# ... or when it opens the unit and the name ends the clause.
_IT_OPENER = re.compile(
    rf"^[\W_]*(?:(?i:{_GREET}|yes|yeah|yep|oh|ah|hmm|um|uh|well|okay|ok|sure|sorry)\b[\W_]*)*$"
)
_CLAUSE_END = re.compile(r"\s*(?:$|[,.;:!\u2014\u2013-])")

# Does the text say the SPEAKER is an AI? (A product mention, "AI-powered" or a
# negation does not.) Fixed words and bounded gaps only.
_AI_NOUN = (
    r"(?:ai|virtual|automated|digital|artificial\s+intelligence)"
    r"(?:\s+(?:voice|phone|call))?\s+(?:assistant|agent)\b"
)
_AI_BARE = (
    r"(?:ai|artificial\s+intelligence)(?![\w-])"
    r"(?=\s*(?:$|[,.;:!?\u2014\u2013])"
    r"|\s+(?:from|calling|for|with|at|on|here|that|who|and|to|speaking|of|so|but|or)\b)"
)
_SPEAKER_AI = re.compile(
    rf"\b(?:an?|your)\s+{_AI_NOUN}"
    rf"|\bi(?:'m|\s+am)\s+(?:(?:just|only|actually|also|really|simply)\s+)?(?:an?\s+)?(?:{_AI_NOUN}|{_AI_BARE})"
    rf"|\bthis\s+is\s+(?:an?|your)\s+{_AI_BARE}"
    rf"|,\s*(?:an?|your)\s+{_AI_BARE}",
    re.IGNORECASE,
)


def _speaker_says_ai(text: str) -> bool:
    return bool(_SPEAKER_AI.search((text or "").replace("\u2019", "'")))


def _name_pattern(words: tuple[str, ...]) -> str:
    """The stored name (at most four words), its later words optional after the first."""
    inner = ""
    for word in reversed(words[1:]):
        inner = rf"(?:[\s-]+{re.escape(word)}{inner})?"
    return rf"(?P<n>(?i:{re.escape(words[0])}{inner}))"


@lru_cache(maxsize=256)
def _patterns(words: tuple[str, ...]) -> tuple[tuple[str, "re.Pattern[str]"], ...]:
    n = _name_pattern(words)
    return (
        ("this_is", re.compile(rf"(?i:\bthis\s+is)\s+{n}{_AFTER}")),
        ("it_is", re.compile(rf"(?i:\bit(?:'s|\s+is))\s+{n}{_AFTER}")),
        ("i_am", re.compile(rf"(?i:\bi(?:'m|\s+am))\s+{n}{_AFTER}")),
        ("my_name_is", re.compile(rf"(?i:\bmy\s+name(?:\s+is|'s))\s+{n}{_AFTER}")),
        ("greeting_name",
         re.compile(rf"^\W*(?i:{_GREET})\W+{n}(?=\s+(?i:from|with|calling)\b)")),
        ("name_here",
         re.compile(rf"^\W*(?:(?i:{_GREET})\W+)?{n}(?=\s+(?i:here|speaking)\b)")),
    )


def add_ai_identity(session: Any, sentence: str) -> str:
    """``sentence`` with ", an AI assistant" after the agent's name when it
    introduces the agent without saying it is an AI; otherwise unchanged. Never
    raises: any error returns ``sentence`` unchanged."""
    try:
        return _add_ai_identity(session, sentence)
    except Exception as exc:  # the guard must never stop a reply being spoken
        logger.debug("ai_identity_skipped error=%s", type(exc).__name__)
        return sentence


def _add_ai_identity(session: Any, sentence: str) -> str:
    if not sentence:
        return sentence
    agent_config = getattr(session, "agent_config", None)
    name = " ".join(str(getattr(agent_config, "agent_name", "") or "").split())
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    if not name:
        return sentence
    if _speaker_says_ai(sentence):
        return sentence
    for earlier in getattr(session, "_spoken_sentences", None) or ():
        if _speaker_says_ai(str(earlier)):
            return sentence
    # Same length as ``sentence``, so offsets carry over: curly apostrophes are
    # straightened and quoted words (someone else's) are blanked.
    text = sentence.replace("\u2019", "'").replace("\u2018", "'")
    text = _QUOTED.sub(lambda quoted: " " * len(quoted.group()), text)
    question = text.rstrip(" \t\"')\u201d").endswith("?")
    for where, pattern in _patterns(tuple(name.split()[:_MAX_NAME_WORDS])):
        for match in pattern.finditer(text):
            if match.group("n")[:1].islower():
                continue
            end = match.end("n")
            if question and not _QUESTION_OK.match(text, end):
                continue
            if where == "it_is" and not (
                _IT_FOLLOW.match(text, end)
                or (_IT_OPENER.match(text[:match.start()]) and _CLAUSE_END.match(text, end))
            ):
                continue
            say = _SAY if _NO_COMMA.match(text, end) else _SAY + ","
            logger.info(
                "ai_identity_added call_id=%s where=%s",
                str(getattr(session, "call_id", "") or "")[:12], where,
            )
            return sentence[:end] + say + sentence[end:]
    return sentence
