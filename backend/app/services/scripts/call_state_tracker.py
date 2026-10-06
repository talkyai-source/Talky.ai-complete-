"""Immutable contact slots shared by recording, persistence and prompt facts."""
from dataclasses import dataclass
from typing import Optional

from app.domain.services.voice_pipeline.contact_capture import (
    CaptureKind, CaptureStatus, ContactCaptureState,
)


@dataclass(frozen=True)
class CallState:
    """Immutable contact slots, retaining historical storage fields."""
    email: Optional[str] = None
    email_confirmed: bool = False
    # Historical counters remain readable; record_contact does not run retry loops.
    email_readback_attempts: int = 0
    phone: Optional[str] = None
    phone_confirmed: bool = False
    phone_readback_attempts: int = 0
    # Source, validation and confirmation metadata belongs to each contact.
    email_capture: Optional[ContactCaptureState] = None
    phone_capture: Optional[ContactCaptureState] = None
    earlier_email_captures: tuple = ()
    earlier_phone_captures: tuple = ()
    # Historical context fields are retained for existing snapshots/persistence.
    # Live dialogue interpretation and ordering belong to the model.
    agent_asked_kind: Optional[CaptureKind] = None
    active_contact_kind: Optional[CaptureKind] = None
    follow_up: Optional[str] = None
    project_type: Optional[str] = None
    bidding_active: Optional[bool] = None
    declined_count: int = 0
    contact_ask_objections: int = 0
    contact_capture_paused: bool = False
    caller_asked_question: bool = False
    # Known call line is context, not caller-confirmed preferred contact.
    line_phone: Optional[str] = None

    def __post_init__(self) -> None:
        # Lift historical scalar slots into the existing state shape without
        # granting caller ownership. record_contact preserves an untouched field
        # when applying current model-directed changes.
        if self.email and (
            self.email_capture is None
            or self.email_capture.normalized_value != self.email
        ):
            status = (
                CaptureStatus.CONFIRMED
                if self.email_confirmed
                else CaptureStatus.AWAITING_CONFIRMATION
            )
            object.__setattr__(
                self,
                "email_capture",
                ContactCaptureState(
                    kind="email",
                    status=status,
                    raw_value=self.email,
                    normalized_value=self.email,
                    validation_status=status.value,
                    attempts=self.email_readback_attempts,
                    from_caller=False,
                    segments=tuple(self.email.split("@", 1)) if "@" in self.email else (),
                ),
            )
        if self.phone and (
            self.phone_capture is None
            or self.phone_capture.normalized_value != self.phone
        ):
            status = (
                CaptureStatus.CONFIRMED
                if self.phone_confirmed
                else CaptureStatus.AWAITING_CONFIRMATION
            )
            object.__setattr__(
                self,
                "phone_capture",
                ContactCaptureState(
                    kind="phone",
                    status=status,
                    raw_value=self.phone,
                    normalized_value=self.phone,
                    validation_status=status.value,
                    attempts=self.phone_readback_attempts,
                    segments=(self.phone,),
                    from_caller=False,
                ),
            )
