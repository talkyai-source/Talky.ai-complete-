"""Request / response Pydantic models for runtime policy endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RuntimeActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(default=None, max_length=500)


class RuntimeRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_version: Optional[int] = Field(default=None, ge=1)
    reason: Optional[str] = Field(default=None, max_length=500)


class RuntimeCompilePreviewResponse(BaseModel):
    schema_version: str
    source_hash: str
    active_trunks: int
    active_codecs: int
    active_routes: int
    active_trust_policies: int
    artifact: Dict[str, Any]


class RuntimeActivationResponse(BaseModel):
    policy_version: int
    source_hash: str
    build_status: str
    apply_result: Dict[str, Any]
    verify_result: Dict[str, Any]


class RuntimeRollbackResponse(BaseModel):
    from_version: int
    to_version: int
    status: str
    apply_result: Dict[str, Any]
    verify_result: Dict[str, Any]


class RuntimeVersionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    policy_version: int
    source_hash: str
    schema_version: str
    build_status: str
    is_active: bool
    is_last_good: bool
    validation_report: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime]


class RuntimeActivationMetricsResponse(BaseModel):
    tenant_id: UUID
    window_hours: int
    generated_at: datetime
    activation_success_count: int
    activation_failure_count: int
    activation_success_rate_pct: float
    rollback_success_count: int
    rollback_failure_count: int
    rollback_latency_p50_ms: float
    rollback_latency_p95_ms: float
    rollback_latency_max_ms: float
