"""Alertmanager owns alert rules and external notification delivery.

The old process-local settings could acknowledge changes that no worker used.
Keep compatibility routes/helpers explicit and fail closed; the authenticated
webhook is the sole supported ingestion path into durable incidents.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from app.api.v1.dependencies import CurrentUser, require_platform_admin
from app.api.v1.endpoints.alertmanager import configured_source
from .schemas import AlertSettings

router = APIRouter()


@router.get("/alerts/settings", response_model=AlertSettings)
async def get_alert_settings(admin_user: CurrentUser = Depends(require_platform_admin)):
    return AlertSettings(
        ingestion_status="configured" if configured_source() is not None else "unavailable",
        detail="Rules, routes and receivers are managed in the deployed Alertmanager configuration. "
               "An ingestion credential being configured does not prove alert coverage or notification delivery.",
    )


@router.put("/alerts/settings")
async def update_alert_settings(
    settings: dict | None = Body(None),
    admin_user: CurrentUser = Depends(require_platform_admin),
):
    raise HTTPException(status_code=409, detail="Alert settings are externally managed in Alertmanager; no settings were changed.")


async def send_email_alert(subject: str, body: str, recipients: list[str]) -> bool:
    """Legacy helper is unavailable; Alertmanager owns approved receivers."""
    return False


async def send_slack_alert(message: str, webhook_url: str, severity: str = "warning") -> bool:
    """Never fetch a caller-provided URL through the legacy Admin helper."""
    return False


async def trigger_alert(title: str, severity: str, description: str, db_client) -> str:
    """Fail visibly instead of returning an unsaved incident ID or empty success."""
    raise RuntimeError("Legacy alert triggering is unavailable; use authenticated Alertmanager ingestion.")
