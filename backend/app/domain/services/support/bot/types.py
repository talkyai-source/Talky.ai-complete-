"""Contract between the support bot and the conversation service.

The bot only reads. It never writes tickets or changes account state; the
conversation service decides what to store and whether to hand over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BotErrorKind = Literal["timeout", "provider_error", "invalid_output", "not_configured"]


@dataclass(frozen=True)
class Source:
    """A help article the answer is based on (only ones a tool returned)."""
    slug: str
    title: str
    url: str


@dataclass(frozen=True)
class ToolTrace:
    """What was checked, for storage and the ticket draft. No secrets, no PII."""
    name: str
    summary: str  # <= 200 chars


@dataclass(frozen=True)
class Finding:
    """A diagnostic fact found by a read-only check.

    ``account_wide_block``: calling is blocked for the whole account.
    ``staff_only``: only platform staff can clear it (e.g. caller-ID review).
    """
    code: str
    title: str
    detail: str
    staff_only: bool = False
    account_wide_block: bool = False
    related_campaign_id: str | None = None
    related_call_id: str | None = None


@dataclass(frozen=True)
class BotRequest:
    tenant_id: str
    user_id: str
    user_text: str
    # Earlier stored messages, oldest first: ("user" | "assistant", content).
    history: tuple[tuple[str, str], ...] = ()
    # Server-validated page context: route, campaign_id, call_id (ids already
    # checked against the tenant or dropped).
    page_context: dict[str, Any] = field(default_factory=dict)
    model: str = ""


@dataclass(frozen=True)
class BotTurn:
    answer: str
    sources: tuple[Source, ...] = ()
    resolution_prompt: bool = False
    needs_clarification: bool = False
    propose_escalation: bool = False
    escalation_reason: str | None = None  # model's short reason, <= 200 chars
    category: str = "other"               # always one of vocabulary.CATEGORY_IDS
    tool_calls: tuple[ToolTrace, ...] = ()
    findings: tuple[Finding, ...] = ()
    # Snapshot from get_account_overview when it ran: {"paying": bool, ...}.
    account: dict[str, Any] | None = None
    model: str | None = None
    latency_ms: int = 0
    error: BotErrorKind | None = None
