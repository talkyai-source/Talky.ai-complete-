"""Bounded-cardinality support metrics.

Labels come only from code-owned vocabularies; tenant, user, ticket ids and
message text never become labels. Unknown values collapse to ``other``.
"""
from __future__ import annotations

from typing import Any, Optional

from prometheus_client import REGISTRY, Counter

from app.domain.services.support.vocabulary import CATEGORY_IDS, ESCALATION_REASONS, PRIORITIES

_ERROR_KINDS = frozenset({"timeout", "provider_error", "invalid_output", "not_configured", "storage"})
_SLA_TARGETS = frozenset({"first_response", "resolution"})


def _existing(name: str) -> Optional[Any]:
    return getattr(REGISTRY, "_names_to_collectors", {}).get(name)


def _counter(name: str, documentation: str, labels: tuple[str, ...] = ()) -> Counter:
    existing = _existing(name)
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, documentation, labelnames=labels)


BOT_TURNS = _counter("support_bot_turns_total", "Support bot turns answered (model ran or was attempted).")
BOT_RESOLVED = _counter("support_bot_resolved_total", "Conversations the customer marked solved by the bot.")
ESCALATIONS = _counter("support_escalations_total", "Hand-overs from the bot to a ticket, by reason.", ("reason",))
TICKETS_CREATED = _counter("support_tickets_created_total", "Support tickets created.", ("category", "priority"))
BOT_ERRORS = _counter("support_bot_errors_total", "Support bot failures, by kind.", ("kind",))
SLA_BREACHES = _counter("support_sla_breaches_total", "Support deadlines missed, by target.", ("target",))


def _bounded(value: str | None, allowed: frozenset[str] | tuple[str, ...]) -> str:
    return value if value in allowed else "other"


def bot_turn() -> None:
    BOT_TURNS.inc()


def bot_resolved() -> None:
    BOT_RESOLVED.inc()


def escalation(reason: str) -> None:
    ESCALATIONS.labels(reason=_bounded(reason, ESCALATION_REASONS)).inc()


def ticket_created(category: str, priority: str) -> None:
    TICKETS_CREATED.labels(category=_bounded(category, CATEGORY_IDS), priority=_bounded(priority, PRIORITIES)).inc()


def bot_error(kind: str) -> None:
    BOT_ERRORS.labels(kind=_bounded(kind, _ERROR_KINDS)).inc()


def sla_breach(target: str) -> None:
    SLA_BREACHES.labels(target=_bounded(target, _SLA_TARGETS)).inc()
