"""Minute bundle prices, changed by platform admins without a release.

Owner decision 31 Oct 2026: the pay-as-you-go rate is decided later and must
stay adjustable. Prices live in ``topup_packages``; an order snapshots price and
minutes when it is created, so a change applies to new orders only. Bundles are
never deleted: ``is_active = false`` takes one off sale and keeps history.
"""
from __future__ import annotations

import re
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_pool, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import (
    require_current_platform_admin,
    require_platform_admin_mutation,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.payg import AUDIENCES

router = APIRouter(prefix="/billing/packages", tags=["admin"])

_COLUMNS = "id, code, name, minutes, price_cents, currency, audience, is_active, sort_order"
_CODE = re.compile(r"^[a-z0-9][a-z0-9_]{2,63}$")
_MAX_PRICE_CENTS = 10_000_000
_MAX_MINUTES = 1_000_000


class PackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str = Field(min_length=1, max_length=128)
    minutes: int = Field(gt=0, le=_MAX_MINUTES)
    price_cents: int = Field(gt=0, le=_MAX_PRICE_CENTS)
    currency: str = "USD"
    audience: str
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        if not _CODE.match(value):
            raise ValueError("code: 3-64 lowercase letters, digits or underscores")
        return value

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency: three capital letters, e.g. USD")
        return value

    @field_validator("audience")
    @classmethod
    def _audience(cls, value: str) -> str:
        if value not in AUDIENCES:
            raise ValueError("audience: subscriber or payg")
        return value


class PackagePatch(BaseModel):
    """Audience and currency are fixed once a bundle exists: orders refer to it."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    minutes: Optional[int] = Field(default=None, gt=0, le=_MAX_MINUTES)
    price_cents: Optional[int] = Field(default=None, gt=0, le=_MAX_PRICE_CENTS)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10_000)

    def changes(self) -> dict:
        return {key: value for key, value in self.model_dump().items() if value is not None}


def _public(row) -> dict:
    out = dict(row)
    out.pop("id", None)
    return out


@router.get("")
async def list_packages(user: CurrentUser = Depends(require_platform_admin), pool=Depends(get_db_pool)):
    async with acquire_with_tenant(pool, None, user_id=user.id) as conn:
        await require_current_platform_admin(conn, user.id)
        rows = await conn.fetch(f"SELECT {_COLUMNS} FROM topup_packages ORDER BY audience, sort_order, minutes")
    return {"items": [_public(row) for row in rows]}


@router.post("", status_code=201)
async def create_package(body: PackageCreate, user: CurrentUser = Depends(require_platform_admin),
                         pool=Depends(get_db_pool), audit: AuditLogger = Depends(get_audit_logger)):
    async with acquire_with_tenant(pool, None, user_id=user.id) as conn:
        await audit.lock_transaction(conn)
        await require_platform_admin_mutation(conn, user.id)
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO topup_packages
                            (code, name, minutes, price_cents, currency, expires_days, is_active, sort_order, audience)
                        VALUES ($1, $2, $3, $4, $5, NULL, $6, $7, $8) RETURNING {_COLUMNS}""",
                    body.code, body.name, body.minutes, body.price_cents, body.currency, body.is_active,
                    body.sort_order, body.audience)
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(status_code=409, detail="A bundle with this code already exists.") from exc
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.BILLING_UPDATED, action="billing.package.create",
            actor_id=user.id, actor_role=user.role, resource_type="topup_package", resource_id=row["id"],
            after_state=_public(row))
    return _public(row)


@router.patch("/{code}")
async def update_package(code: str, body: PackagePatch, user: CurrentUser = Depends(require_platform_admin),
                         pool=Depends(get_db_pool), audit: AuditLogger = Depends(get_audit_logger)):
    changes = body.changes()
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    async with acquire_with_tenant(pool, None, user_id=user.id) as conn:
        await audit.lock_transaction(conn)
        await require_platform_admin_mutation(conn, user.id)
        before = await conn.fetchrow(f"SELECT {_COLUMNS} FROM topup_packages WHERE code = $1 FOR UPDATE", code)
        if before is None:
            raise HTTPException(status_code=404, detail="Bundle not found.")
        assignments = ", ".join(f"{column} = ${index}" for index, column in enumerate(changes, start=2))
        row = await conn.fetchrow(
            f"UPDATE topup_packages SET {assignments} WHERE code = $1 RETURNING {_COLUMNS}",
            code, *changes.values())
        await audit.log_in_transaction(
            conn, event_type=AuditEvent.BILLING_UPDATED, action="billing.package.update",
            actor_id=user.id, actor_role=user.role, resource_type="topup_package", resource_id=row["id"],
            before_state=_public(before), after_state=_public(row))
    return _public(row)
