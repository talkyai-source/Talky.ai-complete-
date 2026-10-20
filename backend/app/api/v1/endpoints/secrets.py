"""
Secrets Management API Endpoints

Centralized secrets management with encryption and rotation.
"""
import logging
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.api_security_middleware import resolve_client_ip
from app.api.v1.dependencies import get_current_user, require_permissions, get_audit_logger, get_secrets_manager
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.secrets_manager import (
    SecretMetadata,
    SecretsManager,
    SecretType,
    SecretValueRequired,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/secrets", tags=["Secrets"])


class CreateSecretRequest(BaseModel):
    """Create secret request"""
    secret_name: str
    secret_type: SecretType
    description: Optional[str] = None
    value: dict
    permissions: Optional[dict] = None
    rotation_days: Optional[int] = 90
    test_key: bool = False


class CreateSecretResponse(BaseModel):
    """Create secret response"""
    secret_id: UUID
    secret_name: str
    api_key: Optional[str] = None  # Only returned once on creation
    warning: str = "Store this API key securely - it will not be shown again"


class RotateSecretRequest(BaseModel):
    """Rotate secret request"""
    grace_period_hours: int = 24
    # Required for every secret type except TENANT_API_KEY (whose new key is
    # generated server-side and returned once in the response).
    value: Optional[dict] = None


class ValidateApiKeyRequest(BaseModel):
    """Validate API key request"""
    api_key: str
    required_permission: Optional[str] = None


class SecretListResponse(BaseModel):
    """Secret list response (no values)"""
    secrets: list[SecretMetadata]


# Tenant Secrets Endpoints

@router.post("/tenants/{tenant_id}/secrets", response_model=CreateSecretResponse)
async def create_secret(
    tenant_id: UUID,
    data: CreateSecretRequest,
    current_user: dict = Depends(require_permissions(["secrets:write"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Create a new encrypted secret"""
    # Check tenant access. Path `tenant_id` is parsed by FastAPI as `uuid.UUID`,
    # while current_user.tenant_id is a string from the JWT — compare as strings
    # so the check doesn't 403 the legitimate owner.
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot create secrets for other tenant")

    secret_id, api_key = await secrets_manager.create(
        secret_type=data.secret_type,
        owner_type="tenant",
        owner_id=tenant_id,
        value=data.value,
        secret_name=data.secret_name,
        description=data.description,
        created_by=current_user["id"],
        permissions=data.permissions,
        rotation_days=data.rotation_days,
        test_key=data.test_key,
    )

    await audit_logger.log(
        event_type=AuditEvent.API_KEY_CREATED,
        action="secret_created",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="secret",
        resource_id=secret_id,
        metadata={
            "secret_type": data.secret_type.value,
            "secret_name": data.secret_name,
            "test_key": data.test_key,
        },
    )

    return CreateSecretResponse(
        secret_id=secret_id,
        secret_name=data.secret_name,
        api_key=api_key,
    )


@router.get("/tenants/{tenant_id}/secrets")
async def list_secrets(
    tenant_id: UUID,
    secret_type: Optional[SecretType] = None,
    include_inactive: bool = Query(False),
    current_user: dict = Depends(require_permissions(["secrets:read"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """List secrets (metadata only - no values)"""
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot access other tenant secrets")

    secrets = await secrets_manager.list_secrets(
        tenant_id=tenant_id,
        secret_type=secret_type,
        include_inactive=include_inactive,
    )

    return {"secrets": secrets}


@router.get("/tenants/{tenant_id}/secrets/{secret_id}")
async def get_secret(
    tenant_id: UUID,
    secret_id: UUID,
    current_user: dict = Depends(require_permissions(["secrets:read"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """Get secret metadata"""
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot access other tenant secrets")

    metadata = await secrets_manager.get_metadata(secret_id, tenant_id=tenant_id)

    if not metadata or str(metadata.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=404, detail="Secret not found")

    return metadata


@router.post("/tenants/{tenant_id}/secrets/{secret_id}/rotate")
async def rotate_secret(
    tenant_id: UUID,
    secret_id: UUID,
    data: RotateSecretRequest,
    current_user: dict = Depends(require_permissions(["secrets:rotate"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Rotate a secret to a new value"""
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot rotate other tenant secrets")

    # Scope the rotation to the path tenant so a secret owned by another
    # tenant reads as not-found (tenant_secrets has no RLS). rotate() raises
    # ValueError when the scoped secret doesn't exist.
    try:
        new_secret_id, api_key = await secrets_manager.rotate(
            secret_id=secret_id,
            rotated_by=current_user["id"],
            grace_period_hours=data.grace_period_hours,
            new_value=data.value,
            tenant_id=tenant_id,
        )
    except SecretValueRequired:
        raise HTTPException(
            status_code=400,
            detail="A new value is required to rotate this secret type",
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Secret not found")

    await audit_logger.log(
        event_type=AuditEvent.SECRET_ROTATED,
        action="secret_rotated",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="secret",
        resource_id=secret_id,
        after_state={"new_secret_id": str(new_secret_id)},
    )

    response = {
        "rotated": True,
        "old_secret_id": secret_id,
        "new_secret_id": new_secret_id,
        "grace_period_hours": data.grace_period_hours,
    }
    if api_key:
        # Shown once; only its hash is stored.
        response["api_key"] = api_key
        response["warning"] = "Store this API key securely - it will not be shown again"
    return response


@router.delete("/tenants/{tenant_id}/secrets/{secret_id}")
async def revoke_secret(
    tenant_id: UUID,
    secret_id: UUID,
    reason: str = Query(..., description="Reason for revocation"),
    current_user: dict = Depends(require_permissions(["secrets:revoke"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Revoke a secret immediately"""
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot revoke other tenant secrets")

    result = await secrets_manager.revoke(
        secret_id=secret_id,
        revoked_by=current_user["id"],
        reason=reason,
        tenant_id=tenant_id,
    )

    if not result:
        raise HTTPException(status_code=404, detail="Secret not found")

    await audit_logger.log(
        event_type=AuditEvent.SECRET_REVOKED,
        action="secret_revoked",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="secret",
        resource_id=secret_id,
        metadata={"reason": reason},
    )

    return {"revoked": True}


@router.post("/tenants/{tenant_id}/secrets/{secret_id}/compromise")
async def mark_secret_compromised(
    tenant_id: UUID,
    secret_id: UUID,
    reason: str = Query(..., description="Description of compromise"),
    current_user: dict = Depends(require_permissions(["secrets:admin"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Mark a secret as compromised and revoke immediately"""
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and str(tenant_id) != str(user_tenant_id):
        raise HTTPException(status_code=403, detail="Cannot modify other tenant secrets")

    result = await secrets_manager.mark_compromised(
        secret_id=secret_id,
        reported_by=current_user["id"],
        reason=reason,
        tenant_id=tenant_id,
    )

    if not result:
        raise HTTPException(status_code=404, detail="Secret not found")

    await audit_logger.log(
        event_type=AuditEvent.SECRET_REVOKED,
        action="secret_compromised",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="secret",
        resource_id=secret_id,
        metadata={"compromise_reason": reason},
    )

    return {"marked_compromised": True, "auto_revoked": True}


# API Key Validation

# Validation is unauthenticated, so it gets its own per-client cap on top of the
# global IP tier. The key row is found by lookup_hash (migration 0068), so a request
# unwraps one key rather than every active tenant key; only keys that predate that
# column are still scanned, once, until they are indexed. Per process: it bounds
# abuse, it is not a distributed quota.
_VALIDATE_MAX_PER_WINDOW = 30
_VALIDATE_WINDOW_S = 60.0
_VALIDATE_ATTEMPTS: dict[str, deque] = defaultdict(deque)


def _validate_client_ip(request: Request) -> str:
    return resolve_client_ip(request)


def _validate_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    attempts = _VALIDATE_ATTEMPTS[ip]
    while attempts and now - attempts[0] > _VALIDATE_WINDOW_S:
        attempts.popleft()
    if len(attempts) >= _VALIDATE_MAX_PER_WINDOW:
        return True
    attempts.append(now)
    if len(_VALIDATE_ATTEMPTS) > 10_000:  # bound memory under address spraying
        for key in [k for k, v in _VALIDATE_ATTEMPTS.items() if not v or now - v[-1] > _VALIDATE_WINDOW_S]:
            _VALIDATE_ATTEMPTS.pop(key, None)
    return False


@router.post("/validate-api-key")
async def validate_api_key(
    data: ValidateApiKeyRequest,
    request: Request,
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """Validate an API key (public endpoint, rate limited per client IP)"""
    ip = _validate_client_ip(request)
    if _validate_rate_limited(ip):
        logger.warning("api_key_validation_rate_limited ip=%s", ip)
        raise HTTPException(status_code=429, detail="Too many validation attempts")

    result = await secrets_manager.validate_api_key(
        api_key=data.api_key,
        required_permission=data.required_permission,
    )

    if not result:
        logger.warning("api_key_validation_rejected ip=%s", ip)
        raise HTTPException(status_code=401, detail="Invalid API key")

    return {
        "valid": True,
        "tenant_id": result["tenant_id"],
        "permissions": result["permissions"],
        "is_test": result["is_test"],
    }


# Platform Secrets (Platform Admin Only)

@router.get("/platform/secrets")
async def list_platform_secrets(
    current_user: dict = Depends(require_permissions(["platform:admin"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """List platform-level secrets (platform admin only)"""
    secrets = await secrets_manager.list_secrets(
        secret_type=SecretType.PLATFORM,
        include_inactive=False,
    )
    return {"secrets": secrets}


@router.post("/platform/secrets")
async def create_platform_secret(
    data: CreateSecretRequest,
    current_user: dict = Depends(require_permissions(["platform:admin"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Create platform-level secret (platform admin only)"""
    secret_id, _ = await secrets_manager.create(
        secret_type=SecretType.PLATFORM,
        owner_type="system",
        owner_id=UUID("00000000-0000-0000-0000-000000000000"),
        value=data.value,
        secret_name=data.secret_name,
        description=data.description,
        created_by=current_user["id"],
        permissions=data.permissions,
        rotation_days=data.rotation_days,
    )

    await audit_logger.log(
        event_type=AuditEvent.SECRET_ROTATED,
        action="platform_secret_created",
        actor_id=current_user["id"],
        resource_type="secret",
        resource_id=secret_id,
        metadata={"secret_name": data.secret_name},
    )

    return {"secret_id": secret_id, "created": True}


@router.post("/platform/secrets/{secret_id}/rotate")
async def rotate_platform_secret(
    secret_id: UUID,
    data: RotateSecretRequest,
    current_user: dict = Depends(require_permissions(["platform:admin"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Rotate platform secret (platform admin only)"""
    try:
        new_secret_id, api_key = await secrets_manager.rotate(
            secret_id=secret_id,
            rotated_by=current_user["id"],
            grace_period_hours=data.grace_period_hours,
            new_value=data.value,
        )
    except SecretValueRequired:
        raise HTTPException(
            status_code=400,
            detail="A new value is required to rotate this secret type",
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Secret not found")

    await audit_logger.log(
        event_type=AuditEvent.SECRET_ROTATED,
        action="platform_secret_rotated",
        actor_id=current_user["id"],
        resource_type="secret",
        resource_id=secret_id,
    )

    response = {"rotated": True, "new_secret_id": new_secret_id}
    if api_key:
        response["api_key"] = api_key
        response["warning"] = "Store this API key securely - it will not be shown again"
    return response


# Expiring Secrets

@router.get("/expiring")
async def get_expiring_secrets(
    days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_permissions(["secrets:read"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """Get secrets expiring within specified days (scoped to the caller's tenant).

    ``secrets:read`` is a tenant-level permission, so this endpoint must only
    return the caller's own expiring secrets. A caller without a tenant in
    their token (platform admin) sees all tenants, matching the other
    platform-scoped list endpoints.
    """
    secrets = await secrets_manager.get_expiring_secrets(
        days=days,
        tenant_id=current_user.get("tenant_id"),
    )
    return {"secrets": secrets, "count": len(secrets)}
