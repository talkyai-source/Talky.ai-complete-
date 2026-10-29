"""Ticket status rules (pure).

open -> in_progress -> waiting_on_customer -> resolved -> closed. A customer
reply sends waiting_on_customer or resolved back to work (in_progress when
someone is assigned, else open). The customer may resolve, and may reopen
within seven days of resolution. Closed is final for everyone.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from app.domain.services.support.errors import InvalidTransition
from app.domain.services.support.vocabulary import OPEN_STATUSES

REOPEN_WINDOW = timedelta(days=7)

_STAFF_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "waiting_on_customer", "resolved"}),
    "in_progress": frozenset({"open", "waiting_on_customer", "resolved"}),
    "waiting_on_customer": frozenset({"in_progress", "resolved"}),
    "resolved": frozenset({"in_progress", "closed"}),
    "closed": frozenset(),
}


def _back_to_work(ticket: Mapping[str, Any]) -> str:
    return "in_progress" if ticket.get("assignee_user_id") else "open"


def _within_reopen_window(ticket: Mapping[str, Any], now: datetime) -> bool:
    resolved_at = ticket.get("resolved_at")
    return resolved_at is not None and now - resolved_at <= REOPEN_WINDOW


def after_customer_reply(ticket: Mapping[str, Any], now: datetime) -> str:
    """Status after a customer's public reply."""
    status = ticket["status"]
    if status == "closed":
        raise InvalidTransition("This ticket is closed. Open a new ticket instead.")
    if status == "resolved":
        if not _within_reopen_window(ticket, now):
            raise InvalidTransition("This ticket was resolved more than 7 days ago. Open a new ticket instead.")
        return _back_to_work(ticket)
    if status == "waiting_on_customer":
        return _back_to_work(ticket)
    return status


def customer_resolve(ticket: Mapping[str, Any]) -> str:
    if ticket["status"] not in OPEN_STATUSES:
        raise InvalidTransition("Only an open ticket can be marked resolved.")
    return "resolved"


def customer_reopen(ticket: Mapping[str, Any], now: datetime) -> str:
    if ticket["status"] != "resolved" or not _within_reopen_window(ticket, now):
        raise InvalidTransition("A ticket can be reopened only within 7 days of being resolved.")
    return _back_to_work(ticket)


def can_rate(ticket: Mapping[str, Any]) -> bool:
    return ticket["status"] in {"resolved", "closed"} and ticket.get("csat_score") is None


def staff_transition(current: str, target: str) -> bool:
    """True when the status changes, False for a no-op; raises when not allowed."""
    if current == target:
        return False
    if target not in _STAFF_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"A ticket cannot move from {current} to {target}.")
    return True


def allowed_actions(ticket: Mapping[str, Any], *, is_requester: bool, now: datetime) -> list[str]:
    """Customer actions in a stable order. Team readers only read."""
    if not is_requester:
        return []
    status = ticket["status"]
    actions: list[str] = []
    if status in OPEN_STATUSES or (status == "resolved" and _within_reopen_window(ticket, now)):
        actions.append("reply")
    if status in OPEN_STATUSES:
        actions.append("resolve")
    if status == "resolved" and _within_reopen_window(ticket, now):
        actions.append("reopen")
    if can_rate(ticket):
        actions.append("rate")
    return actions
