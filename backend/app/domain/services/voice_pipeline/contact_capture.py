"""Contact state and caller-source receipts used by model-driven recording.

Interpretation belongs to record_contact. These models retain the existing
persistence and historical evidence shape without parsing dialogue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

CaptureKind = Literal["email", "phone", "full_name", "company_name"]
CAPTURE_FIELD_TYPES = {"email": "email", "phone": "phone", "full_name": "text", "company_name": "text"}


class CaptureStatus(str, Enum):
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID = "invalid"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ContactSource:
    """Bounded caller evidence identity, never an assertion of human hearing."""
    provider_item_id: str
    caller_turn_order: int
    revision_sha256: str

    def __post_init__(self) -> None:
        if (not isinstance(self.provider_item_id, str) or not self.provider_item_id.strip()
                or len(self.provider_item_id) > 256
                or isinstance(self.caller_turn_order, bool)
                or not isinstance(self.caller_turn_order, int) or self.caller_turn_order < 0
                or not isinstance(self.revision_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.revision_sha256)):
            raise ValueError("invalid contact source identity")


@dataclass(frozen=True)
class ContactReadback:
    utterance_id: str
    status: str = "completed"
    evidence: str = "transport_played"

    def __post_init__(self) -> None:
        if (not isinstance(self.utterance_id, str) or not self.utterance_id
                or len(self.utterance_id) > 256 or self.status != "completed"
                or self.evidence != "transport_played"):
            raise ValueError("invalid contact readback receipt")


@dataclass(frozen=True)
class ContactCaptureState:
    kind: CaptureKind
    status: CaptureStatus
    raw_value: Optional[str] = None
    normalized_value: Optional[str] = None
    validation_status: str = CaptureStatus.NEEDS_CLARIFICATION.value
    confirmed_at: Optional[datetime] = None
    attempts: int = 0
    segments: tuple[str, ...] = ()
    clarification_prompt: Optional[str] = None
    # Caller-owned model recordings may persist before confirmation. Historical
    # scalar-only restores remain unowned until current caller evidence exists.
    from_caller: bool = True
    confirmation_evidence: Optional[str] = None
    value_source: Optional[ContactSource] = None
    confirmation_source: Optional[ContactSource] = None
    status_source: Optional[ContactSource] = None
    readback: Optional[ContactReadback] = None

    def __post_init__(self) -> None:
        # Status is the source of truth; keep the audit string impossible to
        # drift when dataclasses.replace changes a state.
        if self.validation_status != self.status.value:
            object.__setattr__(self, "validation_status", self.status.value)
