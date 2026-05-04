"""Pydantic models + tiny canonicalisation helpers for /telephony/sip.

`_normalize_codec` and `_validate_match_pattern` live here (rather than in
`_shared.py`) because they are referenced inside `model_validator` methods
on the schemas themselves, and `_shared.py` already imports from this
module — moving them would create a circular import.
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- helpers used by the validators below + by endpoint code -----------

def _normalize_codec(codec: str) -> str:
    return codec.strip().upper()


def _validate_match_pattern(pattern: str) -> None:
    # Keep regex validation strict to avoid runtime route-compile failures.
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid match_pattern regex: {exc}") from exc


# --- enums --------------------------------------------------------------

class SIPTransport(str, Enum):
    UDP = "udp"
    TCP = "tcp"
    TLS = "tls"


class SIPDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class SIPRouteType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


# --- SIP trunks ---------------------------------------------------------

class SIPTrunkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trunk_name: str = Field(min_length=3, max_length=100)
    sip_domain: str = Field(min_length=3, max_length=255)
    port: int = Field(default=5060, ge=1, le=65535)
    transport: SIPTransport = SIPTransport.UDP
    direction: SIPDirection = SIPDirection.BOTH
    auth_username: Optional[str] = Field(default=None, max_length=255)
    auth_password: Optional[str] = Field(default=None, max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_auth_pair(self) -> "SIPTrunkCreateRequest":
        if bool(self.auth_username) != bool(self.auth_password):
            raise ValueError("auth_username and auth_password must both be provided")
        return self


class SIPTrunkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trunk_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    sip_domain: Optional[str] = Field(default=None, min_length=3, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    transport: Optional[SIPTransport] = None
    direction: Optional[SIPDirection] = None
    auth_username: Optional[str] = Field(default=None, max_length=255)
    auth_password: Optional[str] = Field(default=None, max_length=255)
    clear_auth: bool = False
    metadata: Optional[Dict[str, Any]] = None


class SIPTrunkResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    trunk_name: str
    sip_domain: str
    port: int
    transport: SIPTransport
    direction: SIPDirection
    is_active: bool
    auth_username: Optional[str]
    auth_configured: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --- codec policies -----------------------------------------------------

class CodecPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(min_length=3, max_length=100)
    allowed_codecs: List[str] = Field(default_factory=lambda: ["PCMU", "PCMA"], min_length=1)
    preferred_codec: str = Field(default="PCMU", min_length=1, max_length=20)
    sample_rate_hz: int = Field(default=8000)
    ptime_ms: int = Field(default=20)
    max_bitrate_kbps: Optional[int] = Field(default=None, gt=0)
    jitter_buffer_ms: int = Field(default=60, ge=0, le=1000)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "CodecPolicyCreateRequest":
        allowed = [_normalize_codec(c) for c in self.allowed_codecs]
        preferred = _normalize_codec(self.preferred_codec)
        if preferred not in allowed:
            raise ValueError("preferred_codec must be present in allowed_codecs")
        if self.sample_rate_hz not in {8000, 16000, 24000, 48000}:
            raise ValueError("sample_rate_hz must be one of 8000,16000,24000,48000")
        if self.ptime_ms not in {10, 20, 30, 40, 60}:
            raise ValueError("ptime_ms must be one of 10,20,30,40,60")
        self.allowed_codecs = allowed
        self.preferred_codec = preferred
        return self


class CodecPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    allowed_codecs: Optional[List[str]] = Field(default=None, min_length=1)
    preferred_codec: Optional[str] = Field(default=None, min_length=1, max_length=20)
    sample_rate_hz: Optional[int] = None
    ptime_ms: Optional[int] = None
    max_bitrate_kbps: Optional[int] = Field(default=None, gt=0)
    jitter_buffer_ms: Optional[int] = Field(default=None, ge=0, le=1000)
    metadata: Optional[Dict[str, Any]] = None


class CodecPolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_name: str
    allowed_codecs: List[str]
    preferred_codec: str
    sample_rate_hz: int
    ptime_ms: int
    max_bitrate_kbps: Optional[int]
    jitter_buffer_ms: int
    is_active: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --- route policies -----------------------------------------------------

class RoutePolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(min_length=3, max_length=100)
    route_type: SIPRouteType = SIPRouteType.OUTBOUND
    priority: int = Field(default=100, ge=1, le=10000)
    match_pattern: str = Field(min_length=1, max_length=512)
    target_trunk_id: UUID
    codec_policy_id: Optional[UUID] = None
    strip_digits: int = Field(default=0, ge=0, le=15)
    prepend_digits: Optional[str] = Field(default=None, max_length=20)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_pattern(self) -> "RoutePolicyCreateRequest":
        _validate_match_pattern(self.match_pattern)
        return self


class RoutePolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_name: Optional[str] = Field(default=None, min_length=3, max_length=100)
    route_type: Optional[SIPRouteType] = None
    priority: Optional[int] = Field(default=None, ge=1, le=10000)
    match_pattern: Optional[str] = Field(default=None, min_length=1, max_length=512)
    target_trunk_id: Optional[UUID] = None
    codec_policy_id: Optional[UUID] = None
    strip_digits: Optional[int] = Field(default=None, ge=0, le=15)
    prepend_digits: Optional[str] = Field(default=None, max_length=20)
    metadata: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class RoutePolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_name: str
    route_type: SIPRouteType
    priority: int
    match_pattern: str
    target_trunk_id: UUID
    codec_policy_id: Optional[UUID]
    strip_digits: int
    prepend_digits: Optional[str]
    is_active: bool
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --- quotas -------------------------------------------------------------

class TelephonyQuotaStatusItem(BaseModel):
    policy_id: Optional[str]
    policy_name: str
    policy_scope: str
    metric_key: str
    window_seconds: int
    warn_threshold: int
    throttle_threshold: int
    block_threshold: int
    block_duration_seconds: int
    throttle_retry_seconds: int
    counter_value: int
    window_ttl_seconds: int
    block_ttl_seconds: int
    current_action: str
    metadata: Dict[str, Any]


class TelephonyQuotaStatusResponse(BaseModel):
    tenant_id: str
    policy_scope: str
    metrics: List[TelephonyQuotaStatusItem]
    generated_at: datetime
