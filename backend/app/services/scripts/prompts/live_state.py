"""Small per-turn identity and progress header; runtime evidence owns state."""
from __future__ import annotations


def build_live_state_block(
    *,
    agent_name: str,
    company_name: str,
    has_introduced: bool = False,
    opening_interrupted: bool = False,
    time_of_day_line: str = "",
    structured_state_block: str = "",
    direction: str = "outbound",
) -> str:
    """Render current status without inferring call direction from who spoke first."""
    name = (agent_name or "").strip()
    company = (company_name or "").strip()
    structured = (structured_state_block or "").strip()
    if not name and not company and not time_of_day_line and not structured:
        return ""
    inbound = str(getattr(direction, "value", direction) or "outbound").lower() == "inbound"
    who = f"You're on this call as {name}" if name else "You're on this call"
    if company:
        who += f", for {company}"
    lines = [f"- {who}. Keep this exact name and the same role throughout."]
    if has_introduced:
        lines.append(
            "- You have ALREADY introduced yourself. Do NOT introduce yourself again "
            "unless asked who you are; continue from the caller's latest words."
        )
    elif opening_interrupted:
        lines.append(
            "- The opening was interrupted before delivery was confirmed. Follow the "
            "caller's latest words; do not restart the greeting or permission question. "
            "Identify yourself briefly only if still needed, without delaying their answer."
        )
    elif inbound:
        lines.append(
            "- The caller contacted the company. You have not introduced yourself yet. "
            "Answer their request first; identify yourself briefly if needed. "
            "Do not say you called them or use a cold-call permission ask. "
            "Do not repeat a greeting already delivered by the runtime."
        )
    else:
        lines.append(
            "- You have not introduced yourself yet: say who you are and why you're "
            "calling in one breath, under twenty words, then stop and let them answer. "
            "A brief permission ask is fine after the reason; answer a direct question first."
        )
    lines.append(
        "- Turn priority: respect a clear stop or urgent safety need first; otherwise "
        "answer an unanswered direct question or accept a correction before any "
        "discovery, qualification or next step. Current caller intent and confirmed "
        "runtime state override campaign assumptions."
    )
    if time_of_day_line:
        lines.append(time_of_day_line)
    if structured:
        lines.append(structured)
    return "LIVE STATE — current call status, read this before you reply:\n" + "\n".join(lines)
