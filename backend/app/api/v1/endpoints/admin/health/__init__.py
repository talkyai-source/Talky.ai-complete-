"""Admin Health Endpoints — system health monitoring.

Aggregates: detailed health, workers, queues, database, incidents, alerts.

Public surface mirrors the previous single-file `health.py`:
  - `router` — FastAPI router (used by admin/__init__.py)
  - `send_email_alert`, `send_slack_alert`, `trigger_alert` — public
    helpers callable from background monitoring code
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    alerts as _alerts_mod,
    database as _database_mod,
    incidents as _incidents_mod,
    queues as _queues_mod,
    system as _system_mod,
    workers as _workers_mod,
)
from .alerts import send_email_alert, send_slack_alert, trigger_alert
from .schemas import (
    AlertSettings,
    DatabaseStatus,
    DetailedHealthResponse,
    IncidentItem,
    IncidentListResponse,
    QueueStatus,
    QueuesResponse,
    SystemHealthItem,
    WorkerStatus,
    WorkersResponse,
)

router = APIRouter()
router.include_router(_system_mod.router)
router.include_router(_workers_mod.router)
router.include_router(_queues_mod.router)
router.include_router(_database_mod.router)
router.include_router(_incidents_mod.router)
router.include_router(_alerts_mod.router)


__all__ = [
    "router",
    # Public helpers
    "send_email_alert",
    "send_slack_alert",
    "trigger_alert",
    # Schemas
    "AlertSettings",
    "DatabaseStatus",
    "DetailedHealthResponse",
    "IncidentItem",
    "IncidentListResponse",
    "QueueStatus",
    "QueuesResponse",
    "SystemHealthItem",
    "WorkerStatus",
    "WorkersResponse",
]
