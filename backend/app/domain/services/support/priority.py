"""Ticket priority (computed, never chosen by the customer) and deadlines.

urgent: a security report, or calling blocked for the whole account of a
        paying tenant.
high:   a payment or charge problem, a lockout, or a blocker only staff can
        clear (for example caller-ID verification).
low:    how-to questions and feature requests.
normal: everything else.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from app.domain.services.support.bot.types import Finding
from app.domain.services.support.config import SupportSettings

_SECURITY = re.compile(
    r"\b(security|vulnerab\w*|breach\w*|hack(ed|er|ing)?|compromis\w*|phish\w*|exploit\w*|"
    r"unauthori[sz]ed (access|login|charge)|data leak\w*)\b", re.IGNORECASE)
_PAYMENT = re.compile(
    r"\b(charg(e|ed|es|ing)|refund\w*|payment\w*|invoice\w*|card|billed|overcharg\w*|"
    r"double[- ]?charg\w*|money back|chargeback)\b", re.IGNORECASE)
_LOCKOUT = re.compile(
    r"(locked out|lock(ed)? me out|lockout|account (is )?locked|can'?t (log|sign) ?in|cannot (log|sign) ?in|"
    r"unable to (log|sign) ?in|\b2fa\b|\bmfa\b|authenticator|recovery code)", re.IGNORECASE)
_HOW_TO = re.compile(
    r"^\s*(how (do|can|should) (i|we)|how to|where (do|can) (i|we)|is it possible|can (i|we)|what is the way)\b",
    re.IGNORECASE)


def is_security_report(text: str) -> bool:
    return bool(_SECURITY.search(text or ""))


def compute_priority(*, category: str, text: str, findings: Iterable[Finding] = (),
                     account: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """(priority, reason). ``text`` is subject plus description."""
    findings = tuple(findings)
    text = text or ""
    paying = bool(account and account.get("paying") is True)
    if is_security_report(text):
        return "urgent", "security_report"
    if paying and any(f.account_wide_block for f in findings):
        return "urgent", "account_calling_blocked"
    if category == "billing" and _PAYMENT.search(text):
        return "high", "payment_problem"
    if category == "account_login" and _LOCKOUT.search(text):
        return "high", "lockout"
    if any(f.staff_only for f in findings):
        return "high", "staff_only_blocker"
    if category == "feature_request":
        return "low", "feature_request"
    if not findings and _HOW_TO.search(text):
        return "low", "how_to"
    return "normal", "default"


def deadlines(priority: str, created_at: datetime, settings: SupportSettings) -> tuple[datetime, datetime]:
    """(first_response_due_at, resolution_due_at), anchored to creation time."""
    try:
        first, resolution = settings.sla[priority]
    except KeyError as exc:
        raise ValueError(f"unknown priority {priority!r}") from exc
    return created_at + first, created_at + resolution


def sla_breached(ticket: Mapping[str, Any], now: datetime) -> bool:
    """A live deadline is past, or one was already missed."""
    if ticket.get("first_response_breached_at") or ticket.get("resolution_breached_at"):
        return True
    if ticket["status"] in {"resolved", "closed"}:
        return False
    if ticket.get("first_responded_at") is None and ticket["first_response_due_at"] <= now:
        return True
    return ticket["resolution_due_at"] <= now


def sla_at_risk(ticket: Mapping[str, Any], now: datetime, window: timedelta = timedelta(hours=1)) -> bool:
    """Not breached yet, but a live deadline falls within ``window``."""
    if ticket["status"] in {"resolved", "closed"} or sla_breached(ticket, now):
        return False
    due = [ticket["resolution_due_at"]]
    if ticket.get("first_responded_at") is None:
        due.append(ticket["first_response_due_at"])
    return min(due) <= now + window
