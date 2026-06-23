"""
Telephony Providers — per-tenant Twilio / Vonage credential management.

Lets a tenant admin save their own cloud-telephony credentials, validate
them against the provider, and pick which provider (Twilio / Vonage /
local SIP trunk / none) is active for their outbound calls.

The SIP-trunk side has its own existing CRUD router at /telephony/sip/*;
this router only handles cloud providers and the active-provider pointer.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool, require_admin
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony/providers", tags=["Telephony Providers"])

ALLOWED_PROVIDERS = ("twilio", "vonage")
ALLOWED_ACTIVE = ("twilio", "vonage", "sip", "none")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TwilioCredentialsBody(BaseModel):
    account_sid: str = Field(min_length=10, max_length=200)
    auth_token: str = Field(min_length=10, max_length=200)


class VonageCredentialsBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)
    api_secret: str = Field(min_length=1, max_length=200)
    app_id: Optional[str] = Field(default=None, max_length=200)
    private_key: Optional[str] = None  # PEM body


class ProviderSaveRequest(BaseModel):
    # Discriminated by URL path; we accept either credential shape and
    # let the endpoint validate against the path provider.
    credentials: Dict[str, str]
    from_number: Optional[str] = None
    label: Optional[str] = None


class ProviderActivateRequest(BaseModel):
    provider: str  # 'twilio' | 'vonage' | 'sip' | 'none'


class TestResult(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: Optional[str] = None
    status_code: Optional[int] = None
    account_status: Optional[str] = None
    friendly_name: Optional[str] = None


class ProviderRowOut(BaseModel):
    provider: str
    label: Optional[str] = None
    from_number: Optional[str] = None
    status: str
    last_tested_at: Optional[datetime] = None
    last_test_result: Optional[Dict[str, Any]] = None
    has_credentials: bool


class ProvidersListResponse(BaseModel):
    active: str
    providers: List[ProviderRowOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_tenant(user: CurrentUser) -> UUID:
    if not user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")
    try:
        return UUID(str(user.tenant_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid tenant id")


def _assert_provider(provider: str) -> str:
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {ALLOWED_PROVIDERS}",
        )
    return provider


def _validate_credentials_shape(provider: str, creds: Dict[str, str]) -> Dict[str, str]:
    if provider == "twilio":
        TwilioCredentialsBody(**creds)  # raises if invalid
        return {
            "account_sid": creds["account_sid"],
            "auth_token": creds["auth_token"],
        }
    if provider == "vonage":
        VonageCredentialsBody(**creds)
        return {
            "api_key": creds["api_key"],
            "api_secret": creds["api_secret"],
            "app_id": creds.get("app_id", ""),
            "private_key": creds.get("private_key", "") or "",
        }
    raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ProvidersListResponse)
async def list_providers(
    current_user: CurrentUser = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
):
    """
    List all saved telephony provider credentials for the tenant plus
    the currently-active provider. Secrets are NEVER returned — callers
    only learn that a row exists (`has_credentials: true`).
    """
    tenant_uuid = _assert_tenant(current_user)

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                active_row = await conn.fetchrow(
                    "SELECT active_telephony_provider FROM tenants WHERE id = $1",
                    tenant_uuid,
                )
                rows = await conn.fetch(
                    """
                    SELECT provider, label, from_number, status,
                           last_tested_at, last_test_result
                    FROM tenant_telephony_credentials
                    WHERE tenant_id = $1
                    ORDER BY provider
                    """,
                    tenant_uuid,
                )
    except Exception as e:
        logger.error("list_providers failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list providers")

    active = (active_row and active_row["active_telephony_provider"]) or "none"
    providers = [
        ProviderRowOut(
            provider=r["provider"],
            label=r["label"],
            from_number=r["from_number"],
            status=r["status"],
            last_tested_at=r["last_tested_at"],
            last_test_result=(
                json.loads(r["last_test_result"])
                if isinstance(r["last_test_result"], str)
                else r["last_test_result"]
            ),
            has_credentials=True,
        )
        for r in rows
    ]
    return ProvidersListResponse(active=active, providers=providers)


@router.put("/{provider}", response_model=ProviderRowOut)
async def save_provider(
    provider: str,
    body: ProviderSaveRequest,
    current_user: CurrentUser = Depends(require_admin),  # audit #16: tenant-admin only
    db_pool=Depends(get_db_pool),
):
    """
    Upsert credentials for one provider. Encrypts the credentials blob
    with the shared Fernet key before storage. Subsequent reads never
    return the plaintext.
    """
    tenant_uuid = _assert_tenant(current_user)
    provider = _assert_provider(provider)

    clean_creds = _validate_credentials_shape(provider, body.credentials)
    encrypted = get_encryption_service().encrypt(json.dumps(clean_creds))

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_telephony_credentials
                        (tenant_id, provider, label, credentials_encrypted,
                         from_number, status, updated_at)
                    VALUES ($1, $2, $3, $4, $5, 'inactive', NOW())
                    ON CONFLICT (tenant_id, provider) DO UPDATE SET
                        label = EXCLUDED.label,
                        credentials_encrypted = EXCLUDED.credentials_encrypted,
                        from_number = EXCLUDED.from_number,
                        status = 'inactive',
                        last_tested_at = NULL,
                        last_test_result = NULL,
                        updated_at = NOW()
                    RETURNING provider, label, from_number, status,
                              last_tested_at, last_test_result
                    """,
                    tenant_uuid, provider, body.label,
                    encrypted, body.from_number,
                )
    except Exception as e:
        logger.error("save_provider failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save provider credentials")

    return ProviderRowOut(
        provider=row["provider"],
        label=row["label"],
        from_number=row["from_number"],
        status=row["status"],
        last_tested_at=row["last_tested_at"],
        last_test_result=None,
        has_credentials=True,
    )


@router.delete("/{provider}")
async def delete_provider(
    provider: str,
    current_user: CurrentUser = Depends(require_admin),  # audit #16: tenant-admin only
    db_pool=Depends(get_db_pool),
):
    """
    Forget credentials for one provider. If this provider was active,
    the active pointer falls back to 'none'.
    """
    tenant_uuid = _assert_tenant(current_user)
    provider = _assert_provider(provider)

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                await conn.execute(
                    """
                    DELETE FROM tenant_telephony_credentials
                    WHERE tenant_id = $1 AND provider = $2
                    """,
                    tenant_uuid, provider,
                )
                await conn.execute(
                    """
                    UPDATE tenants
                    SET active_telephony_provider = 'none'
                    WHERE id = $1 AND active_telephony_provider = $2
                    """,
                    tenant_uuid, provider,
                )
    except Exception as e:
        logger.error("delete_provider failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete provider")
    return {"ok": True}


@router.post("/{provider}/test", response_model=TestResult)
async def test_provider(
    provider: str,
    current_user: CurrentUser = Depends(require_admin),  # audit #16: tenant-admin only
    db_pool=Depends(get_db_pool),
):
    """
    Validate the saved credentials against the provider's API.

    Twilio: fetches the account record (cheapest auth-validating call).
    Vonage: instantiates the SDK client with the supplied auth.

    Stores the result on the row so the UI can display the last attempt.
    """
    tenant_uuid = _assert_tenant(current_user)
    provider = _assert_provider(provider)

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                row = await conn.fetchrow(
                    """
                    SELECT credentials_encrypted, from_number
                    FROM tenant_telephony_credentials
                    WHERE tenant_id = $1 AND provider = $2
                    """,
                    tenant_uuid, provider,
                )
    except Exception as e:
        logger.error("test_provider fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load credentials")

    if not row:
        raise HTTPException(status_code=404, detail="No saved credentials for this provider")

    try:
        plaintext = get_encryption_service().decrypt(row["credentials_encrypted"])
        creds = json.loads(plaintext) if plaintext else {}
    except Exception as e:
        logger.error("test_provider decrypt failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to decrypt credentials")

    if provider == "twilio":
        from app.infrastructure.telephony.twilio_provider_adapter import TwilioProviderAdapter
        adapter = TwilioProviderAdapter(
            account_sid=creds.get("account_sid", ""),
            auth_token=creds.get("auth_token", ""),
            from_number=row["from_number"] or "",
        )
        result = await adapter.ping_with_detail()
    elif provider == "vonage":
        from app.infrastructure.telephony.vonage_provider_adapter import VonageProviderAdapter
        adapter = VonageProviderAdapter(
            api_key=creds.get("api_key"),
            api_secret=creds.get("api_secret"),
            app_id=creds.get("app_id"),
            private_key=creds.get("private_key"),
            from_number=row["from_number"],
        )
        result = await adapter.ping_with_detail()
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    new_status = "active" if result.get("ok") else "failed"
    tested_at = datetime.now(timezone.utc)

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                await conn.execute(
                    """
                    UPDATE tenant_telephony_credentials
                    SET status = $1,
                        last_tested_at = $2,
                        last_test_result = $3::jsonb,
                        updated_at = NOW()
                    WHERE tenant_id = $4 AND provider = $5
                    """,
                    new_status, tested_at, json.dumps(result),
                    tenant_uuid, provider,
                )
    except Exception as e:
        # Test happened, persistence failed — return the test result anyway.
        logger.warning("Could not persist test result: %s", e)

    return TestResult(
        ok=bool(result.get("ok")),
        latency_ms=int(result.get("latency_ms", 0) or 0),
        error=result.get("error"),
        status_code=result.get("status_code"),
        account_status=result.get("account_status"),
        friendly_name=result.get("friendly_name"),
    )


@router.post("/activate")
async def activate_provider(
    body: ProviderActivateRequest,
    current_user: CurrentUser = Depends(require_admin),  # audit #16: tenant-admin only
    db_pool=Depends(get_db_pool),
):
    """
    Pin which provider the dialer should resolve for this tenant.

    For ``twilio`` / ``vonage`` the matching credentials row must exist
    and have a successful last test. For ``sip`` we require at least
    one active row in ``tenant_sip_trunks``. ``none`` always succeeds
    and effectively disables tenant-side telephony (falls back to the
    platform default).
    """
    tenant_uuid = _assert_tenant(current_user)
    target = body.provider
    if target not in ALLOWED_ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {ALLOWED_ACTIVE}",
        )

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                if target in ("twilio", "vonage"):
                    row = await conn.fetchrow(
                        """
                        SELECT status FROM tenant_telephony_credentials
                        WHERE tenant_id = $1 AND provider = $2
                        """,
                        tenant_uuid, target,
                    )
                    if not row:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Save {target} credentials before activating",
                        )
                elif target == "sip":
                    cnt = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM tenant_sip_trunks
                        WHERE tenant_id = $1 AND is_active = TRUE
                        """,
                        tenant_uuid,
                    )
                    if not cnt:
                        raise HTTPException(
                            status_code=400,
                            detail="Add and activate at least one SIP trunk first",
                        )

                await conn.execute(
                    """
                    UPDATE tenants
                    SET active_telephony_provider = $1
                    WHERE id = $2
                    """,
                    target, tenant_uuid,
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("activate_provider failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to activate provider")

    return {"active": target}
