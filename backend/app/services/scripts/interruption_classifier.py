"""Interruption classifier — the event-lifecycle taxonomy for barge-in.

2026 best practice (Hamming voice-agent interruption runbook, LiveKit/Pipecat
turn-taking guides) treats an interruption not as a binary "user spoke" toggle
but as a classified event whose right handling depends on WHAT the caller did:

    | Caller did…              | Type        | Right action                     |
    | "uh-huh" / "yeah"        | backchannel | keep talking (false interrupt)   |
    | "no, I meant Friday"     | correction  | stop, accept the correction      |
    | "how much is it?"        | question    | stop, answer                     |
    | "get me a real person"   | escalation  | stop, route / honor opt-out      |
    | keypad press             | dtmf        | stop, route per menu policy      |
    | garbled / non-speech     | noise       | ignore, resume                   |
    | anything else with words | statement   | stop, respond                    |

This module is the single source of truth for that label. It is PURE and
side-effect free — callers decide what to DO with the label (today: emit the
interruption-quality metrics in turn_ender; the barge-in / suppress decisions
themselves are unchanged). It reuses ``interruption_filter.is_backchannel`` so
the backchannel definition stays in one place.

A "false interruption" — the metric operators care about most — is when the
agent's speech was stopped for a ``backchannel`` or ``noise`` that didn't
warrant a stop. Everything else is a legitimate interruption.
"""
from __future__ import annotations

import re
from enum import Enum

from app.services.scripts.interruption_filter import is_backchannel


class InterruptionType(str, Enum):
    BACKCHANNEL = "backchannel"
    CORRECTION = "correction"
    QUESTION = "question"
    ESCALATION = "escalation"
    DTMF = "dtmf"
    NOISE = "noise"
    STATEMENT = "statement"


# Caller wants a human, or is opting out / asking to be left alone. Anchored to
# request verbs + human nouns (so "I'm the account manager" doesn't trip it) and
# to explicit opt-out phrases. These are the highest-stakes interruptions.
_ESCALATION_RE = re.compile(
    r"\b("
    # Standalone strong signals — unambiguous in a call context.
    r"real person|live (?:person|agent)|representative|"
    # Request verb + a human noun ("talk to your manager", "get me a human").
    r"(?:speak|talk|connect me|transfer me|get me|give me|put me through|want|need)"
    r"\s+(?:to\s+|with\s+)?(?:a\s+|an\s+|your\s+|someone\s+)*"
    r"(?:human|person|agent|rep|manager|supervisor|operator|someone)|"
    # Opt-out / do-not-contact.
    r"do\s*n'?o?t\s+call|stop\s+calling|take me off|remove me from|"
    r"unsubscribe|opt\s*out|leave me alone"
    r")\b",
    re.IGNORECASE,
)

# Explicit correction / repair signals. Bare "no" is a backchannel (handled
# first); a "no …" with real content lands in STATEMENT unless it carries one of
# these repair markers.
_CORRECTION_RE = re.compile(
    r"\b("
    r"that'?s (?:not|wrong|incorrect)|that is (?:not|wrong)|"
    r"i meant|i said|i didn'?t (?:say|mean)|you (?:said|mean)|"
    r"actually|wait|hold on|go back|let me (?:stop|correct|clarify|finish)|"
    r"not (?:quite|right|what)|incorrect"
    r")\b",
    re.IGNORECASE,
)

# Interrogative openers (mirrors the kb_budget question signal, kept local).
_QUESTION_RE = re.compile(
    r"^\s*(?:wh(?:at|o|en|ere|y|ich)|how|can|could|would|will|do|does|"
    r"did|is|are|should|may|might)\b",
    re.IGNORECASE,
)


def _alpha_count(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def classify_interruption(
    transcript: str,
    *,
    dtmf: str | None = None,
) -> InterruptionType:
    """Classify what the caller did when they took the floor.

    Args:
        transcript: the caller's (possibly partial) utterance text.
        dtmf: any keypad digits captured for this event (telephony). When
            present it wins — a keypress is an unambiguous DTMF interruption.

    Precedence is deliberate: DTMF → noise(empty) → escalation → backchannel →
    correction → question → noise(non-speech) → statement.
    """
    if dtmf:
        return InterruptionType.DTMF

    text = (transcript or "").strip()
    if not text:
        return InterruptionType.NOISE

    # Escalation outranks everything spoken — honoring "stop calling me" or a
    # request for a human late would be the worst caller-visible failure.
    if _ESCALATION_RE.search(text):
        return InterruptionType.ESCALATION

    # Pure listening sound — the classic false interruption.
    if is_backchannel(text):
        return InterruptionType.BACKCHANNEL

    if _CORRECTION_RE.search(text):
        return InterruptionType.CORRECTION

    if text.endswith("?") or _QUESTION_RE.search(text):
        return InterruptionType.QUESTION

    # Non-speech garble (e.g. a stray token with no real letters) — treat as
    # noise so it counts as a false interruption rather than a real statement.
    if _alpha_count(text) < 2:
        return InterruptionType.NOISE

    return InterruptionType.STATEMENT


# Types where stopping the agent's speech was unnecessary — the "false
# interruption" set operators alert on.
_FALSE_INTERRUPTION_TYPES = frozenset(
    {InterruptionType.BACKCHANNEL, InterruptionType.NOISE}
)


def is_false_interruption(itype: InterruptionType) -> bool:
    """True when stopping active agent speech for this type was unwarranted."""
    return itype in _FALSE_INTERRUPTION_TYPES
