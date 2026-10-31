"""Concise lead-generation flow; shared guardrails own facts, actions and voice style."""
from __future__ import annotations

from app.services.scripts.prompts.policies import load_policy

from collections.abc import Mapping

# Historical "inbound" key means caller-first OUTBOUND, not carrier inbound.
LEAD_GEN_OPENINGS: dict[str, str] = {
    "outbound": "OPENING CONTEXT\nThis is an outbound call about {call_reason}. Introduce {agent_name} and {company_name} naturally if needed, then listen.\n",
    "inbound": "OPENING CONTEXT\nThis is an outbound call; the recipient speaks first. Explain the purpose ({call_reason}) naturally when relevant.\n",
}

LEAD_GEN_PLAYBOOK = load_policy("personas/lead_gen")


# ── Slot-based body: shared playbook + campaign positioning ──────────────────
LEAD_GEN_BODY = LEAD_GEN_PLAYBOOK + """
CAMPAIGN CONTEXT
Services: {services_description}
Value proposition: {value_proposition}
Intended audience and area: {industry}; {coverage_area}
Useful qualification topics, only when relevant:
{qualification_questions}
Fit limitations: {disqualifying_answers}
Suggested next step, subject to available tools: {calendar_booking_type}
{campaign_controls}
"""


# ── Knowledge-first body: opening context + shared guide, no content slots ─
def lead_gen_kd_body(opening_key: str = "outbound") -> str:
    """Knowledge-driven lead_gen body: the opening context for ``opening_key``
    ("outbound" = agent opens, "inbound" = callee says hello first) followed by
    the playbook. The opening used to be a private copy of the agent-first text,
    so a callee-first knowledge-driven call carried two contradictory openers.
    ``{call_reason}`` has no slot on this path; the shape line points at the
    campaign guidance instead."""
    opening = LEAD_GEN_OPENINGS[opening_key if opening_key in LEAD_GEN_OPENINGS else "outbound"]
    return opening + "\n" + LEAD_GEN_PLAYBOOK


# Backward-compat: the agent-first knowledge-driven body as a constant, for
# callers and tests that import it directly.
LEAD_GEN_KD_BODY = lead_gen_kd_body("outbound")


# Backward-compat alias (full outbound template) for callers that import
# LEAD_GEN_PERSONA directly without going through the direction-aware composer.
# Optional controls require the composer's formatter, so the legacy template
# leaves that block empty rather than introducing a new required placeholder.
LEAD_GEN_PERSONA = (
    LEAD_GEN_OPENINGS["outbound"] + "\n" + LEAD_GEN_BODY
).replace("{campaign_controls}", "")


def format_qualification_questions(questions: list[str]) -> str:
    """Turn a plain list of qualification questions into the bulleted block the
    persona expects. Returns a safe placeholder for an empty list so
    str.format keeps working."""
    if not questions:
        return "  (no specific qualification questions configured — qualify on need, fit, timing)"
    return "\n".join(f"  - {q}" for q in questions)


def _plain_campaign_value(value: object) -> str:
    """Render one optional campaign control without inventing a default."""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def format_lead_gen_campaign_controls(slots: Mapping[str, object]) -> str:
    """Render optional, operator-approved facts only when they are configured.

    These fields came from the generic lead-generation specification, but fit
    Talk-Leee's existing ``campaign_slots`` contract without pretending that a
    booking/transfer tool exists. Keeping them in one compact block avoids
    duplicating the standing objection and fact-grounding rules.
    """
    lines: list[str] = []
    for key, label in (
        ("company_differentiator", "Approved differentiator"),
        ("approved_offer", "Approved offer or incentive"),
        (
            "approved_data_source_explanation",
            "If asked how the contact was obtained, answer",
        ),
        ("restricted_claims", "Restricted claims or topics"),
    ):
        rendered = _plain_campaign_value(slots.get(key))
        if rendered:
            lines.append(f"- {label}: {rendered}")

    objection_lines: list[str] = []
    raw_objections = slots.get("approved_objection_responses")
    if isinstance(raw_objections, Mapping):
        candidates = [
            {"objection": objection, "response": response}
            for objection, response in raw_objections.items()
        ]
    elif isinstance(raw_objections, (list, tuple)):
        candidates = list(raw_objections)
    else:
        candidates = []

    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        objection = _plain_campaign_value(
            item.get("objection") or item.get("issue") or item.get("name")
        )
        response = _plain_campaign_value(
            item.get("response") or item.get("answer") or item.get("solution")
        )
        if objection and response:
            objection_lines.append(f"  - {objection} → {response}")

    if objection_lines:
        lines.append("- Approved objection replies (use only for a matching concern):")
        lines.extend(objection_lines)

    if not lines:
        return ""
    return (
        "\nCAMPAIGN-SPECIFIC APPROVALS\n"
        + "\n".join(lines)
        + "\nUse these only as written. They never authorize a stronger claim, "
        "and Company knowledge wins on any factual conflict."
    )


# Pricing / coverage specifics now come from the Company knowledge (RAG), so
# pricing_info and company_differentiator are no longer required slots.
REQUIRED_SLOTS = (
    "industry",
    "services_description",
    "coverage_area",
    "value_proposition",
    "call_reason",
    "qualification_questions",
    "disqualifying_answers",
    "calendar_booking_type",
)
