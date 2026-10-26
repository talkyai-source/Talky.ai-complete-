"""Pydantic models for /admin/health endpoints."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


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

class IncidentItem(BaseModel):
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


class IncidentListResponse(BaseModel):
    """Paginated incident list"""
    items: List[IncidentItem]
    total: int
    page: int
    page_size: int
    open_count: int
    critical_count: int


class AlertSettings(BaseModel):
    """Alert threshold settings"""
    error_rate_threshold: float = 5.0  # Percentage
    latency_threshold_ms: int = 500
    queue_depth_threshold: int = 100
    memory_threshold_percent: float = 90.0
    cpu_threshold_percent: float = 80.0
    email_notifications: bool = False  # Future feature
    slack_notifications: bool = False  # Future feature
    slack_webhook_url: Optional[str] = None  # Future feature
