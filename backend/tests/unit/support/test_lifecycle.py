"""Ticket status rules: every allowed and blocked change."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.services.support import lifecycle
from app.domain.services.support.errors import InvalidTransition
from app.domain.services.support.vocabulary import TICKET_STATUSES

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def ticket(status, *, assignee=None, resolved_at=None, csat=None):
    return {"status": status, "assignee_user_id": assignee, "resolved_at": resolved_at, "csat_score": csat}


@pytest.mark.parametrize("status,assignee,expected", [
    ("open", None, "open"),
    ("in_progress", "staff", "in_progress"),
    ("waiting_on_customer", "staff", "in_progress"),
    ("waiting_on_customer", None, "open"),
    ("resolved", "staff", "in_progress"),
    ("resolved", None, "open"),
])
def test_customer_reply_moves_waiting_or_resolved_back_to_work(status, assignee, expected):
    resolved_at = NOW - timedelta(days=1) if status == "resolved" else None
    assert lifecycle.after_customer_reply(ticket(status, assignee=assignee, resolved_at=resolved_at), NOW) == expected


def test_customer_cannot_reply_to_a_closed_ticket():
    with pytest.raises(InvalidTransition):
        lifecycle.after_customer_reply(ticket("closed", resolved_at=NOW), NOW)


def test_customer_reply_to_a_resolved_ticket_past_the_reopen_window_is_refused():
    with pytest.raises(InvalidTransition):
        lifecycle.after_customer_reply(ticket("resolved", resolved_at=NOW - timedelta(days=8)), NOW)


@pytest.mark.parametrize("status", ["open", "in_progress", "waiting_on_customer"])
def test_customer_may_resolve_an_open_ticket(status):
    assert lifecycle.customer_resolve(ticket(status)) == "resolved"


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_customer_resolve_on_finished_ticket_is_invalid(status):
    with pytest.raises(InvalidTransition):
        lifecycle.customer_resolve(ticket(status, resolved_at=NOW))


def test_customer_may_reopen_within_seven_days():
    resolved = ticket("resolved", resolved_at=NOW - timedelta(days=6, hours=23))
    assert lifecycle.customer_reopen(resolved, NOW) == "open"
    assert lifecycle.customer_reopen({**resolved, "assignee_user_id": "staff"}, NOW) == "in_progress"


@pytest.mark.parametrize("candidate", [
    ticket("resolved", resolved_at=NOW - timedelta(days=7, seconds=1)),
    ticket("closed", resolved_at=NOW - timedelta(days=1)),
    ticket("open"), ticket("in_progress"), ticket("waiting_on_customer"),
])
def test_customer_reopen_is_refused_outside_the_rule(candidate):
    with pytest.raises(InvalidTransition):
        lifecycle.customer_reopen(candidate, NOW)


def test_rating_needs_a_finished_unrated_ticket():
    assert lifecycle.can_rate(ticket("resolved", resolved_at=NOW))
    assert lifecycle.can_rate(ticket("closed", resolved_at=NOW))
    assert not lifecycle.can_rate(ticket("resolved", resolved_at=NOW, csat=4))
    assert not lifecycle.can_rate(ticket("open"))


ALLOWED_STAFF = {
    ("open", "in_progress"), ("open", "waiting_on_customer"), ("open", "resolved"),
    ("in_progress", "open"), ("in_progress", "waiting_on_customer"), ("in_progress", "resolved"),
    ("waiting_on_customer", "in_progress"), ("waiting_on_customer", "resolved"),
    ("resolved", "in_progress"), ("resolved", "closed"),
}


@pytest.mark.parametrize("current", TICKET_STATUSES)
@pytest.mark.parametrize("target", TICKET_STATUSES)
def test_staff_transition_matrix(current, target):
    if current == target:
        assert lifecycle.staff_transition(current, target) is False  # no-op
    elif (current, target) in ALLOWED_STAFF:
        assert lifecycle.staff_transition(current, target) is True
    else:
        with pytest.raises(InvalidTransition):
            lifecycle.staff_transition(current, target)


def test_closed_is_final_for_everyone():
    for target in TICKET_STATUSES:
        if target != "closed":
            with pytest.raises(InvalidTransition):
                lifecycle.staff_transition("closed", target)


def test_allowed_actions_for_requester():
    assert lifecycle.allowed_actions(ticket("open"), is_requester=True, now=NOW) == ["reply", "resolve"]
    resolved = ticket("resolved", resolved_at=NOW - timedelta(days=1))
    assert lifecycle.allowed_actions(resolved, is_requester=True, now=NOW) == ["reply", "reopen", "rate"]
    stale = ticket("resolved", resolved_at=NOW - timedelta(days=9))
    assert lifecycle.allowed_actions(stale, is_requester=True, now=NOW) == ["rate"]
    assert lifecycle.allowed_actions(ticket("closed", resolved_at=NOW, csat=5), is_requester=True, now=NOW) == []


def test_team_readers_get_no_actions_on_colleagues_tickets():
    assert lifecycle.allowed_actions(ticket("open"), is_requester=False, now=NOW) == []
