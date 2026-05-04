"""Pydantic request / response models for /auth/mfa endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MFASetupResponse(BaseModel):
    """Returned by POST /auth/mfa/setup — contains QR code data for the authenticator app."""

    provisioning_uri: str
    qr_code: str  # data:image/png;base64,... — embed directly in <img>
    issuer: str
    account: str


class MFAConfirmRequest(BaseModel):
    """Confirm MFA setup by submitting the first valid TOTP code."""

    code: str


class MFAConfirmResponse(BaseModel):
    """
    Returned once when MFA is successfully activated.
    recovery_codes are shown EXACTLY ONCE — the user must save them.
    """

    enabled: bool
    recovery_codes: list[str]  # formatted (e.g. "AbCdEfGh-IjKlMnOp")
    recovery_codes_count: int
    message: str


class MFAChallengeVerifyRequest(BaseModel):
    """
    Step-2 of the two-step login flow.
    Present the challenge token (from POST /auth/login) + TOTP code.
    Alternatively supply recovery_code instead of code.
    """

    challenge_token: str
    code: Optional[str] = None  # TOTP code (6 digits)
    recovery_code: Optional[str] = None  # backup code


class MFAChallengeVerifyResponse(BaseModel):
    """Full auth response returned after successful MFA challenge completion."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    role: str
    business_name: Optional[str]
    minutes_remaining: int
    mfa_verified: bool = True
    message: str


class MFAStatusResponse(BaseModel):
    enabled: bool
    verified_at: Optional[datetime]
    recovery_codes_remaining: int


class MFADisableRequest(BaseModel):
    """Requires current password to disable MFA (OWASP: reauthentication before disabling MFA)."""

    password: str


class MFARegenerateCodesRequest(BaseModel):
    """Requires a valid current TOTP code to regenerate recovery codes."""

    code: str


class MFARegenerateCodesResponse(BaseModel):
    recovery_codes: list[str]
    recovery_codes_count: int
    message: str
