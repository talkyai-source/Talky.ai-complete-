"""Backend-computed priority and configured deadlines."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.support import priority
from app.domain.services.support.bot.types import Finding
from app.domain.services.support.config import load_settings

CREATED = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
PAYING = {"paying": True}


def compute(category="other", text="Something odd happens", findings=(), account=None):
    return priority.compute_priority(category=category, text=text, findings=findings, account=account)


def test_security_report_is_urgent_in_any_category():
    assert compute("integrations", "I think our account was hacked")[0] == "urgent"
    assert compute("other", "Reporting a security vulnerability in the login page")[0] == "urgent"


def test_account_wide_calling_block_for_a_paying_tenant_is_urgent():
    block = Finding(code="account_suspended", title="t", detail="d", account_wide_block=True)
    assert compute("calls_campaigns", "No calls", (block,), PAYING)[0] == "urgent"


def test_account_wide_block_without_payment_is_not_urgent():
    block = Finding(code="out_of_minutes", title="t", detail="d", account_wide_block=True)
    assert compute("calls_campaigns", "No calls", (block,), {"paying": False})[0] == "normal"
    assert compute("calls_campaigns", "No calls", (block,), None)[0] == "normal"


@pytest.mark.parametrize("category,text", [
    ("billing", "I was charged twice this month"),
    ("billing", "Refund the payment please"),
    ("account_login", "I am locked out of my account"),
    ("account_login", "Can't log in since my 2FA phone broke"),
])
def test_payment_problems_and_lockouts_are_high(category, text):
    assert compute(category, text)[0] == "high"


def test_staff_only_blocker_is_high():
    pending = Finding(code="caller_id_not_verified", title="t", detail="d", staff_only=True)
    assert compute("phone_numbers", "Caller ID stuck", (pending,))[0] == "high"


def test_feature_requests_and_how_to_questions_are_low():
    assert compute("feature_request", "Please add Zoho")[0] == "low"
    assert compute("calls_campaigns", "How do I pause a campaign?")[0] == "low"


def test_how_to_with_a_diagnostic_finding_is_not_low():
    issue = Finding(code="schedule_outside_window", title="t", detail="d")
    assert compute("calls_campaigns", "How do I get calls going?", (issue,))[0] == "normal"


def test_billing_question_without_a_payment_problem_is_normal():
    assert compute("billing", "Which plan includes more agents")[0] == "normal"


def test_everything_else_is_normal():
    assert compute("bug", "The page shows an error")[0] == "normal"


@pytest.mark.parametrize("level,first,resolution", [
    ("urgent", timedelta(hours=1), timedelta(hours=8)),
    ("high", timedelta(hours=4), timedelta(hours=24)),
    ("normal", timedelta(hours=24), timedelta(hours=72)),
    ("low", timedelta(hours=72), timedelta(days=7)),
])
def test_deadlines_come_from_config(level, first, resolution):
    assert priority.deadlines(level, CREATED, load_settings()) == (CREATED + first, CREATED + resolution)


def test_unknown_priority_is_rejected():
    with pytest.raises(ValueError):
        priority.deadlines("critical", CREATED, load_settings())
