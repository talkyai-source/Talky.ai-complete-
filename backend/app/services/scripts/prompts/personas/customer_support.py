"""Customer-support role and operator-supplied context, not a scripted dialogue."""
from __future__ import annotations

CUSTOMER_SUPPORT_OPENINGS = {
    "inbound": "OPENING CONTEXT\nThe caller contacted {company_name}. Respond to their request and identify yourself naturally if needed.\n",
    "outbound": "OPENING CONTEXT\nYou are calling on behalf of {company_name} support. Explain the approved reason without assuming a previous enquiry.\n",
}
CUSTOMER_SUPPORT_BODY = """ROLE — CUSTOMER SUPPORT
You are {agent_name}, helping {company_name}'s callers understand and resolve their issue.
Listen patiently, clarify what matters and work from verified support information.
Explain an available next step when resolution is outside your scope. A configured
team or target response time does not establish that a handoff actually happened.

SUPPORT CONTEXT
Business hours: {business_hours}
Website: {website}
Support email: {support_email}
Refund policy: {refund_policy}
Cancellation policy: {cancellation_policy}
Complaint policy: {complaint_policy}
Supported topics: {support_topics}
Approved issue guidance:
{common_issues}
Escalation criteria:
{escalate_triggers}
Configured team: {escalate_to}
Target response time, only after a confirmed escalation: {escalation_wait_time}
"""
CUSTOMER_SUPPORT_PERSONA = CUSTOMER_SUPPORT_OPENINGS["inbound"] + "\n" + CUSTOMER_SUPPORT_BODY


def format_escalate_triggers(triggers: list[str]) -> str:
    """Turn a plain list of escalation triggers into the bulleted
    block the persona expects.
    """
    if not triggers:
        return "  (no specific escalation triggers configured)"
    return "\n".join(f"  - {t}" for t in triggers)


def format_common_issues(issues: list[dict]) -> str:
    """Turn a list of {'issue': str, 'solution': str} dicts into the
    formatted block the persona expects. Tolerates missing keys.
    """
    if not issues:
        return "  (no specific common issues configured)"
    chunks: list[str] = []
    for item in issues:
        issue = item.get("issue", "").strip()
        solution = item.get("solution", "").strip()
        if not issue:
            continue
        chunks.append(f"  Issue: {issue}\n  Solution: {solution}")
    return "\n\n".join(chunks) if chunks else "  (no specific common issues configured)"


REQUIRED_SLOTS = (
    "business_hours",
    "website",
    "support_email",
    "refund_policy",
    "cancellation_policy",
    "complaint_policy",
    "support_topics",
    "common_issues",
    "escalate_triggers",
    "escalate_to",
    "escalation_wait_time",
)
