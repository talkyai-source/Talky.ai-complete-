"""Hand-over rules and the server-built ticket draft."""
import pytest

from app.domain.services.support import escalation
from app.domain.services.support.bot.types import BotTurn, Finding, Source, ToolTrace
from app.domain.services.support.vocabulary import CATEGORY_IDS, ESCALATION_REASONS

ANSWERED = BotTurn(answer="Open Billing.", sources=(Source("billing-plans", "Billing", "/docs/billing#plans"),))


@pytest.mark.parametrize("text,reason", [
    ("Can I talk to a human please", "customer_asked_for_person"),
    ("I want to speak to someone from support", "customer_asked_for_person"),
    ("get me a real person", "customer_asked_for_person"),
    ("I need a refund for last month", "refund_or_billing_dispute"),
    ("I am disputing this charge with my bank", "refund_or_billing_dispute"),
    ("Please delete all my data under GDPR", "privacy_or_data_deletion"),
    ("Transfer account ownership to my colleague", "account_ownership"),
    ("Our lawyer says your calls broke TCPA rules", "legal_or_compliance"),
    ("I found a security vulnerability in your API", "security_report"),
    ("Someone hacked our account", "security_report"),
])
def test_text_that_must_go_to_a_person(text, reason):
    assert escalation.reason_for_text(text) == reason
    assert reason in ESCALATION_REASONS


@pytest.mark.parametrize("text", [
    "How do I pause a campaign?",
    "My calls are not going out",
    "Where is the invoice download",  # a how-to, not a dispute
])
def test_ordinary_questions_stay_with_the_bot(text):
    assert escalation.reason_for_text(text) is None


def test_answered_turn_with_sources_does_not_hand_over():
    assert escalation.reason_for_turn(ANSWERED) is None


def test_diagnostic_finding_counts_as_an_answer():
    turn = BotTurn(answer="Calling hours are closed.", findings=(Finding("schedule_outside_window", "t", "d"),))
    assert escalation.reason_for_turn(turn) is None


def test_no_sources_and_no_findings_hands_over():
    assert escalation.reason_for_turn(BotTurn(answer="I am not sure.")) == "no_answer_found"


def test_clarifying_question_is_not_a_failure():
    assert escalation.reason_for_turn(BotTurn(answer="Which campaign?", needs_clarification=True)) is None


def test_model_proposal_and_model_failure_hand_over():
    assert escalation.reason_for_turn(BotTurn(answer="x", propose_escalation=True, sources=ANSWERED.sources)) == "bot_proposed"
    assert escalation.reason_for_turn(BotTurn(answer="x", error="timeout")) == "bot_unavailable"


def test_not_solved_twice_rule():
    assert escalation.reason_for_feedback(solved=False, not_solved_count=1) is None
    assert escalation.reason_for_feedback(solved=False, not_solved_count=2) == "not_solved_twice"
    assert escalation.reason_for_feedback(solved=True, not_solved_count=5) is None


def test_draft_summarises_problem_checks_suggestion_and_findings():
    finding = Finding("caller_id_not_verified", "Caller ID not verified", "Ask staff to verify it.",
                      staff_only=True, related_campaign_id="11111111-1111-1111-1111-111111111111")
    turn = BotTurn(answer="Your caller ID still needs verification.", category="phone_numbers",
                   tool_calls=(ToolTrace("get_phone_numbers_status", "1 number pending verification"),),
                   findings=(finding,))
    result = escalation.build_escalation(
        reason="bot_proposed",
        user_messages=["Calls from campaign Alpha never start", "Still broken"],
        turns=[turn], page_context={"call_id": "22222222-2222-2222-2222-222222222222"},
        account=None, category_hint=None,
    )
    draft = result["draft"]
    assert draft["subject"] == "Calls from campaign Alpha never start"
    assert draft["category"] == "phone_numbers"
    assert "Calls from campaign Alpha never start" in draft["description"]
    assert "get_phone_numbers_status" in draft["description"]
    assert "Your caller ID still needs verification." in draft["description"]
    assert "Caller ID not verified" in draft["description"]
    assert draft["related_campaign_id"] == "11111111-1111-1111-1111-111111111111"
    assert draft["related_call_id"] == "22222222-2222-2222-2222-222222222222"
    assert result["priority_hint"] == "high"  # staff-only blocker
    assert result["reason"] == "bot_proposed"
    assert result["existing_ticket"] is None
    assert 10 <= len(draft["description"]) <= 5000


def test_draft_subject_is_bounded_and_never_empty():
    long = "word " * 200
    result = escalation.build_escalation(reason="customer_requested", user_messages=[long], turns=[],
                                         page_context={}, account=None, category_hint=None)
    assert 3 <= len(result["draft"]["subject"]) <= 150
    empty = escalation.build_escalation(reason="customer_requested", user_messages=[], turns=[],
                                        page_context={}, account=None, category_hint=None)
    assert empty["draft"]["subject"] == "Help needed"
    assert len(empty["draft"]["description"]) >= 10


def test_sensitive_reasons_pick_a_category_when_the_bot_did_not_run():
    refund = escalation.build_escalation(reason="refund_or_billing_dispute", user_messages=["refund me"],
                                         turns=[], page_context={}, account=None, category_hint=None)
    assert refund["draft"]["category"] == "billing"
    assert refund["priority_hint"] == "high"
    privacy = escalation.build_escalation(reason="privacy_or_data_deletion", user_messages=["erase my data"],
                                          turns=[], page_context={}, account=None, category_hint=None)
    assert privacy["draft"]["category"] == "privacy_data"
    security = escalation.build_escalation(reason="security_report", user_messages=["we were hacked"],
                                           turns=[], page_context={}, account=None, category_hint=None)
    assert security["priority_hint"] == "urgent"
    assert all(r["draft"]["category"] in CATEGORY_IDS for r in (refund, privacy, security))


def test_description_is_clipped_to_the_ticket_limit():
    messages = ["x" * 1900] * 10
    result = escalation.build_escalation(reason="no_answer_found", user_messages=messages, turns=[],
                                         page_context={}, account=None, category_hint=None)
    assert len(result["draft"]["description"]) <= 5000
