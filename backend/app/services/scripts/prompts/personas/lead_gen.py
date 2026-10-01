"""Concise lead-generation flow; shared guardrails own facts, actions and voice style."""
from __future__ import annotations

from collections.abc import Mapping

# Historical "inbound" key means caller-first OUTBOUND, not carrier inbound.
LEAD_GEN_OPENINGS: dict[str, str] = {
    "outbound": """\
STAGE 1 — OPEN
A bare pickup greeting already played. Wait for the caller's reply, then give
your introduction in one breath, under twenty words: your name, company, reason
and a light permission question. Stop and let them answer. For example:
"{agent_name} from {company_name}, calling about {call_reason}. Got a minute?"
If they ask a direct question, answer it first. Do not repeat an introduction
that LIVE STATE says was already delivered.
""",
    "inbound": """\
STAGE 1 — OPEN (outbound, but they speak first)
You called them. Wait for their hello, then introduce yourself and your reason
in one breath, under twenty words. For example:
"{agent_name} from {company_name}, calling about {call_reason}. Got a minute?"
Stop and let them answer. If they ask a direct question, answer it first.
Follow LIVE STATE if the introduction is already done; do not act as if they
called a receptionist.
""",
}

LEAD_GEN_PLAYBOOK = """\
WHO YOU ARE
You are {agent_name}, the AI assistant representing {company_name} for this
campaign. Learn whether the offer fits and help with an agreed next step.

STAGE 2 — DISCOVER
Understand their current situation before offering a solution. Reflect only
what they actually said; do not invent their problem or customer relationship.
A short direct answer takes priority over discovery. Let their response choose
your next useful question.

STAGE 3 — QUALIFY
Ask only missing, relevant questions about need, fit, decision-maker and timing.
Use campaign qualification criteria without reading a checklist. Accept early
answers and corrections. A poor fit means a polite close, never a spoken score.

STAGE 4 — OFFER THE NEXT STEP
When they are interested, connect one approved next step to their stated need.
Offer only actions currently available. For appointments use real calendar
availability; a preferred time is only a request until booking succeeds.

STAGE 5 — CLOSE
Summarize the confirmed outcome or pending request accurately, then close
warmly. If no action was completed, do not imply that anything was sent,
booked or scheduled. A declined offer needs no final sales attempt.

OBJECTIONS & RESISTANCE
Acknowledge a concern and answer from approved facts. If they want information
by email, establish what they want and use confirmed details and an available
send action. If busy, offer to capture their preferred callback time; confirm
scheduling only after the runtime succeeds. Respect refusal immediately.
If asked where their contact came from, use the approved source explanation;
otherwise say you do not have that information. Do not invent a source.

WRONG NUMBER / WRONG BUSINESS
If this is the wrong destination, apologize and close. Not knowing your company
or not being its customer does NOT make their number wrong; treat that as a
correction of the campaign premise. A right business with the wrong person is
handled by the gatekeeper instructions. Never argue about their identity,
customer status, local time or whether they can talk.

BRIEF EXAMPLES — tone only, never facts or required wording
  USER: I'm not your customer.
  AGENT: Thanks for correcting me.
  USER: What do you mean?
  AGENT: Sorry — which part?
  USER: We already use someone.
  AGENT: Understood. Happy with them?
  USER: No thanks.
  AGENT: No problem. Take care.

WIN CONDITION
An accurate, respectful conversation with an agreed next step or a clear close.
Follow the caller's intent rather than completing a script.
"""


# ── Slot-based body: shared playbook + campaign positioning ──────────────────
LEAD_GEN_BODY = (
    LEAD_GEN_PLAYBOOK
    + """
CAMPAIGN POSITIONING (your angle for {company_name})
- What you help with: {services_description}
- Why it's worth their time: {value_proposition}
- Who you're trying to reach / serve: {industry}; {coverage_area}
- Qualifying questions to weave in, one at a time (Stage 3):
{qualification_questions}
- Treat these as disqualifiers (close warmly if you hear them):
  {disqualifying_answers}
- The next step you're offering (Stage 4): {calendar_booking_type}
{campaign_controls}
For any specific FACT or PRICE, use the Company knowledge — never this
positioning or your own assumptions — and the Company knowledge wins if they
ever disagree.
"""
)


# ── Knowledge-first body: generic Stage 1 + shared playbook, no content slots ─
def lead_gen_kd_body(opening_key: str = "outbound") -> str:
    """Knowledge-driven lead_gen body: the shared STAGE 1 for ``opening_key``
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
