"""Request / response Pydantic models for /auth endpoints."""
from __future__ import annotations

from typing import Annotated, Literal, Optional
import unicodedata

from pydantic import AfterValidator, BaseModel, EmailStr, Field


def _validate_account_name(value: str) -> str:
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("Names must not contain control characters.")
    return value


AccountName = Annotated[str, Field(max_length=100), AfterValidator(_validate_account_name)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    business_name: AccountName
    name: Optional[AccountName] = None
    # NOTE: plan_id is NOT a user-controlled field. The frontend signup
    # form has been observed sending the user's first-name string
    # (or other adjacent field values) into a `plan_id` slot by mistake.
    # All new signups land on the `free` plan unconditionally; plan
    # changes happen later from the dashboard. Anything the frontend
    # sends in `plan_id` is silently ignored — see register() body.
    model_config = {"extra": "ignore"}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: Optional[str] = Field(default=None, min_length=1, max_length=2048)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: Optional[str] = None
    email: str
    role: str
    business_name: Optional[str] = None
    minutes_remaining: Optional[int] = None
    minutes_state: Literal["known", "unlimited", "unavailable"] = "unavailable"
    message: str
    # MFA two-step login fields (only present when mfa_required=True)
    mfa_required: bool = False
    mfa_challenge_token: Optional[str] = None


class RegisterResponse(BaseModel):
    """
    Response from POST /auth/register.

    Returns NO access_token by design: the user must verify their email
    before they can log in. Issuing a session here would let an attacker
    register with someone else's address and immediately exercise full
    tenant_admin powers without ever proving inbox ownership.
    """
    user_id: str
    email: str
    business_name: str
    verification_required: bool = True
    verification_email_sent: bool
    message: str


class MeResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    account_timezone: Optional[str] = None
    role: str
    legal_acceptance_required: bool = False
    terms_version: Optional[str] = None
    privacy_version: Optional[str] = None
    minutes_remaining: Optional[int] = None
    minutes_state: Literal["known", "unlimited", "unavailable"] = "unavailable"

    # Suspension fields — sourced from tenants + white_label_partners.
    # Returning these on /auth/me lets the frontend's SuspensionStateProvider
    # derive suspension state from the single AuthContext.user instead of
    # firing its own parallel /auth/me query. See plan Phase 1.
    #
    # All fields are nullable: a user without a tenant (rare — e.g. a
    # platform_admin) has no tenant_status. partner_* are null when the
    # tenant isn't enrolled with a white-label partner.
    partner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    partner_status: Optional[str] = None        # "active" | "suspended" | null
    tenant_status: Optional[str] = None         # "active" | "suspended" | null
    suspended_scope: Optional[str] = None       # "tenant" | "partner" | null
    suspension_reason: Optional[str] = None
    suspended_at: Optional[str] = None          # ISO 8601


class UpdateMeRequest(BaseModel):
    name: Optional[AccountName] = None
    business_name: Optional[AccountName] = None


class LegalAcceptanceRequest(BaseModel):
    terms_version: str = Field(..., min_length=1, max_length=128)
    privacy_version: str = Field(..., min_length=1, max_length=128)
    model_config = {"extra": "forbid"}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    message: str
    email: str


class SignupStartRequest(BaseModel):
    name: AccountName
    business_name: AccountName
    email: EmailStr
    turnstile_token: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    # Strict — refuse any extra fields. Frontend MUST NOT send password,
    # plan_id, or anything else here. The point of step 1 is to verify
    # the email BEFORE collecting the password.
    model_config = {"extra": "forbid"}


class SignupStartResponse(BaseModel):
    message: str
    expires_in_minutes: int
    email: EmailStr


class SignupVerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str
    model_config = {"extra": "forbid"}


class SignupVerifyCodeResponse(BaseModel):
    message: str
    email: EmailStr


class SignupCompleteRequest(BaseModel):
    email: EmailStr
    code: str
    password: str
    confirm_password: str
    terms_version: Optional[str] = Field(None, min_length=1, max_length=128)
    privacy_version: Optional[str] = Field(None, min_length=1, max_length=128)
    model_config = {"extra": "forbid"}
