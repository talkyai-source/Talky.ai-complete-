"""Shared call-direction primitives for prompt composition.

Two pieces live here so they can be referenced by both the prompt
composer (low-level) and the telephony bridge runtime (higher-level)
without creating an import cycle:

* ``INBOUND_DIRECTIVE_SENTINEL`` — the unique header string that marks a
  prompt as direction-aware. The runtime ``select_inbound_base_prompt``
  uses this for idempotency: if the sentinel is already in the prompt,
  the prompt was built inbound-aware at compose time and runtime
  shaping is a no-op.
* ``inbound_directive_block(...)`` — the canonical block that frames a
  CALLER-SPEAKS-FIRST outbound call: the agent dialed the callee but waits
  for them to speak, then leads with its own introduction + purpose. Used
  both by ``compose_prompt`` (when ``direction=INBOUND``) and by
  ``select_inbound_base_prompt`` (runtime fallback for legacy / non-
  composed prompts).

Keeping the sentinel and the block here means there is exactly one
canonical phrasing across the codebase. Tests assert downstream readers
match this canonical wording.

Historical note: the ``INBOUND_*`` names predate the realisation that
"caller speaks first" on an OUTBOUND dialer is a turn-taking choice, NOT a
genuine inbound call. The names are kept (composer, telemetry labels, and
tests import them) but the directive text below is outbound-framed: the
agent owns the call and introduces its purpose — it never plays receptionist.
"""
from __future__ import annotations


# First line of the caller-speaks-first directive AND the idempotency
# marker. Must stay byte-for-byte in sync with the first line of
# ``TELEPHONY_INBOUND_SYSTEM_PROMPT`` (telephony_session_config.py).
INBOUND_DIRECTIVE_SENTINEL = "OUTBOUND CALL — CALLEE SPEAKS FIRST"


def inbound_directive_block(*, agent_name: str, company_name: str) -> str:
    """Return the caller-speaks-first directive for the given agent/company.

    This frames an OUTBOUND call where the campaign owner chose to let the
    callee speak first (a brief courtesy pause before the agent talks). The
    agent still OWNS the call — it dialed this person and has a reason for
    calling — so after the callee says "hello" the agent introduces itself
    and its purpose. It must NOT act as a receptionist ("how can I help
    you?"), which would wrongly imply the callee dialed in.

    Llama-style and OpenAI Realtime models weight early tokens heavily, so
    this block is designed to land at position 0 of the system prompt — its
    framing overrides any outbound-cold-open or receptionist phrasing that
    might sit in the persona body below it.
    """
    return (
        f"{INBOUND_DIRECTIVE_SENTINEL} (this overrides any timing or "
        "opening instructions below):\n"
        f"- You are placing an OUTBOUND call on behalf of {company_name}. "
        "You dialed this person — they did NOT call you. They will speak "
        'first (usually a short "hello?"); wait for them before you say '
        "anything.\n"
        "- On their first words, give your opening: introduce yourself as "
        f"{agent_name} from {company_name} and say why you're calling, in "
        "your own natural words, following the personality and goal "
        "described below.\n"
        '- Do NOT answer like a receptionist. Never open with "how can I '
        'help you?", "thanks for calling", or "you\'ve reached ..." — you '
        "called THEM, so you lead with your reason for reaching out.\n"
        "- If they instead open with a direct question, answer it in 1-2 "
        "short sentences first, then continue.\n"
        "- Keep the opening to 1-2 short sentences and ask at most one "
        "question. After this opening turn, follow the personality, "
        "knowledge, and rules below for the rest of the call."
    )
