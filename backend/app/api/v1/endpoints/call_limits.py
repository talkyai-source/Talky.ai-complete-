"""
Call Limits Admin API (Day 7)

Endpoints for managing tenant call limits and partner aggregate limits.

Routes:
    GET  /api/v1/admin/tenants/{tenant_id}/call-limits
    PUT  /api/v1/admin/tenants/{tenant_id}/call-limits
    GET  /api/v1/admin/partners/{partner_id}/limits
    PUT  /api/v1/admin/partners/{partner_id}/limits
    POST /api/v1/admin/dnc
    GET  /api/v1/admin/dnc
    DELETE /api/v1/admin/dnc/{entry_id}
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from app.api.v1.dependencies import (
    get_db_client,
    require_admin,
    CurrentUser,
    get_audit_logger,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.core.postgres_adapter import Client

router = APIRouter(prefix="/admin", tags=["Call Limits Admin (Day 7)"])

logger = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TenantCallLimitsSchema(BaseModel):
    """Schema for tenant call limits."""
    calls_per_minute: int = Field(default=60, ge=1, le=10000)
    calls_per_hour: int = Field(default=1000, ge=1, le=100000)
    calls_per_day: int = Field(default=10000, ge=1, le=1000000)
    max_concurrent_calls: int = Field(default=10, ge=1, le=1000)
    max_queue_size: int = Field(default=50, ge=0, le=1000)
    monthly_minutes_allocated: int = Field(default=0, ge=0)
    monthly_minutes_used: int = Field(default=0, ge=0)
    monthly_spend_cap: Optional[float] = Field(default=None, ge=0)
    monthly_spend_used: float = Field(default=0.0, ge=0)
    max_call_duration_seconds: int = Field(default=3600, ge=60, le=14400)
    min_call_interval_seconds: int = Field(default=300, ge=0)
    allowed_country_codes: List[str] = Field(default_factory=list)
    blocked_country_codes: List[str] = Field(default_factory=list)
    blocked_prefixes: List[str] = Field(default_factory=list)
    features_enabled: Dict[str, Any] = Field(default_factory=dict)
    features_disabled: Dict[str, Any] = Field(default_factory=dict)
    respect_business_hours: bool = False
    business_hours_start: Optional[str] = None  # "HH:MM" format
    business_hours_end: Optional[str] = None
    business_hours_timezone: str = "UTC"
    is_active: bool = True


class PartnerLimitsSchema(BaseModel):
    """Schema for partner aggregate limits."""
    max_tenants: int = Field(default=10, ge=1)
    aggregate_calls_per_minute: int = Field(default=600, ge=1)
    aggregate_calls_per_hour: int = Field(default=10000, ge=1)
    aggregate_calls_per_day: int = Field(default=100000, ge=1)
    aggregate_concurrent_calls: int = Field(default=100, ge=1)
    revenue_share_percent: float = Field(default=20.0, ge=0, le=100)
    min_billing_amount: float = Field(default=100.0, ge=0)
    max_billing_amount: Optional[float] = Field(default=None, ge=0)
    feature_whitelist: List[str] = Field(default_factory=list)
    feature_blacklist: List[str] = Field(default_factory=list)
    fraud_detection_sensitivity: int = Field(default=50, ge=0, le=100)
    is_active: bool = True


class DncEntrySchema(BaseModel):
    """Schema for DNC entry creation."""
    phone_number: str = Field(..., description="Phone number (will be normalized)")
    source: str = Field(default="manual")
    reason: Optional[str] = None
    expires_at: Optional[str] = None  # ISO format datetime


class DncEntryResponse(BaseModel):
    """Response schema for DNC entry."""
    id: UUID
    phone_number: str
    normalized_number: str
    source: str
    reason: Optional[str]
    expires_at: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# Tenant Call Limits Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/tenants/{tenant_id}/call-limits",
    response_model=Dict[str, Any],
    summary="Get tenant call limits",
    description="Retrieve call limits configuration for a tenant.",
)
async def get_tenant_call_limits(
    tenant_id: UUID,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get call limits for a tenant. Requires admin privileges."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM tenant_call_limits
            WHERE tenant_id = $1 AND is_active = TRUE
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            tenant_id,
        )

    if not row:
        # Return defaults
        return {
            "tenant_id": str(tenant_id),
            "calls_per_minute": 60,
            "calls_per_hour": 1000,
            "calls_per_day": 10000,
            "max_concurrent_calls": 10,
            "max_queue_size": 50,
            "is_default": True,
        }

    return dict(row)


@router.put(
    "/tenants/{tenant_id}/call-limits",
    response_model=Dict[str, Any],
    summary="Update tenant call limits",
    description="Update or create call limits for a tenant.",
)
async def update_tenant_call_limits(
    tenant_id: UUID,
    limits: TenantCallLimitsSchema,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Update call limits for a tenant. Requires admin privileges."""
    import json
    from datetime import time

    # Parse business hours
    business_start = None
    business_end = None
    if limits.business_hours_start:
        hour, minute = map(int, limits.business_hours_start.split(":"))
        business_start = time(hour, minute)
    if limits.business_hours_end:
        hour, minute = map(int, limits.business_hours_end.split(":"))
        business_end = time(hour, minute)

    async with db_client.pool.acquire() as conn:
        # Deactivate existing limits
        await conn.execute(
            """
            UPDATE tenant_call_limits
            SET is_active = FALSE,
                effective_until = NOW()
            WHERE tenant_id = $1 AND is_active = TRUE
            """,
            tenant_id,
        )

        # Insert new limits
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_call_limits (
                tenant_id,
                calls_per_minute,
                calls_per_hour,
                calls_per_day,
                max_concurrent_calls,
                max_queue_size,
                monthly_minutes_allocated,
                monthly_minutes_used,
                monthly_spend_cap,
                monthly_spend_used,
                max_call_duration_seconds,
                min_call_interval_seconds,
                allowed_country_codes,
                blocked_country_codes,
                blocked_prefixes,
                features_enabled,
                features_disabled,
                respect_business_hours,
                business_hours_start,
                business_hours_end,
                business_hours_timezone,
                is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
            RETURNING *
            """,
            tenant_id,
            limits.calls_per_minute,
            limits.calls_per_hour,
            limits.calls_per_day,
            limits.max_concurrent_calls,
            limits.max_queue_size,
            limits.monthly_minutes_allocated,
            limits.monthly_minutes_used,
            limits.monthly_spend_cap,
            limits.monthly_spend_used,
            limits.max_call_duration_seconds,
            limits.min_call_interval_seconds,
            limits.allowed_country_codes,
            limits.blocked_country_codes,
            limits.blocked_prefixes,
            json.dumps(limits.features_enabled),
            json.dumps(limits.features_disabled),
            limits.respect_business_hours,
            business_start,
            business_end,
            limits.business_hours_timezone,
            True,
        )

        # Log the change
        await audit_logger.log(
            event_type=AuditEvent.LIMITS_CHANGED,
            actor_id=admin_user.id,
            actor_type="user",
            tenant_id=str(tenant_id),
            action="call_limits_updated",
            description=f"Call limits updated for tenant {tenant_id}",
            metadata={
                "calls_per_minute": limits.calls_per_minute,
                "calls_per_hour": limits.calls_per_hour,
                "calls_per_day": limits.calls_per_day,
                "max_concurrent_calls": limits.max_concurrent_calls,
            },
        )

    return dict(row)


# ---------------------------------------------------------------------------
# Partner Limits Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/partners/{partner_id}/limits",
    response_model=Dict[str, Any],
    summary="Get partner limits",
    description="Retrieve aggregate limits for a partner.",
)
async def get_partner_limits(
    partner_id: UUID,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get aggregate limits for a partner. Requires admin privileges."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT pl.*, t.name as partner_name
            FROM partner_limits pl
            JOIN tenants t ON t.id = pl.partner_id
            WHERE pl.partner_id = $1 AND pl.is_active = TRUE
            """,
            partner_id,
        )

    if not row:
        # Get partner name and return defaults
        async with db_client.pool.acquire() as conn:
            tenant = await conn.fetchrow(
                "SELECT name FROM tenants WHERE id = $1",
                partner_id,
            )

        return {
            "partner_id": str(partner_id),
            "partner_name": tenant["name"] if tenant else "Unknown",
            "max_tenants": 10,
            "aggregate_calls_per_minute": 600,
            "aggregate_calls_per_hour": 10000,
            "aggregate_calls_per_day": 100000,
            "aggregate_concurrent_calls": 100,
            "is_default": True,
        }

    return dict(row)


@router.put(
    "/partners/{partner_id}/limits",
    response_model=Dict[str, Any],
    summary="Update partner limits",
    description="Update aggregate limits for a partner.",
)
async def update_partner_limits(
    partner_id: UUID,
    limits: PartnerLimitsSchema,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Update aggregate limits for a partner. Requires admin privileges."""
    import json

    async with db_client.pool.acquire() as conn:
        # Upsert partner limits
        row = await conn.fetchrow(
            """
            INSERT INTO partner_limits (
                partner_id,
                max_tenants,
                aggregate_calls_per_minute,
                aggregate_calls_per_hour,
                aggregate_calls_per_day,
                aggregate_concurrent_calls,
                revenue_share_percent,
                min_billing_amount,
                max_billing_amount,
                feature_whitelist,
                feature_blacklist,
                fraud_detection_sensitivity,
                is_active
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (partner_id)
            DO UPDATE SET
                max_tenants = EXCLUDED.max_tenants,
                aggregate_calls_per_minute = EXCLUDED.aggregate_calls_per_minute,
                aggregate_calls_per_hour = EXCLUDED.aggregate_calls_per_hour,
                aggregate_calls_per_day = EXCLUDED.aggregate_calls_per_day,
                aggregate_concurrent_calls = EXCLUDED.aggregate_concurrent_calls,
                revenue_share_percent = EXCLUDED.revenue_share_percent,
                min_billing_amount = EXCLUDED.min_billing_amount,
                max_billing_amount = EXCLUDED.max_billing_amount,
                feature_whitelist = EXCLUDED.feature_whitelist,
                feature_blacklist = EXCLUDED.feature_blacklist,
                fraud_detection_sensitivity = EXCLUDED.fraud_detection_sensitivity,
                updated_at = NOW()
            RETURNING *
            """,
            partner_id,
            limits.max_tenants,
            limits.aggregate_calls_per_minute,
            limits.aggregate_calls_per_hour,
            limits.aggregate_calls_per_day,
            limits.aggregate_concurrent_calls,
            limits.revenue_share_percent,
            limits.min_billing_amount,
            limits.max_billing_amount,
            json.dumps(limits.feature_whitelist),
            json.dumps(limits.feature_blacklist),
            limits.fraud_detection_sensitivity,
            True,
        )

    return dict(row)


# ---------------------------------------------------------------------------
# DNC List Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/dnc",
    response_model=DncEntryResponse,
    summary="Add DNC entry",
    description="Add a phone number to the do-not-call list.",
    status_code=status.HTTP_201_CREATED,
)
async def add_dnc_entry(
    entry: DncEntrySchema,
    tenant_id: Optional[UUID] = Query(None, description="Tenant ID (null for global)"),
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Add a phone number to DNC list. Requires admin privileges."""
    import re
    from datetime import datetime

    # Normalize phone number
    phone = entry.phone_number
    has_plus = phone.startswith("+")
    digits = re.sub(r"\D", "", phone)
    normalized = f"+{digits}" if has_plus or len(digits) > 10 else f"+1{digits}"

    # Parse expires_at
    expires = None
    if entry.expires_at:
        expires = datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00"))

    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO dnc_entries (
                tenant_id,
                phone_number,
                normalized_number,
                source,
                reason,
                expires_at
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, normalized_number)
            DO UPDATE SET
                source = EXCLUDED.source,
                reason = EXCLUDED.reason,
                expires_at = EXCLUDED.expires_at
            RETURNING *
            """,
            tenant_id,
            entry.phone_number,
            normalized,
            entry.source,
            entry.reason,
            expires,
        )

    return DncEntryResponse(
        id=row["id"],
        phone_number=row["phone_number"],
        normalized_number=row["normalized_number"],
        source=row["source"],
        reason=row["reason"],
        expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
        created_at=row["created_at"].isoformat(),
    )


@router.get(
    "/dnc",
    response_model=List[DncEntryResponse],
    summary="List DNC entries",
    description="Get DNC list entries for a tenant or global.",
)
async def list_dnc_entries(
    tenant_id: Optional[UUID] = Query(None),
    phone_number: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """List DNC entries. Requires admin privileges."""
    query = """
        SELECT *
        FROM dnc_entries
        WHERE (tenant_id = $1 OR tenant_id IS NULL)
    """
    params = [tenant_id]

    if phone_number:
        # Normalize search number
        import re
        digits = re.sub(r"\D", "", phone_number)
        query += f" AND normalized_number LIKE ${len(params) + 1}"
        params.append(f"%{digits}%")

    query += " ORDER BY created_at DESC"
    query += f" LIMIT ${len(params) + 1}"
    params.append(limit)

    async with db_client.pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        DncEntryResponse(
            id=r["id"],
            phone_number=r["phone_number"],
            normalized_number=r["normalized_number"],
            source=r["source"],
            reason=r["reason"],
            expires_at=r["expires_at"].isoformat() if r["expires_at"] else None,
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


@router.delete(
    "/dnc/{entry_id}",
    summary="Remove DNC entry",
    description="Remove a phone number from the DNC list.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_dnc_entry(
    entry_id: UUID,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Remove a DNC entry. Requires admin privileges."""
    async with db_client.pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM dnc_entries WHERE id = $1",
            entry_id,
        )

    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="DNC entry not found")


# ---------------------------------------------------------------------------
# Utility Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/call-limits/status",
    response_model=Dict[str, Any],
    summary="Get call limits system status",
    description="Get overview of call limits and usage across all tenants.",
)
async def get_call_limits_status(
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get call limits system status. Requires admin privileges."""
    """Get system-wide call limits status."""
    async with db_client.pool.acquire() as conn:
        # Count tenants with custom limits
        custom_limits_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT tenant_id) FROM tenant_call_limits WHERE is_active = TRUE"
        )

        # Count partners with limits
        partner_limits_count = await conn.fetchval(
            "SELECT COUNT(*) FROM partner_limits WHERE is_active = TRUE"
        )

        # Count DNC entries
        dnc_count = await conn.fetchval(
            "SELECT COUNT(*) FROM dnc_entries"
        )

        # Recent guard decisions
        recent_decisions = await conn.fetch(
            """
            SELECT
                decision,
                COUNT(*) as count
            FROM call_guard_decisions
            WHERE created_at > NOW() - INTERVAL '1 hour'
            GROUP BY decision
            """
        )

    return {
        "tenants_with_custom_limits": custom_limits_count,
        "partners_with_limits": partner_limits_count,
        "dnc_entries": dnc_count,
        "recent_decisions": {r["decision"]: r["count"] for r in recent_decisions},
    }
