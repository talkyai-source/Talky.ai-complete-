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


class DTMFMode(str, Enum):
    RFC2833 = "rfc2833"
    SIP_INFO = "sip-info"
    INBAND = "inband"
    AUTO = "auto"


def normalize_trunk_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate + canonicalise the advanced SIP-trunk options carried in the
    trunk's free-form ``metadata`` JSON.

    These options (caller_id, outbound_proxy, auth_realm, register,
    register_interval, dtmf_mode, srtp) live in the existing JSONB ``metadata``
    column — no migration — but we still validate them here so a bad value is
    a 422 at write time instead of a surprise when the runtime policy is
    compiled. Keys we don't recognise are preserved untouched so other
    features can keep using ``metadata`` freely.
    """
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise ValueError("metadata must be an object")

    out: Dict[str, Any] = dict(meta)

    def _opt_str(key: str, max_len: int) -> None:
        if key not in out:
            return
        val = out[key]
        if val is None or (isinstance(val, str) and not val.strip()):
            out.pop(key, None)
            return
        if not isinstance(val, str):
            raise ValueError(f"{key} must be a string")
        val = val.strip()
        if len(val) > max_len:
            raise ValueError(f"{key} must be at most {max_len} characters")
        out[key] = val

    def _opt_bool(key: str) -> None:
        if key in out and not isinstance(out[key], bool):
            raise ValueError(f"{key} must be a boolean")

    # caller_id — number presented as the From on outbound calls.
    if "caller_id" in out:
        cid = out["caller_id"]
        if cid is None or (isinstance(cid, str) and not cid.strip()):
            out.pop("caller_id", None)
        else:
            if not isinstance(cid, str):
                raise ValueError("caller_id must be a string")
            cid = cid.strip()
            if len(cid) > 64:
                raise ValueError("caller_id must be at most 64 characters")
            if not re.fullmatch(r"\+?[0-9][0-9\s().\-]{1,}", cid):
                raise ValueError("caller_id must be a phone number (digits, optional leading +)")
            out["caller_id"] = cid

    _opt_str("outbound_proxy", 255)
    _opt_str("auth_realm", 255)
    _opt_bool("register")
    _opt_bool("srtp")

    # source_host — the hostname/IP the tenant's carrier signals inbound from.
    # SECURITY-relevant: it becomes the PJSIP `identify match=` that decides
    # which inbound signalling is trusted as THIS tenant's trunk, so it must
    # be a clean single token (no whitespace/newlines/control chars). Empty →
    # dropped (the generator then falls back to sip_domain).
    if "source_host" in out:
        sh = out["source_host"]
        if sh is None or (isinstance(sh, str) and not sh.strip()):
            out.pop("source_host", None)
        else:
            if not isinstance(sh, str):
                raise ValueError("source_host must be a string")
            sh = sh.strip()
            if len(sh) > 255:
                raise ValueError("source_host must be at most 255 characters")
            # Reject any whitespace/control chars; allow only host/IP tokens
            # (letters, digits, dot, hyphen, colon for IPv6/port).
            if not re.fullmatch(r"[A-Za-z0-9.\-:]+", sh):
                raise ValueError(
                    "source_host must be a hostname or IP address "
                    "(no spaces or control characters)"
                )
            out["source_host"] = sh

    if "dtmf_mode" in out and out["dtmf_mode"] not in {m.value for m in DTMFMode}:
        raise ValueError("dtmf_mode must be one of: " + ", ".join(m.value for m in DTMFMode))

    if "register_interval" in out:
        ri = out["register_interval"]
        if isinstance(ri, bool) or not isinstance(ri, int):
            raise ValueError("register_interval must be an integer")
        if ri < 60 or ri > 86400:
            raise ValueError("register_interval must be between 60 and 86400 seconds")

    return out


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

    @model_validator(mode="after")
    def normalize_metadata(self) -> "SIPTrunkCreateRequest":
        self.metadata = normalize_trunk_metadata(self.metadata)
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

    @model_validator(mode="after")
    def normalize_metadata(self) -> "SIPTrunkUpdateRequest":
        if self.metadata is not None:
            self.metadata = normalize_trunk_metadata(self.metadata)
        return self


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
    last_tested_at: Optional[datetime] = None
    last_test_result: Optional[Dict[str, Any]] = None
    # Real-time Asterisk registration state (registered/rejected/unregistered/
    # inactive/unknown), refreshed ~15s by the trunk-status updater. NOT the
    # frozen Test snapshot — this is the live truth the card renders.
    live_registration_status: Optional[str] = None
    live_status_checked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SIPTrunkTestResponse(BaseModel):
    """Result of POST /trunks/{id}/test — persisted on the trunk row.

    Mirrors the dict returned by the SIP probe helper so the activation
    gate can read .ok back off the trunk row without re-running the probe.
    """
    ok: bool
    latency_ms: int
    transport: SIPTransport
    target: str
    error: Optional[str] = None
    detail: Optional[str] = None
    tested_at: datetime


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
