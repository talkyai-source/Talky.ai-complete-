"""Telephony bridge API schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TransferPayload(BaseModel):
    """Request body for PBX call transfer operations."""

    call_id: str = Field(..., description="PBX call / channel UUID")
    destination: str = Field(..., description="Transfer destination")
    mode: Literal["blind", "attended", "deflect"] = Field(default="blind")
