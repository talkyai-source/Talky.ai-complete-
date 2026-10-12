"""Registering a revoked or suspended DID answers 409, not 500.

Bug hunt 2026-10-10 (telephony-infra-media-10 follow-up): create_pending now
refuses to touch a revoked/suspended row and raises
TenantPhoneNumberConflictError (it used to return the revoked row with 201).
register_phone_number did not catch it, so the refusal surfaced as an
unhandled 500 instead of a conflict the dashboard can show.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import tenant_phone_numbers as endpoint
from app.domain.services.tenant_phone_number_service import TenantPhoneNumberConflictError


async def test_registering_a_revoked_number_is_a_409(monkeypatch):
    class _Service:
        def __init__(self, _pool):
            pass

        async def create_pending(self, **_):
            raise TenantPhoneNumberConflictError("number is revoked for this tenant")

    monkeypatch.setattr(endpoint, "TenantPhoneNumberService", _Service)
    payload = endpoint.PhoneNumberRegisterRequest(e164="+14155550123", provider="asterisk")
    user = SimpleNamespace(id="u1", role="tenant_admin", tenant_id="00000000-0000-4000-8000-000000000001")

    with pytest.raises(HTTPException) as exc:
        await endpoint.register_phone_number(payload, current_user=user, db_pool=object())
    assert exc.value.status_code == 409
