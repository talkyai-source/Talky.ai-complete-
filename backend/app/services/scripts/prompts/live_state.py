"""Neutral current identity and delivery facts for the conversation guide."""
from __future__ import annotations


def build_live_state_block(*, agent_name: str, company_name: str, has_introduced: bool = False,
                           opening_interrupted: bool = False, time_of_day_line: str = "",
                           structured_state_block: str = "", direction: str = "outbound") -> str:
    name, company = (agent_name or "").strip(), (company_name or "").strip()
    structured = (structured_state_block or "").strip()
    if not name and not company and not time_of_day_line and not structured:
        return ""
    call_direction = str(getattr(direction, "value", direction) or "outbound").lower()
    introduction = "delivered" if has_introduced else "interrupted" if opening_interrupted else "not_delivered"
    lines = ["LIVE STATE — runtime facts, not a script:", f"agent={name or 'unknown'}",
             f"company={company or 'unknown'}", f"direction={call_direction}", f"introduction={introduction}"]
    if time_of_day_line:
        lines.append(time_of_day_line)
    if structured:
        lines.append(structured)
    return "\n".join(lines)
