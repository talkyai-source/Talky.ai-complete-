"""Caller-first telephony mode helpers."""
from __future__ import annotations


def apply_caller_first_inbound_prompt(voice_session) -> None:
    """
    Make caller-speaks-first behave like an inbound answer, not an outbound
    cold-call script. This is intentionally applied only to explicit
    first_speaker=user sessions.
    """
    session = voice_session.call_session
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company_name = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or "the company"
    inbound_rule = (
        "\n\nCALLER-FIRST INBOUND MODE (highest priority):\n"
        "This call is configured for caller-speaks-first. Treat it like the "
        "caller called us, not like an outbound cold call. Do not use the "
        "outbound opener, do not ask whether they have a minute, and do not "
        "mention that you were waiting for them to speak. On the first real "
        "caller utterance, if they say hello, hi, are you there, can you hear "
        "me, or similar, answer exactly in this style: "
        f"'Hi, this is {agent_name} from {company_name}.' Keep it one short "
        "sentence unless the caller asks a specific question."
    )
    if "CALLER-FIRST INBOUND MODE" not in session.system_prompt:
        session.system_prompt = f"{session.system_prompt}{inbound_rule}"
