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
        "If — and only if — the user asks NOT to be called again (\"stop calling me\", "
        "\"remove me from your list\", \"take me off\", \"do not call me\", \"unsubscribe\"), "
        'add "do_not_call":true to the same JSON and set the farewell to a brief, '
        "respectful confirmation that they won't be contacted again, e.g. "
        f'{{"action":"{action_name}","reason":"user_done","farewell":"Understood — I\'ll '
        'remove you from our list. Sorry to bother you, take care.","do_not_call":true}}. '
        "Do NOT set do_not_call for ordinary goodbyes, objections, or \"I\'m busy right "
        "now\" — only a genuine request never to be called again. "
        "For all other messages, answer normally. Do not use this action when the "
        "user is asking a question about ending, goodbye handling, calls, or sessions."
    )


def parse_end_session_action(text: str) -> Optional[dict[str, object]]:
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

    # Opt-out flag — accept real booleans and the common string spellings an
    # LLM might emit. Defaults to False so ordinary end-sessions are unaffected.
    raw_dnc = payload.get("do_not_call")
    do_not_call = raw_dnc is True or (
        isinstance(raw_dnc, str) and raw_dnc.strip().lower() in {"true", "yes", "1"}
    )

    return {
        "reason": reason,
        "farewell": farewell.strip(),
        "do_not_call": do_not_call,
    }
