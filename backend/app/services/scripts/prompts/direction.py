"""Call origin and first-speaker context shared by composition and runtime."""
from __future__ import annotations

INBOUND_DIRECTIVE_SENTINEL = "OUTBOUND CALL — CALLEE SPEAKS FIRST"


def inbound_directive_block(*, agent_name: str, company_name: str) -> str:
    """Historical name retained for caller-first outbound calls."""
    return (
        f"{INBOUND_DIRECTIVE_SENTINEL}\n"
        f"You are {agent_name} for {company_name}. You dialed this person; they did not call the company. "
        "Wait for them to speak first. Introduce yourself and the approved reason naturally if needed, "
        "then follow their response. A direct question takes priority over an opening."
    )
