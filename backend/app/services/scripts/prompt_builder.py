"""Compose the per-turn system prompt.

Per Groq 2026 prompting docs, the model weighs early tokens most heavily,
so the CAPTURED block lives at the very top of the system message. The
static persona/style rules follow. An 8B model is far less likely to
re-ask for data it can see stated as a fact in the first 200 tokens of
its own system message.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState


def compose_system_prompt(base_prompt: str, state: CallState) -> str:
    """Return base_prompt with a CAPTURED-slots header prepended when state
    has any filled slot; otherwise return base_prompt unchanged.

    The header is deterministic and short (<= 120 tokens) so it never
    crowds out the persona rules.
    """
    lines: list[str] = []
    if state.email:
        lines.append(
            f"- Caller email (already given, do not ask again): {state.email}"
        )
    if state.follow_up:
        lines.append(
            f"- Follow-up time (already agreed): {state.follow_up}"
        )
    if state.bidding_active is True:
        lines.append("- Caller confirmed they are actively bidding on projects.")
    elif state.bidding_active is False:
        lines.append("- Caller said they are NOT actively bidding right now.")
    if state.declined_count >= 2:
        lines.append(
            "- Caller has declined twice. Close politely and end the call."
        )

    if not lines:
        return base_prompt

    header = (
        "CAPTURED (facts from this call — these are TRUE, "
        "do not re-ask, do not contradict):\n"
        + "\n".join(lines)
        + "\n"
        + "If a CAPTURED fact exists, acknowledge it and move on — never "
          "ask the same question again.\n"
        + "------------------------------------------------------------\n"
    )
    return header + base_prompt
