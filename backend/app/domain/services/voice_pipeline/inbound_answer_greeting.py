"""The line an agent speaks when it answers an incoming call that has no saved greeting.

Seen 2026-11-20: callers rang a caller-first campaign and nobody spoke. The
saved greeting is used when the campaign has one. Without one, the existing
inbound persona lines never say that the agent is an AI, which the product's
compliance rules require, so the silent-pickup path uses this line instead.
"""
from __future__ import annotations


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
