"""Per-turn LIVE STATE block — a fresh, top-of-prompt fact that re-anchors the
agent's identity and progress on EVERY turn.

Why this exists
---------------
"Introduce yourself once / never re-introduce / never invent a title" is a
*static* rule in the base prompt. Over a call — especially on smaller models —
attention dilutes and the model forgets it already introduced itself, so it
re-introduces and sometimes drifts its job title (observed in production
transcripts: an agent that opened as a "representative" later called itself a
"senior business consultant"). A short fact restated *every* turn is far harder
to lose than a rule buried earlier in a long prompt.

This is the per-turn anchor that fixes that — and it's provider-agnostic, so it
helps every model, the weak ones most.

Design
------
- Restates the agent's NAME every turn (the value that drifts most) and
  *references* — never re-declares — the role, so it can't introduce a second,
  competing role title (the persona body is the one place the role is defined).
- ``has_introduced`` is the key signal: once the agent has given its real
  opening (set in turn_runner after the first LLM reply), this flips and tells
  the model not to introduce itself again.

Pure function, no I/O. Prepended at the very TOP of the per-turn prompt by
``build_turn_prompt`` (above the CAPTURED facts) so it sits in the
highest-attention position.
"""
from __future__ import annotations


def build_live_state_block(
    *,
    agent_name: str,
    company_name: str,
    has_introduced: bool = False,
) -> str:
    """Return the LIVE STATE block, or '' when there's no identity to anchor."""
    name = (agent_name or "").strip()
    company = (company_name or "").strip()
    if not name and not company:
        return ""

    # Phrased as a status line ("You're on this call as …") rather than a fresh
    # "You are …" declaration, so the literal identity is declared once (in the
    # persona) while LIVE STATE still re-anchors the name every turn.
    who = f"You're on this call as {name}" if name else "You're on this call"
    if company:
        who += f", for {company}"
    lines = [
        f"- {who}. Keep this exact name for the whole call, and keep the same "
        "role you opened with — never switch to a different name or job title.",
    ]
    if has_introduced:
        lines.append(
            "- You have ALREADY introduced yourself and said why you're calling. "
            "Do NOT introduce yourself again, restate your name/role, or "
            "re-explain your reason for calling — just continue naturally from "
            "where the conversation is now."
        )
    else:
        lines.append(
            "- You have not introduced yourself yet — give your short opening "
            "this turn: who you are and why you're calling, in one or two "
            "sentences."
        )
    return (
        "LIVE STATE — current call status, read this before you reply:\n"
        + "\n".join(lines)
    )
