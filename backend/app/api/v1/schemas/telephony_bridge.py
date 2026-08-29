"""Telephony bridge API schemas."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.domain.services.telephony.transfer_validation import (
    canonicalize_transfer_destination,
    validate_any_transfer_destination,
    validate_transfer_call_id,
)


class TransferPayload(BaseModel):
    """Request body for PBX call transfer operations."""

    call_id: str = Field(..., description="PBX call / channel UUID")
    destination: str = Field(..., description="Transfer destination")
    mode: Optional[Literal["blind", "attended", "deflect"]] = Field(default=None)

    @model_validator(mode="after")
    def validate_command_arguments(self) -> "TransferPayload":
        self.call_id = validate_transfer_call_id(self.call_id)
        if self.mode is None:
            self.destination = validate_any_transfer_destination(self.destination)
        else:
            self.destination = canonicalize_transfer_destination(
                self.destination,
                self.mode,
            )
        return self
