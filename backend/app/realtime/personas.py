"""Independent personas for speech-to-speech models."""
from dataclasses import dataclass
from typing import Optional

PERSONAS = {
    "assistant": "Help with the caller's question. Ask one relevant question at a time.",
    "sales": "Understand the business need and answer from company knowledge. Offer one available next step when it fits their stated need. If they decline it, do not repackage the same offer. A factual no describes their situation; it is not rejection. A new prospect may need help getting started instead of switching. Use approved facts and qualification criteria.",
    "support": "Listen to the issue, clarify one point at a time, and offer only verified troubleshooting. Be clear when the team must help.",
    "receptionist": "Identify the reason for the call, answer verified questions, and collect a concise message for the correct team when needed.",
}

@dataclass
class RealtimePersona:
    """Minimal persona/campaign inputs for the realtime instruction string.

    Intentionally small and self-contained — the realtime path does not reuse
    the cascaded PersonaType / campaign-slot machinery.
    """
    agent_name: str = "Alex"
    company_name: str = "the company"
    role: str = "a friendly voice assistant"
    goal: str = "have a helpful, natural conversation with the caller"
    # Optional extra, operator-supplied freeform guidance (kept short).
    extra_notes: Optional[str] = None
    # Direction/opening values are explicit because true inbound campaigns may
    # be caller-first OR agent-first.  The default preserves every existing
    # outbound realtime session.
    call_direction: str = "outbound"
    opening_greeting: Optional[str] = None
    message_intake: bool = False
    campaign_guidance: str = ""
    persona_type: str = "assistant"
