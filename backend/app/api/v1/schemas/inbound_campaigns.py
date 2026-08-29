"""HTTP contracts for strict inbound campaign administration."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _normalize_e164(value: str) -> str:
    cleaned = value.strip()
    if not _E164_RE.fullmatch(cleaned):
        raise ValueError("DID must be strict E.164 (+ followed by 7-15 digits)")
    return cleaned


class InboundCampaignCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=255)
    did_number: str
    campaign_id: str
    sip_trunk_id: str
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    after_hours_action: Literal["hangup", "voicemail", "transfer"] = "hangup"
    transfer_number: Optional[str] = Field(default=None, max_length=32)
    recording_enabled: bool = False
    consent_message: Optional[str] = Field(default=None, max_length=2000)
    opening_mode: Literal["caller_first", "agent_first"] = "caller_first"
    greeting: Optional[str] = Field(default=None, max_length=2000)
    business_hours: dict[str, Any] = Field(default_factory=dict)
    recording_policy: dict[str, Any] = Field(default_factory=dict)
    transfer_policy: dict[str, Any] = Field(default_factory=dict)
    qualification_config: dict[str, Any] = Field(default_factory=dict)

    _did = field_validator("did_number")(_normalize_e164)

    @field_validator("transfer_number")
    @classmethod
    def _optional_transfer_number(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return _normalize_e164(value)

    @field_validator("name", "timezone")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _policy_requirements(self):
        if self.after_hours_action == "transfer" and not (self.transfer_number or "").strip():
            raise ValueError("transfer_number is required for after-hours transfer")
        if self.recording_enabled and not (self.consent_message or "").strip():
            raise ValueError("consent_message is required when recording is enabled")
        return self


class InboundCampaignUpdateRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    did_number: Optional[str] = None
    campaign_id: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    timezone: Optional[str] = Field(default=None, min_length=1, max_length=64)
    after_hours_action: Optional[Literal["hangup", "voicemail", "transfer"]] = None
    transfer_number: Optional[str] = Field(default=None, max_length=32)
    recording_enabled: Optional[bool] = None
    consent_message: Optional[str] = Field(default=None, max_length=2000)
    opening_mode: Optional[Literal["caller_first", "agent_first"]] = None
    greeting: Optional[str] = Field(default=None, max_length=2000)
    business_hours: Optional[dict[str, Any]] = None
    recording_policy: Optional[dict[str, Any]] = None
    transfer_policy: Optional[dict[str, Any]] = None
    qualification_config: Optional[dict[str, Any]] = None

    @field_validator("did_number")
    @classmethod
    def _optional_did(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_e164(value) if value is not None else None

    @field_validator("transfer_number")
    @classmethod
    def _optional_transfer_number(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        return _normalize_e164(value)

    @field_validator("name", "timezone")
    @classmethod
    def _strip_optional(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


class InboundVersionRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)


class InboundDidAssignmentRequest(_StrictModel):
    did_number: str
    sip_trunk_id: str
    expected_version: int = Field(ge=1)
    reason: Optional[str] = Field(default=None, max_length=1000)

    _did = field_validator("did_number")(_normalize_e164)


class TenantInboundControlsPatch(_StrictModel):
    inbound_enabled: bool
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class InboundReadinessCheck(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str


class InboundReadinessBlocker(BaseModel):
    code: str
    message: str
    remediation: str


class InboundReadiness(BaseModel):
    ready: bool
    checks: list[InboundReadinessCheck]
    blockers: list[InboundReadinessBlocker] = Field(default_factory=list)


class InboundCampaignResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    status: str
    version: int
    config_version: int
    config_checksum: str
    did_number: str
    assignment_id: str
    assignment_status: str
    assignment_version: int
    campaign_id: str
    campaign_name: Optional[str] = None
    sip_trunk_id: str
    sip_trunk_name: Optional[str] = None
    timezone: str
    after_hours_action: Literal["hangup", "voicemail", "transfer"]
    transfer_number: Optional[str] = None
    recording_enabled: bool
    consent_message: Optional[str] = None
    opening_mode: Literal["caller_first", "agent_first"]
    greeting: Optional[str] = None
    business_hours: dict[str, Any]
    recording_policy: dict[str, Any]
    transfer_policy: dict[str, Any]
    qualification_config: dict[str, Any]
    readiness: InboundReadiness
    last_call_at: Optional[datetime] = None
    last_error: Optional[str] = None
    active_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class InboundCampaignListResponse(BaseModel):
    items: list[InboundCampaignResponse]
    total: int


class InboundDidAvailabilityResponse(BaseModel):
    did_number: str
    available: bool
    reason: str
    owned_by_current_tenant: bool = False
    conflicting_assignment_id: Optional[str] = None


class TenantInboundControlsResponse(BaseModel):
    inbound_enabled: bool
    version: int
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None


class InboundRuntimeCapabilitiesResponse(BaseModel):
    transfer_runtime_available: bool
    transfer_platform_enabled: bool
    transfer_configuration_available: bool


class PlatformInboundControlsPatch(_StrictModel):
    inbound_enabled: bool
    recording_enabled: bool
    transfer_enabled: bool
    settlement_enabled: bool
    reason: str = Field(min_length=3, max_length=1000)
    expected_version: int = Field(ge=1)


class PlatformInboundControlsResponse(BaseModel):
    inbound_enabled: bool
    recording_enabled: bool
    transfer_enabled: bool
    settlement_enabled: bool
    version: int
    reason: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class AdminAssignmentVersionRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class AdminInboundBillingHoldResolutionRequest(_StrictModel):
    hold_reason: Literal[
        "provider_answer_ambiguous",
        "usage_exceeded_reservation",
    ]
    decision: Literal["release_unanswered", "finalize"]
    evidence_type: Literal["carrier_cdr", "provider_usage_record"]
    evidence_reference: str = Field(min_length=3, max_length=255)
    evidence_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    adjudication_reason: str = Field(min_length=8, max_length=1000)
    authoritative_duration_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
    )
    authoritative_cost: Optional[Decimal] = Field(
        default=None,
        ge=0,
        le=Decimal("999999.9999"),
        max_digits=10,
        decimal_places=4,
    )
    authoritative_currency: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
    )
    approval_action: Optional[Literal["request", "approve"]] = None
    approval_request_id: Optional[str] = Field(
        default=None,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        ),
    )

    @field_validator("evidence_reference", "adjudication_reason")
    @classmethod
    def _strip_resolution_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence_sha256")
    @classmethod
    def _normalize_evidence_hash(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("authoritative_currency", mode="before")
    @classmethod
    def _normalize_authoritative_currency(cls, value):
        return str(value).strip().upper() if value is not None else None

    @model_validator(mode="after")
    def _validate_resolution_evidence(self):
        expected_evidence = {
            "provider_answer_ambiguous": "carrier_cdr",
            "usage_exceeded_reservation": "provider_usage_record",
        }[self.hold_reason]
        if self.evidence_type != expected_evidence:
            raise ValueError(f"{self.hold_reason} requires {expected_evidence} evidence")
        if self.decision == "finalize" and self.authoritative_duration_seconds is None:
            raise ValueError("finalize requires authoritative_duration_seconds")
        if (self.authoritative_cost is None) != (self.authoritative_currency is None):
            raise ValueError(
                "authoritative_cost and authoritative_currency must be supplied together"
            )
        if self.decision == "release_unanswered":
            if self.approval_action is not None or self.approval_request_id is not None:
                raise ValueError("release_unanswered does not use finalize approval fields")
            if self.authoritative_duration_seconds not in {None, 0}:
                raise ValueError("release_unanswered requires zero authoritative duration")
            if self.authoritative_cost not in {None, Decimal("0")}:
                raise ValueError("release_unanswered cannot carry a positive cost")
        elif self.approval_action == "approve":
            if self.approval_request_id is None:
                raise ValueError("approve requires approval_request_id")
        elif self.approval_request_id is not None:
            raise ValueError("approval_request_id is only valid when approving")
        return self


class AdminInboundBillingHoldResolutionResponse(BaseModel):
    call_id: str
    tenant_id: str
    hold_reason: str
    decision: Literal["release_unanswered", "finalize"]
    billing_status: Literal["held", "released", "finalized"]
    duration_seconds: int = Field(ge=0)
    usage_transaction_id: Optional[str] = None
    evidence_type: str
    evidence_reference: str
    evidence_sha256: str
    authoritative_currency: Optional[str] = None
    workflow_status: Literal["pending_approval", "resolved"] = "resolved"
    approval_request_id: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    is_replay: bool = False


class InboundReassignmentCreateRequest(_StrictModel):
    assignment_id: str
    target_tenant_id: str
    target_campaign_id: str
    reason: str = Field(min_length=3, max_length=2000)
    expected_version: int = Field(ge=1)


class InboundReassignmentApproveRequest(_StrictModel):
    reason: str = Field(min_length=3, max_length=2000)


class InboundReassignmentResponse(BaseModel):
    id: str
    assignment_id: str
    approved_assignment_id: Optional[str] = None
    source_tenant_id: str
    target_tenant_id: str
    target_campaign_id: str
    status: str
    reason: str
    decision_reason: Optional[str] = None
    requested_by: str
    approved_by: Optional[str] = None
    requested_at: datetime
    decided_at: Optional[datetime] = None


class InboundReassignmentListResponse(BaseModel):
    items: list[InboundReassignmentResponse]
    total: int
