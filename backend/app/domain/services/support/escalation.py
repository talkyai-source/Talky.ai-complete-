"""When the bot hands over to a person, and the ticket draft the server builds.

A person is needed when the customer asks for one; the topic is a refund or
billing dispute, data deletion or privacy, account ownership, a legal or
compliance complaint, or a security report; the customer says "not solved"
twice; the model proposes it; the turn has no sources and no diagnostic
finding; or the model failed. The draft is built here from stored facts, never
from raw model JSON.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from app.domain.services.support.bot.types import BotTurn, Finding
from app.domain.services.support.priority import compute_priority
from app.domain.services.support.vocabulary import CATEGORY_IDS

NOT_SOLVED_LIMIT = 2
SUBJECT_MAX = 150
DESCRIPTION_MAX = 5000

# Order matters: the first matching topic names the reason.
_TEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("security_report", re.compile(
        r"\b(security (issue|hole|bug|vulnerab\w*|report|breach|incident)|vulnerab\w*|hack(ed|er|ing)\b|"
        r"breach(ed)?\b|phishing|unauthori[sz]ed (access|login)|account (was |got )?compromised)", re.I)),
    ("legal_or_compliance", re.compile(
        r"\b(lawyer|attorney|legal action|lawsuit|sue you|solicitor|tcpa|ofcom|regulator|compliance complaint|"
        r"formal complaint|court)\b", re.I)),
    ("privacy_or_data_deletion", re.compile(
        r"\b(gdpr|ccpa|delete (all )?(my|our) (data|account|information)|erase (my|our) (data|information)|"
        r"data deletion|right to be forgotten|personal data|privacy (request|complaint)|data subject)\b", re.I)),
    ("account_ownership", re.compile(
        r"\b(account owner(ship)?|ownership of (the |our |my )?account|transfer (the |our |my )?account|"
        r"change (the |our )?(account )?owner|new owner)\b", re.I)),
    ("refund_or_billing_dispute", re.compile(
        r"\b(refund\w*|charge ?back|disput\w*|money back|overcharg\w*|double[- ]?charg\w*|charged twice|"
        r"wrongly charged|unauthori[sz]ed charge)\b", re.I)),
    ("customer_asked_for_person", re.compile(
        r"\b((talk|speak|chat) (to|with) (a |an |some |the )?(human|person|someone|agent|representative|"
        r"support|staff|team|real person)|real (person|human)|human agent|live (agent|person|support)|"
        r"contact (a |the )?(person|human|support team)|escalate)\b", re.I)),
)

_CATEGORY_FOR_REASON = {
    "refund_or_billing_dispute": "billing",
    "privacy_or_data_deletion": "privacy_data",
    "account_ownership": "account_login",
}


def reason_for_text(text: str) -> str | None:
    """Topics and requests that always go to a person, before any model call."""
    for reason, pattern in _TEXT_RULES:
        if pattern.search(text or ""):
            return reason
    return None


def reason_for_turn(turn: BotTurn) -> str | None:
    if turn.error is not None:
        return "bot_unavailable"
    if turn.propose_escalation:
        return "bot_proposed"
    if not turn.sources and not turn.findings and not turn.needs_clarification:
        return "no_answer_found"
    return None


def reason_for_feedback(*, solved: bool, not_solved_count: int) -> str | None:
    """``not_solved_count`` already includes this feedback."""
    return "not_solved_twice" if not solved and not_solved_count >= NOT_SOLVED_LIMIT else None


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip()
    return (cut or text[: limit - 1]) + "…"


def _subject(user_messages: Sequence[str]) -> str:
    for message in user_messages:
        line = _one_line(message)
        if len(line) >= 3:
            return _clip(line, SUBJECT_MAX)
    return "Help needed"


def _section(title: str, lines: Iterable[str]) -> str:
    body = "\n".join(f"- {line}" for line in lines if line)
    return f"{title}\n{body}" if body else ""


def _dedupe(items: Iterable[Any], key) -> list[Any]:
    seen: set[Any] = set()
    unique = []
    for item in items:
        marker = key(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def build_escalation(*, reason: str, user_messages: Sequence[str], turns: Sequence[BotTurn],
                     page_context: Mapping[str, Any], account: Mapping[str, Any] | None,
                     category_hint: str | None, existing_ticket: Mapping[str, Any] | None = None) -> dict:
    """The contract's Escalation object: reason, priority_hint, draft, existing_ticket."""
    findings: list[Finding] = _dedupe((f for turn in turns for f in turn.findings), lambda f: (f.code, f.title))
    traces = _dedupe((t for turn in turns for t in turn.tool_calls), lambda t: (t.name, t.summary))
    answers = [turn.answer for turn in turns if turn.answer and turn.error is None]
    model_categories = [turn.category for turn in turns if turn.category in CATEGORY_IDS and turn.category != "other"]
    category = (category_hint if category_hint in CATEGORY_IDS else None) \
        or _CATEGORY_FOR_REASON.get(reason) or (model_categories[-1] if model_categories else "other")

    problem = [_clip(_one_line(message), 600) for message in user_messages if _one_line(message)]
    parts = [
        _section("Problem:", problem) or "Problem:\n- The customer asked for help from a person.",
        _section("What the assistant checked:", [f"{t.name}: {t.summary}" for t in traces]),
        _section("What the assistant suggested:", [_clip(_one_line(answers[-1]), 600)] if answers else []),
        _section("What the checks found:", [f"{f.title} — {f.detail}" for f in findings]),
    ]
    description = _clip("\n\n".join(part for part in parts if part), DESCRIPTION_MAX)
    subject = _subject(user_messages)

    campaign_id = next((f.related_campaign_id for f in findings if f.related_campaign_id), None) \
        or page_context.get("campaign_id")
    call_id = next((f.related_call_id for f in findings if f.related_call_id), None) or page_context.get("call_id")
    priority_text = " ".join([subject, *problem])
    if reason == "security_report" and not priority_text.strip():
        priority_text = "security report"
    priority_hint, _ = compute_priority(category=category, text=priority_text, findings=findings, account=account)
    if reason == "security_report":
        priority_hint = "urgent"

    draft: dict[str, Any] = {"subject": subject, "category": category, "description": description}
    if campaign_id:
        draft["related_campaign_id"] = str(campaign_id)
    if call_id:
        draft["related_call_id"] = str(call_id)
    return {
        "reason": reason,
        "priority_hint": priority_hint,
        "draft": draft,
        "existing_ticket": dict(existing_ticket) if existing_ticket else None,
    }
