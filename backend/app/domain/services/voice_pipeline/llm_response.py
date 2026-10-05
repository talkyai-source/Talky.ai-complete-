"""Sentence budget used by the traditional streaming turn pipeline."""
from __future__ import annotations

from typing import Optional


def response_max_sentences_for_turn(
    turn_or_session,
    user_input: str = "",
    *,
    has_custom_prompt: bool = False,
) -> Optional[int]:
    """Return the sentence budget for a turn.

    Supports the legacy ``(session, user_input, has_custom_prompt=...)``
    call shape and the newer internal ``(turn_id)`` shape.
    """
    if isinstance(turn_or_session, int):
        return 2 if turn_or_session == 0 else None

    session = turn_or_session
    default_limit = getattr(getattr(session, "agent_config", None), "response_max_sentences", 2) or 2
    limit = default_limit

    # Read-back budget (issue #6): when a sensitive slot is captured but not yet
    # confirmed (issue #1), the agent owes a read-back + confirmation question
    # ("...did I get that right?"). At the 2-sentence floor the streaming loop
    # truncates that trailing question, silently skipping confirmation — so give
    # the read-back turn room for a short lead-in + statement + question.
    slots = getattr(session, "captured_slots", None)
    readback_due = bool(getattr(slots, "email", None)) and not getattr(slots, "email_confirmed", False)
    if readback_due:
        limit = max(limit, 3)

    text = (user_input or "").lower()
    asks_pricing = any(term in text for term in ("pricing", "price", "plan", "plans", "package", "packages"))
    if has_custom_prompt and asks_pricing:
        limit = max(limit, 4)

    return limit
