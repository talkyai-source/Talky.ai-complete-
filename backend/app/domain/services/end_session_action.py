"""Shared LLM-routed end-session action helpers."""
from __future__ import annotations

import json
import os
import re
from typing import Optional

END_SESSION_ACTION = "end_session"
LEGACY_ASK_AI_END_SESSION_ACTION = "end_ask_ai_session"

# Phantom-goodbye guard. The LLM sometimes emits the end-session action when
# the caller did NOT actually signal they were done ("triggers goodbye while I
# haven't asked"). We honor the hangup only when the caller's own words show
# end-intent, OR (for an agent-judged conversation_complete) after enough real
# exchange. Opt-out ("do not call me") is ALWAYS honored — compliance wins.
_MIN_COMPLETE_USER_TURNS = int(os.getenv("VOICE_MIN_END_USER_TURNS", "3"))

# Caller utterances that genuinely mean "I'm ending this." Tight on purpose —
# we'd rather keep a call alive on a false-negative than hang up on a phantom.
_CALLER_END_INTENT = re.compile(
    r"""\b(
        bye | good\s?bye | good\s?night | see\s+(?:ya|you) | take\s+care |
        talk\s+(?:to\s+you\s+)?later | catch\s+you\s+later | gotta\s+go |
        got\s+to\s+go | have\s+to\s+go | need\s+to\s+go | i'?m\s+done |
        we'?re\s+done | that'?s\s+(?:all|it) | that\s+is\s+all | nothing\s+else |
        no\s+thank(?:s|\s+you) | not\s+interested | hang\s+up | stop\s+calling |
        remove\s+me | take\s+me\s+off | do\s+not\s+call | don'?t\s+call | unsubscribe |
        leave\s+me\s+alone | lose\s+my\s+number
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def caller_signaled_end(text: Optional[str]) -> bool:
    """True if the caller's own words clearly signal ending the call."""
    return bool(text and _CALLER_END_INTENT.search(text))


def should_honor_end_session(
    action: Optional[dict],
    last_user_text: Optional[str],
    user_turn_count: int,
    declined_count: int = 0,
) -> bool:
    """Decide whether to actually hang up on an LLM end-session action, or treat
    it as a phantom goodbye and keep the call going.

    Honor when:
      * the caller asked never to be called again (do_not_call) — always, or
      * the caller's words actually signal an end, or
      * the caller has DECLINED >= 2 times (issue #16): the persona tells the
        agent to close politely after two declines, so its end-session there is
        a legitimate close, not a phantom — honoring it stops the recovery line
        re-opening a call the agent just ended, or
      * the model reports the task finished (conversation_complete) AND the call
        has had real back-and-forth (>= _MIN_COMPLETE_USER_TURNS user turns).
    Otherwise it's a phantom — suppress the hangup.
    """
    if not action:
        return False
    if action.get("do_not_call"):
        return True
    if caller_signaled_end(last_user_text):
        return True
    if declined_count >= 2:
        return True
    if action.get("reason") == "conversation_complete" and user_turn_count >= _MIN_COMPLETE_USER_TURNS:
        return True
    return False

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
