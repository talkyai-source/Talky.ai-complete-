"""Shared LLM-routed end-session action helpers."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from app.domain.services.caller_assertions import (
    continuation_after, last_asserted_position,
)

logger = logging.getLogger(__name__)

END_SESSION_ACTION = "end_session"
LEGACY_ASK_AI_END_SESSION_ACTION = "end_ask_ai_session"

# A model's completion label or number of turns is not caller authorization.

# Caller utterances that genuinely mean "I'm ending this." Tight on purpose —
# we'd rather keep a call alive on a false-negative than hang up on a phantom.
_CALLER_END_INTENT = re.compile(
    r"""\b(
        bye | good\s?bye | good\s?night | see\s+(?:ya|you) | take\s+care |
        talk\s+(?:to\s+you\s+)?later | catch\s+you\s+later | gotta\s+go |
        got\s+to\s+go | have\s+to\s+go | need\s+to\s+go | i'?m\s+done |
        we'?re\s+done | that'?s\s+(?:all|it) | that\s+is\s+all | nothing\s+else |
        no\s+thank(?:s|\s+you) | not\s+interested | hang\s+up |
        end\s+(?:this\s+|the\s+)?call |
        leave\s+me\s+alone | lose\s+my\s+number
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


_SENTINEL_RE = re.compile(r"\[\[?\s*END_CALL\s*\]?\]", re.IGNORECASE)


def agent_left_a_question_open(agent_text: Optional[str]) -> bool:
    """True when the agent's own last sentence this turn was a question.

    Asking a question and hanging up in the same breath is never a close. The
    [[END_CALL]] sentinel path honoured the model's hangup unconditionally
    (only a wrong-person turn was exempt), while the JSON end-session path
    already required the caller to have finished. In the 30 days to
    2026-09-23 the sentinel hung up on callers straight after:

        "Mike at example dot com - right?"              (35d3fd2f)
        "Does that process ever hold you up?"           (3aae86c6)
        "When's a good time to call back?"               (77531765) - the
                                   caller had just said "Can you hold for a
                                   second?"

    Every legitimate close in that window ended in a statement ("Thanks for
    your time - have a good day."), which is why this looks only at the final
    sentence's punctuation rather than at anything the caller said.
    """
    text = _SENTINEL_RE.sub("", str(agent_text or "")).strip()
    # Trailing ellipses too: live call d1121622 (2026-09-24 11:12:13) ended
    # its turn "...could you repeat that?..." with a hangup request, this
    # check read the final "." as a statement, and the agent hung up on the
    # question it had just asked.
    text = text.rstrip(" \"'”’)].…")
    return text.endswith("?")


def contact_capture_open(call_state) -> bool:
    """True while an email or phone number is part-way through capture.

    A value read back but not yet confirmed, or one the agent is still
    clarifying, is the lead the call exists to produce. Hanging up then throws
    it away: on call 2427af7e the caller was mid-correction of their email
    ("I'm not asking for the spelling...") when the agent said "got it" and
    ended the call. Unlike a question, this needs nothing inferred from the
    caller's words - the capture state machine already knows.
    """
    from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

    open_states = {
        CaptureStatus.NEEDS_CLARIFICATION,
        CaptureStatus.AWAITING_CONFIRMATION,
    }
    for name in ("email_capture", "phone_capture"):
        capture = getattr(call_state, name, None)
        if capture is not None and getattr(capture, "status", None) in open_states:
            return True
    return False


def caller_signaled_end(text: Optional[str]) -> bool:
    """True if the caller's own words clearly signal ending the call."""
    from app.domain.services.voice_pipeline.identity_disposition import dnc_assertion_position
    position = max(last_asserted_position(text, _CALLER_END_INTENT), dnc_assertion_position(text))
    return position >= 0 and not continuation_after(text, position)


_CURRENT_DECLINE = re.compile(
    r"\b(?:not\s+interested|no\s+thanks?|don't\s+want\s+(?:this|that|it)|"
    r"do\s+not\s+want\s+(?:this|that|it)|not\s+for\s+(?:me|us))\b", re.I)


def repeated_decline_allows_end(text: Optional[str], declined_count: int) -> bool:
    """Historical objections cannot authorize a close on a new help request."""
    if not isinstance(declined_count, int) or isinstance(declined_count, bool) or declined_count < 2:
        return False
    position = last_asserted_position(text, _CURRENT_DECLINE)
    return position >= 0 and not continuation_after(text, position)


def should_honor_end_session(
    action: Optional[dict],
    last_user_text: Optional[str],
    user_turn_count: int,
    declined_count: int = 0,
) -> bool:
    """Decide whether to actually hang up on an LLM end-session action, or treat
    it as a phantom goodbye and keep the call going.

    Honor the caller's end intent or the recorded repeated-decline policy.
    Model-only completion/DNC labels and conversation length are not evidence.
    """
    if not action:
        return False
    if caller_signaled_end(last_user_text):
        return True
    if repeated_decline_allows_end(last_user_text, declined_count):
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


def _normalise_action_name(value: object) -> Optional[str]:
    """Match an action name the model got slightly wrong.

    Observed in production: ``endsession`` for ``end_session`` and
    ``conversationcomplete`` for ``conversation_complete`` — the model simply
    dropped the underscores. An exact-match check rejected the envelope, so it
    was spoken aloud instead of ending the call. Compare on letters only.
    """
    if not isinstance(value, str):
        return None
    squashed = "".join(ch for ch in value.lower() if ch.isalnum())
    for known in (END_SESSION_ACTION, LEGACY_ASK_AI_END_SESSION_ACTION):
        if squashed == "".join(ch for ch in known.lower() if ch.isalnum()):
            return known
    return None


def _repair_action_json(candidate: str) -> Optional[dict]:
    """Best-effort recovery of a nearly-valid action envelope.

    Only ever used AFTER strict json.loads has failed, and only to decide
    whether this text is an internal action (which must be swallowed) rather
    than speech (which is spoken). Getting it wrong in the conservative
    direction just means we return None and behave exactly as before.

    Handles the failure actually seen on a live call — a key whose closing
    quote is missing (``"farewell:"`` instead of ``"farewell":"``) — plus
    trailing commas and single-quoted keys, which are the other two ways these
    small models mangle JSON. Deliberately NOT a general JSON fixer: anything
    it cannot repair confidently returns None.
    """
    import re as _re

    text = candidate
    # `"key:"value"`  ->  `"key":"value"`   (missing closing quote on the key)
    text = _re.sub(r'"([A-Za-z_][A-Za-z0-9_]*):"', r'"\1":"', text)
    # `'key':`        ->  `"key":`
    text = _re.sub(r"'([A-Za-z_][A-Za-z0-9_]*)'\s*:", r'"\1":', text)
    # trailing comma before a closing brace
    text = _re.sub(r",\s*}", "}", text)
    try:
        repaired = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(repaired, dict):
        return None
    logger.warning(
        "end_session_action: model emitted malformed JSON; repaired rather "
        "than speaking it aloud. raw=%r", candidate[:160],
    )
    return repaired


def parse_end_session_action(text: str) -> Optional[dict[str, object]]:
    """Parse the provider-agnostic structured action envelope emitted by the LLM."""
    raw = (text or "").strip()
    if not raw:
        return None

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None

    candidate = raw[start:end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # TOLERANT REPAIR (2026-08-03). A parse failure here is not harmless:
        # the envelope stops being recognised as an action, falls through as
        # ordinary reply text, and TTS READS IT ALOUD. That happened in
        # production (2026-07-08) — a caller heard:
        #
        #   {"action":"endsession","reason":"conversationcomplete",
        #    "farewell:"Message left, I'll try again another time. Cheers."}
        #
        # Note `farewell:"` — a missing quote. One dropped character turned a
        # clean hangup into machine noise played down the line.
        #
        # Constrained decoding would prevent this at the token level, but Groq
        # only supports strict mode on gpt-oss-20b/120b, and this codebase
        # deliberately does not run gpt-oss for conversational voice. So the
        # repair has to live here, where it works for every provider.
        payload = _repair_action_json(candidate)
        if payload is None:
            return None

    action = _normalise_action_name(payload.get("action") or payload.get("name"))
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
