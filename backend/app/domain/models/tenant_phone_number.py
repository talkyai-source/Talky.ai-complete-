"""Tenant phone number (verified DID) domain model.

Backs the `tenant_phone_numbers` table created by migration
20260425_add_tenant_phone_numbers.sql. A row here is the authority on
whether a tenant may originate a call FROM a given E.164 number.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PhoneNumberStatus(str, Enum):
    """Verified-DID lifecycle states."""
    PENDING = "pending_verification"
    VERIFIED = "verified"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class VerificationMethod(str, Enum):
    """How a number was verified. Durable audit trail — never dropped."""
    SMS_CODE = "sms_code"
    CARRIER_API = "carrier_api"
    MANUAL_ADMIN = "manual_admin"
    LETTER_OF_AUTHORIZATION = "letter_of_authorization"


class TenantPhoneNumber(BaseModel):
    """A number a tenant is allowed to dial FROM. `status='verified'` is
    the gate — anything else refuses origination.
    """
    id: str
    tenant_id: str
    e164: str
    provider: str = "manual_admin"
    status: PhoneNumberStatus = PhoneNumberStatus.PENDING
    verification_method: Optional[VerificationMethod] = None
    verification_sent_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    stir_shaken_token: Optional[str] = Field(
        default=None,
        description=(
            "Attestation token returned by the upstream carrier (Twilio, "
            "Telnyx, Bandwidth). NULL = test-only — production refuses to "
            "originate when NULL."
        ),
    )
    label: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"use_enum_values": True}

    def is_dialable_in_production(self) -> bool:
        """True only when the number is verified AND has a real attestation
        token. Use this at the enforcement layer in prod."""
        if self.status != PhoneNumberStatus.VERIFIED.value:
            return False
        return bool(self.stir_shaken_token)
