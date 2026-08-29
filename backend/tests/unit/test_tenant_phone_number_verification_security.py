"""DID ownership verification cannot be asserted by a tenant client."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.tenant_phone_numbers as endpoint
from app.api.v1.dependencies import CurrentUser
from app.domain.models.tenant_phone_number import TenantPhoneNumber, VerificationMethod
from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberError,
    TenantPhoneNumberService,
)


TENANT = "11111111-1111-1111-1111-111111111111"
DID_ID = "22222222-2222-2222-2222-222222222222"


class _RecordingService:
    calls: list[dict] = []

    def __init__(self, _pool):
        pass

    async def mark_verified(self, **kwargs):
        self.calls.append(kwargs)
        return TenantPhoneNumber(
            id=DID_ID,
            tenant_id=TENANT,
            e164="+15551234567",
            status="verified",
            verification_method=kwargs["method"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["tenant_admin", "readonly"])
async def test_tenant_roles_receive_403_without_mutating_did(monkeypatch, role):
    _RecordingService.calls.clear()
    monkeypatch.setattr(endpoint, "TenantPhoneNumberService", _RecordingService)
    payload = endpoint.PhoneNumberVerifyRequest(
        method=VerificationMethod.SMS_CODE,
        proof_reference="challenge-reviewed-123",
    )
    user = CurrentUser(
        id="33333333-3333-3333-3333-333333333333",
        email="tenant@example.test",
        tenant_id=TENANT,
        role=role,
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint.verify_phone_number(
            DID_ID,
            payload,
            target_tenant_id=None,
            current_user=user,
            db_pool=object(),
        )

    assert exc.value.status_code == 403
    assert _RecordingService.calls == []


@pytest.mark.asyncio
async def test_platform_verification_passes_auditable_proof_metadata(monkeypatch):
    _RecordingService.calls.clear()
    monkeypatch.setattr(endpoint, "TenantPhoneNumberService", _RecordingService)
    payload = endpoint.PhoneNumberVerifyRequest(
        method=VerificationMethod.LETTER_OF_AUTHORIZATION,
        proof_reference="loa-case-456",
        notes="Reviewed in carrier portal",
    )
    user = CurrentUser(
        id="44444444-4444-4444-4444-444444444444",
        email="security@example.test",
        tenant_id=None,
        role="platform_admin",
    )

    result = await endpoint.verify_phone_number(
        DID_ID,
        payload,
        target_tenant_id=TENANT,
        current_user=user,
        db_pool=object(),
    )

    assert result.status == "verified"
    assert _RecordingService.calls[0]["tenant_id"] == TENANT
    assert _RecordingService.calls[0]["proof_reference"] == "loa-case-456"
    assert _RecordingService.calls[0]["proof_notes"] == "Reviewed in carrier portal"


@pytest.mark.asyncio
async def test_service_defense_rejects_non_platform_actor_before_database():
    with pytest.raises(TenantPhoneNumberError, match="platform administrator"):
        await TenantPhoneNumberService(None).mark_verified(
            tenant_id=TENANT,
            did_id=DID_ID,
            method=VerificationMethod.CARRIER_API,
            verified_by="tenant@example.test",
            proof_reference="carrier-case-789",
            actor_id="33333333-3333-3333-3333-333333333333",
            actor_role="tenant_admin",
        )
