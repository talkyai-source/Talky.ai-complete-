"""Capture mode — relax STT turn-detection while the caller spells an email.

When the agent *asks* for an email (or asks the caller to spell something),
the caller's next turn is a slow, pause-heavy spell-out: "j... o... h... n...
at... gmail... dot com." Normal turn-detection fires EndOfTurn on the first
gap and ships half an address to the LLM. So the instant the agent's outgoing
line is an email/spell ask, we flip the STT into *capture mode* (longer
end-of-turn timeout, higher confidence) for that one upcoming turn, then revert.

Trigger = the agent's own outgoing text (works for every persona and for both
telephony and ask-AI — no persona/stage wiring). Mechanism lives on the STT
provider (DeepgramFlux.enter_capture_mode / reset_capture_mode); this module
only decides *when*. Providers that don't support it are silently skipped.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Agent phrases that mean "the caller is about to spell an email / sensitive
# detail." Kept deliberately tight — a false positive only makes one turn
# patient (slightly slower), never breaks anything, but we still avoid matching
# statements like "I'll send that to your email."
_EMAIL_ASK = re.compile(
    r"""(
        (?:what'?s|what\s+is|can\s+i\s+(?:get|have|grab|take)|could\s+(?:you|i)|
           may\s+i\s+have|give\s+me|share\s+(?:your|me)|grab|get)\b[^.?!]{0,40}\be-?mail\b
        | \be-?mail\s+address\b
        | \bspell\s+(?:that|it|your\s+e-?mail|your\s+name|the\s+e-?mail)\b
        | \bsay\s+(?:that|it)\s+(?:again\s+)?(?:once\s+more\s+)?slowly\b
        # bare "your email?" ask — but not statements like "to/the/via your email"
        | (?<!to\s)(?<!the\s)(?<!via\s)(?<!on\s)\byour\s+e-?mail\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# The agent READING an address back ("...that's j dot smith at gmail dot com,
# did I get that right?"). The caller's next turn is either a one-word "yes" or
# a slow, pause-heavy correction -- the very spell-out capture mode exists for.
# Arming only on the ASK missed every correction turn, so a corrected address
# was endpointed mid-spell and the caller's fix was lost (live call 2026-09-21).
#
# Both halves are required: an address AND a confirmation cue. Lookaheads, so
# the two may appear in either order. Requiring the cue keeps a plain statement
# ("I will send it to john at gmail dot com") from arming.
_EMAIL_READBACK = re.compile(
    r"""(?=.*(?:\bat\b[^.?!]{0,40}\bdot\s+[a-z]{2,}|@[a-z0-9.-]+\.[a-z]{2,}))
        (?=.*(?:did\s+i\s+(?:get|say|hear)|is\s+(?:that|this|it)\s+(?:right|correct)
              |that\s+right|got\s+(?:that|it)\s+right|is\s+that\s+ok(?:ay)?
              |correct\?))""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# Calls currently in capture mode (single process / single worker).
_active_calls: set[str] = set()


def detect_email_ask(text: Optional[str]) -> bool:
    """True if the agent line is asking the caller for an email / to spell."""
    return bool(text and _EMAIL_ASK.search(text))


def detect_email_readback(text: Optional[str]) -> bool:
    """True if the agent line is reading an address back for confirmation."""
    return bool(text and _EMAIL_READBACK.search(text))


def detect_capture_trigger(text: Optional[str]) -> bool:
    """True if the caller's NEXT turn is likely a pause-heavy spell-out: either
    the agent just asked for an address, or it read one back to be corrected."""
    return detect_email_ask(text) or detect_email_readback(text)


def _flux(provider: Any) -> Any:
    """Resolve the underlying provider that supports capture mode.

    stt_provider may be DeepgramFlux directly, or a ResilientSTTProvider
    wrapping it. Returns the object exposing enter/reset, or None.
    """
    for obj in (
        provider,
        getattr(provider, "_active", None),
        getattr(provider, "active", None),
        getattr(provider, "_primary", None),
        getattr(provider, "primary", None),
    ):
        if obj is not None and hasattr(obj, "enter_capture_mode"):
            return obj
    return None


def maybe_enter(provider: Any, call_id: str, agent_text: str) -> None:
    """Enter capture mode if the agent just asked for an email/spelling."""
    if not call_id or call_id in _active_calls:
        return
    if not detect_capture_trigger(agent_text):
        return
    target = _flux(provider)
    if target is None:
        return
    try:
        target.enter_capture_mode(call_id)
        _active_calls.add(call_id)
        logger.info("capture_mode ENTER call_id=%s (email/spell ask detected)", call_id[:12])
    except Exception as exc:  # never let this break the turn
        logger.warning("capture_mode enter failed call_id=%s: %s", call_id[:12], exc)


def maybe_exit(provider: Any, call_id: str) -> None:
    """Revert to normal turn-detection once the captured turn has come in."""
    if not call_id or call_id not in _active_calls:
        return
    _active_calls.discard(call_id)
    target = _flux(provider)
    if target is None:
        return
    try:
        target.reset_capture_mode(call_id)
        logger.info("capture_mode EXIT call_id=%s (back to normal)", call_id[:12])
    except Exception as exc:
        logger.warning("capture_mode exit failed call_id=%s: %s", call_id[:12], exc)


def clear(call_id: str) -> None:
    """Drop tracking for a call (e.g. on teardown) without touching the ws."""
    _active_calls.discard(call_id)
