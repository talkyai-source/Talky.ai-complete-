"""The line an agent speaks when it answers an incoming call that has no saved greeting.

Seen 2026-11-20: callers rang a caller-first campaign and nobody spoke. The
saved greeting is used when the campaign has one. Without one, the existing
inbound persona lines never say that the agent is an AI, which the product's
compliance rules require, so the silent-pickup path uses this line instead.
"""
from __future__ import annotations

import re


def build_inbound_answer_greeting(session) -> str:
    """"Hello, thank you for calling <company>. This is <agent>, an AI assistant. How can I help you?"

    With no agent name on file: "...This is an AI assistant. ..." (never the
    "your assistant" placeholder). Never asks who is speaking: the caller rang us.
    """
    from app.domain.services.telephony.config import _resolve_greeting_context

    _placeholder_name, company = _resolve_greeting_context(session)
    agent_config = getattr(session, "agent_config", None)
    agent = str(getattr(agent_config, "agent_name", "") or "").strip() if agent_config else ""
    who = f"This is {agent}, an AI assistant." if agent else "This is an AI assistant."
    return f"Hello, thank you for calling {company}. {who} How can I help you?"


def build_inbound_identity_line(session) -> str:
    """What an incoming call says before the model's first reply when the CALLER spoke first:
    "Thank you for calling <company>. This is <agent>, an AI assistant."

    No company on file: no first sentence. No agent name: "This is an AI assistant."
    Nothing is invented. No question at the end: the caller already said what
    they want and the model's reply follows at once.
    """
    agent_config = getattr(session, "agent_config", None)
    company = " ".join(str(getattr(agent_config, "company_name", "") or "").split()) if agent_config else ""
    agent = " ".join(str(getattr(agent_config, "agent_name", "") or "").split()) if agent_config else ""
    who = f"This is {agent}, an AI assistant." if agent else "This is an AI assistant."
    return f"Thank you for calling {company}. {who}" if company else who


# ---------------------------------------------------------------------------
# An incoming call's first line always tells the caller it is an AI.
#
# A greeting the customer saved, an after-hours message and the persona answering
# lines are spoken as written, and none of them has to say it. No sentence-level
# "does this line say it is an AI" check existed (speech_guard only catches the
# agent CLAIMING to be human), so this module owns one. It errs toward adding the
# sentence: a repeat is harmless, a missing disclosure is not.
# ---------------------------------------------------------------------------
_AI_NOUN = r"(?:assistant|agent|receptionist|system|helper|operator|representative)"
# What counts as the line already saying it: "an AI", "AI assistant", "artificial
# intelligence", "automated/virtual/digital assistant|agent|...", "a bot", "a robot".
# A bare "AI-powered scheduling" or "automated reminders" does not.
_SAYS_AI = re.compile(
    rf"\ban\s+a\.?i\.?(?!\w)"
    rf"|\ba\.?i\.?(?:[-\s]powered)?\s+(?:voice\s+|phone\s+|call\s+)?{_AI_NOUN}\b"
    rf"|\bartificial\s+intelligence\b"
    rf"|\b(?:automated|virtual|digital)\s+(?:voice\s+|phone\s+|call\s+)?{_AI_NOUN}\b"
    rf"|\ba\s+(?:chat)?bot\b|\ba\s+robot\b",
    re.IGNORECASE,
)
# A full stop after one of these is not the end of a sentence.
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "jr", "sr", "inc", "ltd", "co", "no", "vs", "a.m", "p.m", "am", "pm"}
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'“(]?[A-Z0-9])")


def says_it_is_ai(text: str) -> bool:
    return bool(_SAYS_AI.search((text or "").replace("’", "'")))


def _boundaries(text: str) -> list[int]:
    """Start offsets of every sentence after the first."""
    found = []
    for m in _BOUNDARY.finditer(text):
        last_word = text[: m.start()].rsplit(None, 1)[-1].rstrip(".!?").lower() if text[: m.start()].strip() else ""
        if text[m.start() - 1] == "." and last_word in _ABBREVIATIONS:
            continue
        found.append(m.end())
    return found


def _join_to_agent_name(greeting: str, agent: str) -> str | None:
    """When the line already introduces the agent by the name on file ("this is
    Sarah", "Sarah speaking", "I'm Sarah", "my name is Sarah", "you've reached
    Sarah", "Hi, Sarah from ..."), the same line with ", an AI assistant" put
    right after that name, so the name is not said twice. None when it names no
    one, or someone else."""
    name = re.escape(agent).replace(r"\ ", r"\s+")
    flags = re.IGNORECASE
    patterns = (
        rf"\b(?:this\s+is|i['’]?m|i\s+am|my\s+name\s+is|you(?:['’]ve|\s+have)\s+reached|it['’]?s"
        rf"|(?:speaking|talking)\s+(?:with|to))\s+(?P<n>{name})(?!\w)",
        rf"(?<![\w'’])(?P<n>{name})(?=\s+(?:here|speaking)\b)",
        rf"^\W*(?:hi|hey|hello)\W+(?P<n>{name})(?=\s+from\b)",
    )
    for pattern in patterns:
        m = re.search(pattern, greeting, flags)
        if m:
            end = m.end("n")
            before_here = re.match(r"\s+(?:here|speaking)\b", greeting[end:], flags)
            say = ", an AI assistant," if before_here else ", an AI assistant"
            return f"{greeting[:end]}{say}{greeting[end:]}"
    return None


def ensure_ai_disclosure(greeting: str, session) -> str:
    """``greeting`` unchanged when it already says it is an AI, else with
    "This is <agent>, an AI assistant." added before its closing question (after
    its first sentence when it ends in no question, in front when it is one
    sentence). No agent name on file: "This is an AI assistant." Never a made-up name."""
    if not greeting or says_it_is_ai(greeting):
        return greeting
    agent_config = getattr(session, "agent_config", None)
    agent = str(getattr(agent_config, "agent_name", "") or "").strip() if agent_config else ""
    line = f"This is {agent}, an AI assistant." if agent else "This is an AI assistant."
    if agent:
        joined = _join_to_agent_name(greeting, agent)
        if joined is not None:
            return joined
    cuts = _boundaries(greeting)
    if not cuts:
        return f"{line} {greeting}"
    cut = cuts[-1] if greeting.rstrip().endswith("?") else cuts[0]
    return f"{greeting[:cut]}{line} {greeting[cut:]}"
