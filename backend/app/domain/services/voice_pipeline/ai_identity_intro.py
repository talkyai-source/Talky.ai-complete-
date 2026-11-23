"""The agent's own introductions always say it is an AI.

Live calls 20 and 23 Nov 2026: every time the language model introduced the
agent in its own words ("Hi there, this is Grace from All State Estimation.")
it left "AI" out (42 of 42 introductions), although its instructions say to be
honest about being an AI assistant. That happens whenever the code-built
opening was cut off, a screening assistant puts the real person through, or the
model re-introduces the agent later.

``add_ai_identity`` runs as the last step of ``guard_spoken_sentence``. When a
sentence introduces the agent BY ITS STORED NAME ("this is Grace", "it's Grace",
"I'm Grace", "my name is Grace", "Hi there, Grace from ...", "Grace here"), it
puts ", an AI assistant" right after the name, unless this sentence or an
earlier sentence of the same reply already says it is an AI. Every pattern is
fixed words plus the escaped name, with no nested repetition, so matching is
linear in the input.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.domain.services.voice_pipeline.inbound_answer_greeting import says_it_is_ai

logger = logging.getLogger(__name__)

_SAY = ", an AI assistant"
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


def _name_pattern(words: list[str]) -> str:
    """The stored name, its later words optional after the first."""
    inner = ""
    for word in reversed(words[1:]):
        inner = rf"(?:[\s-]+{re.escape(word)}{inner})?"
    return rf"(?P<n>(?i:{re.escape(words[0])}{inner}))"


def _patterns(words: list[str]) -> tuple[tuple[str, "re.Pattern[str]"], ...]:
    n = _name_pattern(words)
    return (
        ("this_is", re.compile(rf"(?i:\bthis\s+is)\s+{n}{_AFTER}")),
        ("it_is", re.compile(rf"(?i:\bit(?:'s|\s+is))\s+{n}{_AFTER}")),
        ("i_am", re.compile(rf"(?i:\bi(?:'m|\s+am))\s+{n}{_AFTER}")),
        ("my_name_is", re.compile(rf"(?i:\bmy\s+name\s+is)\s+{n}{_AFTER}")),
        ("greeting_name",
         re.compile(rf"^\W*(?i:{_GREET})\W+{n}(?=\s+(?i:from|with)\b)")),
        ("name_here",
         re.compile(rf"^\W*(?:(?i:{_GREET})\W+)?{n}(?=\s+(?i:here|speaking)\b)")),
    )


def add_ai_identity(session: Any, sentence: str) -> str:
    """``sentence`` with ", an AI assistant" after the agent's name when it
    introduces the agent without saying it is an AI; otherwise unchanged."""
    if not sentence:
        return sentence
    agent_config = getattr(session, "agent_config", None)
    name = " ".join(str(getattr(agent_config, "agent_name", "") or "").split())
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    if not name:
        return sentence
    if says_it_is_ai(sentence):
        return sentence
    for earlier in getattr(session, "_spoken_sentences", None) or ():
        if says_it_is_ai(str(earlier)):
            return sentence
    # Same length as ``sentence``, so offsets carry over: curly apostrophes are
    # straightened and quoted words (someone else's) are blanked.
    text = sentence.replace("\u2019", "'").replace("\u2018", "'")
    text = _QUOTED.sub(lambda quoted: " " * len(quoted.group()), text)
    for where, pattern in _patterns(name.split()):
        for match in pattern.finditer(text):
            if match.group("n")[:1].islower():
                continue
            end = match.end("n")
            say = _SAY if _NO_COMMA.match(text, end) else _SAY + ","
            logger.info(
                "ai_identity_added call_id=%s where=%s",
                str(getattr(session, "call_id", "") or "")[:12], where,
            )
            return sentence[:end] + say + sentence[end:]
    return sentence
