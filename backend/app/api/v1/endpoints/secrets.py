"""
Secrets Management API Endpoints

Centralized secrets management with encryption and rotation.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_user, require_permissions, get_audit_logger, get_secrets_manager
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.secrets_manager import (
    SecretMetadata,
    SecretsManager,
    SecretType,
)

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
    # Check tenant access
    user_tenant_id = current_user.get("tenant_id")
    if user_tenant_id and tenant_id != user_tenant_id:
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
    if user_tenant_id and tenant_id != user_tenant_id:
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
    if user_tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access other tenant secrets")

    metadata = await secrets_manager.get_metadata(secret_id)

    if not metadata or metadata.tenant_id != tenant_id:
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
    if user_tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot rotate other tenant secrets")

    new_secret_id = await secrets_manager.rotate(
        secret_id=secret_id,
        rotated_by=current_user["id"],
        grace_period_hours=data.grace_period_hours,
    )

    await audit_logger.log(
        event_type=AuditEvent.SECRET_ROTATED,
        action="secret_rotated",
        actor_id=current_user["id"],
        tenant_id=tenant_id,
        resource_type="secret",
        resource_id=secret_id,
        after_state={"new_secret_id": str(new_secret_id)},
    )

    return {
        "rotated": True,
        "old_secret_id": secret_id,
        "new_secret_id": new_secret_id,
        "grace_period_hours": data.grace_period_hours,
    }


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
    if user_tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot revoke other tenant secrets")

    result = await secrets_manager.revoke(
        secret_id=secret_id,
        revoked_by=current_user["id"],
        reason=reason,
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
    if user_tenant_id and tenant_id != user_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify other tenant secrets")

    result = await secrets_manager.mark_compromised(
        secret_id=secret_id,
        reported_by=current_user["id"],
        reason=reason,
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

@router.post("/validate-api-key")
async def validate_api_key(
    data: ValidateApiKeyRequest,
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """Validate an API key (public endpoint, rate limited)"""
    result = await secrets_manager.validate_api_key(
        api_key=data.api_key,
        required_permission=data.required_permission,
    )

    if not result:
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
    new_secret_id = await secrets_manager.rotate(
        secret_id=secret_id,
        rotated_by=current_user["id"],
        grace_period_hours=data.grace_period_hours,
    )

    await audit_logger.log(
        event_type=AuditEvent.SECRET_ROTATED,
        action="platform_secret_rotated",
        actor_id=current_user["id"],
        resource_type="secret",
        resource_id=secret_id,
    )

    return {"rotated": True, "new_secret_id": new_secret_id}


# Expiring Secrets

@router.get("/expiring")
async def get_expiring_secrets(
    days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(require_permissions(["secrets:read"])),
    secrets_manager: SecretsManager = Depends(get_secrets_manager),
):
    """Get secrets expiring within specified days"""
    secrets = await secrets_manager.get_expiring_secrets(days=days)
    return {"secrets": secrets, "count": len(secrets)}
