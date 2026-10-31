"""Shared support API pieces: the feature switch, who is acting, error mapping.

With SUPPORT_ENABLED=false (or a tenant outside SUPPORT_TENANT_ALLOWLIST)
every support route except GET /support/config answers 404, after sign-in and
before any permission or database work.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, TypeVar

import asyncpg
from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.errors import ApiError
from app.core.security.rbac import (
    ROLE_DEFAULT_PERMISSIONS,
    Permission,
    UserRole,
    check_permission,
    get_effective_permissions,
    normalize_role,
    rbac_data_is_seeded,
)
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.config import SupportSettings, load_settings
from app.domain.services.support.errors import SupportError
from app.domain.services.support.ticket_service import WriteResult

logger = logging.getLogger(__name__)
T = TypeVar("T")


def not_found() -> ApiError:
    return ApiError(status=404, code="not_found", message="Not found.")


async def effective_permissions(user: CurrentUser) -> set[Permission]:
    """The same resolution as ``require_permission``: database grants, role
    defaults only when there is no pool or the deployment is unseeded, and a
    503 (never a silent downgrade) when the lookup fails."""
    role = normalize_role(user.role)
    if role == UserRole.PLATFORM_ADMIN:
        return set(Permission)
    try:
        from app.core.container import get_db_pool_from_container

        pool = get_db_pool_from_container()
    except Exception:
        pool = None
    if pool is None:
        return set(ROLE_DEFAULT_PERMISSIONS.get(role, set()))
    try:
        permissions = await get_effective_permissions(pool, user.id, user.tenant_id)
        if not permissions and not await rbac_data_is_seeded(pool):
            permissions = set(ROLE_DEFAULT_PERMISSIONS.get(role, set()))
    except Exception as exc:  # authorization fails closed
        logger.error("support_permission_lookup_failed error_type=%s", type(exc).__name__)
        raise ApiError(status=503, code="authorization_unavailable", message="Authorization unavailable.") from exc
    return set(permissions)


def build_actor(user: CurrentUser, permissions: set[Permission]) -> SupportActor:
    return SupportActor(
        user_id=str(user.id), tenant_id=str(user.tenant_id) if user.tenant_id else None, role=user.role,
        can_report=check_permission(permissions, Permission.SUPPORT_REPORT),
        can_read_team=check_permission(permissions, Permission.SUPPORT_READ_TEAM),
        can_manage=check_permission(permissions, Permission.SUPPORT_MANAGE),
    )


async def settings_dep() -> SupportSettings:
    return load_settings()


async def customer_actor(user: CurrentUser = Depends(get_current_user),
                         settings: SupportSettings = Depends(settings_dep)) -> SupportActor:
    """Signed in, support switched on for this tenant, and support:report."""
    if not settings.enabled_for_tenant(user.tenant_id):
        raise not_found()
    actor = build_actor(user, await effective_permissions(user))
    if not actor.can_report:
        raise ApiError(status=403, code="missing_permission",
                       message="Access denied. Required permission: support:report")
    return actor


async def file_actor(user: CurrentUser = Depends(get_current_user),
                     settings: SupportSettings = Depends(settings_dep)) -> SupportActor:
    """Attachments serve customers (support:report) and platform staff (support:manage)."""
    if not settings.enabled:
        raise not_found()
    actor = build_actor(user, await effective_permissions(user))
    staff = actor.can_manage and normalize_role(user.role) == UserRole.PLATFORM_ADMIN
    if staff:
        return actor
    if not settings.enabled_for_tenant(user.tenant_id):
        raise not_found()
    if not actor.can_report:
        raise ApiError(status=403, code="missing_permission",
                       message="Access denied. Required permission: support:report")
    return SupportActor(user_id=actor.user_id, tenant_id=actor.tenant_id, role=actor.role,
                        can_report=True, can_read_team=actor.can_read_team, can_manage=False)


async def staff_actor(user: CurrentUser = Depends(get_current_user),
                      settings: SupportSettings = Depends(settings_dep)) -> SupportActor:
    """Platform admin role and support:manage; current authority is rechecked in the transaction."""
    if not settings.enabled:
        raise not_found()
    if normalize_role(user.role) != UserRole.PLATFORM_ADMIN:
        raise ApiError(status=403, code="platform_admin_required", message="Platform admin access required.")
    actor = build_actor(user, await effective_permissions(user))
    if not actor.can_manage:
        raise ApiError(status=403, code="missing_permission",
                       message="Access denied. Required permission: support:manage")
    return actor


def user_rate_key(request: Request) -> str:
    """Per signed-in user (set by get_current_user before the limit is checked)."""
    user_id = getattr(request.state, "authenticated_user_id", None)
    if user_id:
        return f"support-user:{user_id}"
    return f"support-ip:{request.client.host if request.client else 'unknown'}"


async def run(awaitable: Awaitable[T]) -> T:
    """Map domain and storage failures onto the standard error envelope."""
    try:
        return await awaitable
    except SupportError as exc:
        raise ApiError(status=exc.status, code=exc.code, message=exc.message) from exc
    except (asyncpg.PostgresError, OSError, asyncio.TimeoutError) as exc:
        logger.warning("support_storage_unavailable error_type=%s", type(exc).__name__)
        raise ApiError(status=503, code="support_unavailable",
                       message="Support is temporarily unavailable. Retry the same request.") from exc


def respond(result: WriteResult) -> JSONResponse:
    headers = {"Idempotent-Replay": "true"} if result.replayed else None
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


__all__ = ["customer_actor", "file_actor", "staff_actor", "settings_dep", "user_rate_key", "run", "respond",
           "not_found", "effective_permissions", "build_actor", "get_db_pool"]
