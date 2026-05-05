"""Shared LLM-routed end-session action helpers."""
from __future__ import annotations

import json
from typing import Optional

END_SESSION_ACTION = "end_session"
LEGACY_ASK_AI_END_SESSION_ACTION = "end_ask_ai_session"

END_SESSION_REASONS = {
    "user_goodbye",
    "user_done",
    "conversation_complete",
}

DEFAULT_FAREWELL = "Goodbye, take care."


def build_end_session_tool_instructions(*, action_name: str = END_SESSION_ACTION) -> str:
    return (
        f"Internal action available: {action_name}.\n"
        "If the user is clearly ending the interaction, saying goodbye, saying they "
        "are done, asking to hang up, or indicating the conversation is complete, "
        "respond with exactly this JSON and no spoken text outside JSON:\n"
        f'{{"action":"{action_name}","reason":"user_goodbye","farewell":"{DEFAULT_FAREWELL}"}}\n'
        "Use reason user_goodbye for farewells, user_done when the user says they "
        "are done, and conversation_complete when the task is clearly finished. "
        "Set farewell to one short natural sentence that matches the user's goodbye "
        "style: if they say goodbye, say goodbye; if they say see you, say see you; "
        "if they say take care, answer in that same friendly closing style. "
        "For all other messages, answer normally. Do not use this action when the "
        "user is asking a question about ending, goodbye handling, calls, or sessions."
    )


def parse_end_session_action(text: str) -> Optional[dict[str, str]]:
    """Parse the provider-agnostic structured action envelope emitted by the LLM."""
    raw = (text or "").strip()
    if not raw:
        return None

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None

    try:
        payload = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None

    action = payload.get("action") or payload.get("name")
    if action not in {END_SESSION_ACTION, LEGACY_ASK_AI_END_SESSION_ACTION}:
        return None

    reason = payload.get("reason") or "conversation_complete"
    if reason not in END_SESSION_REASONS:
        reason = "conversation_complete"

    farewell = payload.get("farewell") or payload.get("message") or DEFAULT_FAREWELL
    if not isinstance(farewell, str) or not farewell.strip():
        farewell = DEFAULT_FAREWELL

    return {"reason": reason, "farewell": farewell.strip()}
