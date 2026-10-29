"""The one support vocabulary. The migration's CHECK constraints mirror these."""
from __future__ import annotations

from typing import Literal

TicketStatus = Literal["open", "in_progress", "waiting_on_customer", "resolved", "closed"]
TicketPriority = Literal["urgent", "high", "normal", "low"]
ConversationStatus = Literal["active", "resolved_by_bot", "escalated", "abandoned"]
AuthorType = Literal["customer", "staff", "system"]
ArticleSource = Literal["docs", "staff"]
CustomerAction = Literal["reply", "resolve", "reopen", "rate"]

TICKET_STATUSES: tuple[str, ...] = ("open", "in_progress", "waiting_on_customer", "resolved", "closed")
OPEN_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "waiting_on_customer"})
PRIORITIES: tuple[str, ...] = ("urgent", "high", "normal", "low")

# Ordered as the customer sees them; ids are the stored values.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("billing", "Billing and payments"),
    ("calls_campaigns", "Calls and campaigns"),
    ("phone_numbers", "Phone numbers and caller ID"),
    ("ai_agent_voices", "AI agent and voices"),
    ("integrations", "Integrations and connectors"),
    ("account_login", "Account and sign-in"),
    ("bug", "Something is broken"),
    ("feature_request", "Feature request"),
    ("privacy_data", "Privacy and data"),
    ("other", "Something else"),
)
CATEGORY_IDS: frozenset[str] = frozenset(category for category, _ in CATEGORIES)
Category = Literal["billing", "calls_campaigns", "phone_numbers", "ai_agent_voices", "integrations",
                   "account_login", "bug", "feature_request", "privacy_data", "other"]

# Why a conversation was handed to a person. Also the Prometheus label set.
ESCALATION_REASONS: frozenset[str] = frozenset({
    "customer_asked_for_person",
    "refund_or_billing_dispute",
    "privacy_or_data_deletion",
    "account_ownership",
    "legal_or_compliance",
    "security_report",
    "not_solved_twice",
    "bot_proposed",
    "no_answer_found",
    "bot_unavailable",
    "limit_reached",
    "customer_requested",
})

# Attachment types are decided from file content, never from the client.
ATTACHMENT_TYPES: tuple[tuple[str, str], ...] = (
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("webp", "image/webp"),
    ("pdf", "application/pdf"),
    ("txt", "text/plain"),
    ("log", "text/plain"),
    ("csv", "text/csv"),
)
ATTACHMENT_CONTENT_TYPES: frozenset[str] = frozenset(content_type for _, content_type in ATTACHMENT_TYPES)
MAX_ATTACHMENTS_PER_MESSAGE = 5


def ticket_display_number(number: int) -> str:
    """``123`` -> ``SUP-000123``; wider numbers keep every digit."""
    return f"SUP-{int(number):06d}"
