"""Shared call-direction primitives for prompt composition.

Two pieces live here so they can be referenced by both the prompt
composer (low-level) and the telephony bridge runtime (higher-level)
without creating an import cycle:

* ``INBOUND_DIRECTIVE_SENTINEL`` — the unique header string that marks a
  prompt as direction-aware. The runtime ``select_inbound_base_prompt``
  uses this for idempotency: if the sentinel is already in the prompt,
  the prompt was built inbound-aware at compose time and runtime
  shaping is a no-op.
* ``inbound_directive_block(...)`` — the canonical 6-bullet block that
  re-frames an outbound persona body for an inbound call. Used both by
  ``compose_prompt`` (when ``direction=INBOUND``) and by
  ``select_inbound_base_prompt`` (runtime fallback for legacy / non-
  composed prompts).

Keeping the sentinel and the block here means there is exactly one
canonical phrasing across the codebase. Tests assert downstream readers
match this canonical wording.
"""
from __future__ import annotations


INBOUND_DIRECTIVE_SENTINEL = "INBOUND CALL — YOU ANSWERED THE PHONE"


def inbound_directive_block(*, agent_name: str, company_name: str) -> str:
    """Return the inbound directive block for the given agent/company.

    Llama-style and OpenAI Realtime models weight early tokens heavily,
    so this block is designed to land at position 0 of the system
    prompt. Six bullets is enough to lock the call direction without
    crowding the persona body that trails below it.
    """
    return (
        f"{INBOUND_DIRECTIVE_SENTINEL} (this overrides everything below):\n"
        f"- The caller called {company_name}. You picked up the phone — "
        "you are NOT making an outbound call.\n"
        "- Treat the section below as the personality, knowledge, and rules "
        "you bring to the call. Ignore any instruction down there that tells "
        "you to introduce yourself as someone who is calling them, to ask "
        "whether they have a minute, or to recite an outbound opener.\n"
        "- On the caller's first utterance, if they said hello / hi / are "
        "you there / can you hear me, answer like a real person picking up "
        f"the phone: 'Hello, {company_name}, this is {agent_name} -- how "
        "can I help you?' Keep it that single sentence. Do not pitch. Wait "
        "for them to say what they need.\n"
        "- If they immediately asked a substantive question, answer it in "
        "1-2 short sentences and offer one short follow-up.\n"
        "- If they sound confused or asked 'who is this?', answer: "
        f"'This is {agent_name} at {company_name}. How can I help?'\n"
        "- After this opening turn, follow the personality and rules below "
        "for the rest of the conversation."
    )
