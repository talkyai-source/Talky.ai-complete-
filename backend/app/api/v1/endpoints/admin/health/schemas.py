"""Pydantic models for /admin/health endpoints."""
from __future__ import annotations

import json
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from .._serialization import AdminResponseModel


# =============================================================================
# Response Models - System Health
# =============================================================================

class SystemHealthItem(BaseModel):
    """Single provider health status"""
    name: str
    status: str  # 'operational', 'degraded', 'down'
    latency_ms: Optional[int] = None
    latency_display: str
    last_check: Optional[str] = None
    scope: str = "unmeasured"
    detail: str = ""


class DetailedHealthResponse(BaseModel):
    """Full system health with metrics"""
    uptime_seconds: int
    uptime_display: str
    memory_usage_mb: float
    memory_total_mb: float
    memory_percent: float
    cpu_usage_percent: float
    disk_usage_percent: float
    disk_free_gb: float
    python_version: str
    os_info: str
    environment: str
    version: str
    providers: List[SystemHealthItem]
    last_updated: str
    checked_at: str
    metrics_scope: str


class WorkerStatus(BaseModel):
    """Background worker status"""
    id: str
    name: str
    status: str  # idle, busy, offline
    current_task: Optional[str] = None
    processed_count: Optional[int] = None
    failed_count: Optional[int] = None
    success_rate: Optional[float] = None
    last_heartbeat: Optional[str] = None
    uptime_seconds: Optional[int] = None
    age_seconds: Optional[float] = None
    detail: str = ""


class WorkersResponse(BaseModel):
    """Workers list response"""
    workers: List[WorkerStatus]
    total_workers: int
    active_workers: int
    busy_workers: Optional[int] = None
    checked_at: str
    scope: str = "Role heartbeats; not an inventory of worker instances or tasks."


class QueueStatus(BaseModel):
    """Queue status"""
    name: str
    pending: Optional[int] = None
    processing: Optional[int] = None
    failed: Optional[int] = None
    completed_24h: Optional[int] = None
    success_rate_24h: Optional[float] = None
    avg_processing_time_ms: Optional[int] = None
    status: str = "unavailable"
    scope: str
    detail: str = ""


class QueuesResponse(BaseModel):
    """Queues list response"""
    queues: List[QueueStatus]
    total_pending: Optional[int] = None
    total_processing: Optional[int] = None
    checked_at: str


class DatabaseStatus(BaseModel):
    """Database connection status"""
    connected: bool
    latency_ms: Optional[int] = None
    pool_size: Optional[int] = None
    active_connections: Optional[int] = None
    available_connections: Optional[int] = None
    database_size_mb: Optional[float] = None
    table_count: Optional[int] = None
    last_check: str
    error: Optional[str] = None
    scope: str = "Current API process connection pool and connected PostgreSQL database."


# =============================================================================
# Response Models - Incidents & Alerts
# =============================================================================

class IncidentItem(AdminResponseModel):
    """Incident list item"""
    id: str
    title: str
    severity: str  # critical, warning, info
    status: str  # open, acknowledged, resolved
    description: Optional[str] = None
    triggered_at: str
    acknowledged_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    source_system: Optional[str] = None
    source_fingerprint: Optional[str] = None
    source_started_at: Optional[str] = None
    source_status: Optional[Literal["firing", "recovered"]] = None
    source_recovered_at: Optional[str] = None
    source_last_received_at: Optional[str] = None
    source_labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_labels", mode="before")
    @classmethod
    def _decode_source_labels(cls, value):
        # asyncpg may use its default text JSON codec or the app dict codec.
        return json.loads(value) if isinstance(value, str) else value


class IncidentListResponse(BaseModel):
    """Paginated incident list"""
    items: List[IncidentItem]
    total: int
    page: int
    page_size: int
    open_count: int
    critical_count: int


class AlertSettings(BaseModel):
    """No process-local settings or implied receiver delivery."""
    management: Literal["external"] = "external"
    source: Literal["alertmanager"] = "alertmanager"
    editable: Literal[False] = False
    ingestion_status: Literal["configured", "unavailable"]
    notification_delivery: Literal["unverified"] = "unverified"
    detail: str
