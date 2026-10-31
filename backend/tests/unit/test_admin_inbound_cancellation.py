"""Bounded input and HTTP error contracts; business effects use real PG tests."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.admin import inbound
from app.api.v1.schemas.inbound_campaigns import InboundReassignmentCancelRequest
from app.domain.services.inbound_campaign_service import InboundCampaignError, InboundCampaignService


@pytest.mark.parametrize("reason", ["", "  ", "xy", "x" * 2001])
def test_cancellation_requires_bounded_meaningful_reason(reason):
    with pytest.raises(ValidationError):
        InboundReassignmentCancelRequest(reason=reason)


def test_cancellation_normalizes_reason_and_forbids_invented_actors():
    assert InboundReassignmentCancelRequest(reason="  reviewed reason  ").reason == "reviewed reason"
    with pytest.raises(ValidationError):
        InboundReassignmentCancelRequest(reason="reviewed reason", actor_id="forged")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    InboundCampaignError("missing", code="reassignment_not_found", status_code=404),
    InboundCampaignError("decided", code="already_decided", status_code=409),
    InboundCampaignError("unconfirmed", code="inbound_admin_outcome_unconfirmed", status_code=503),
    HTTPException(status_code=403, detail="Current authority refused"),
    asyncio.CancelledError(),
])
async def test_cancellation_route_keeps_error_and_cancellation_semantics(monkeypatch, error):
    service = AsyncMock()
    service.cancel_reassignment.side_effect = error
    monkeypatch.setattr(inbound, "InboundCampaignService", lambda pool: service)
    user = CurrentUser(id="11111111-1111-1111-1111-111111111111", email="operator@example.test", role="platform_admin")
    with pytest.raises(asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else HTTPException) as caught:
        await inbound.cancel_inbound_reassignment(
            request_id="22222222-2222-2222-2222-222222222222",
            payload=InboundReassignmentCancelRequest(reason=" reviewed cancellation "),
            user=user, idempotency_key="exact-reviewed-key", db_pool=None,
        )
    service.cancel_reassignment.assert_awaited_once_with(
        request_id="22222222-2222-2222-2222-222222222222", reason="reviewed cancellation",
        actor_id=user.id, actor_role="platform_admin", idempotency_key="exact-reviewed-key",
    )
    if isinstance(error, (InboundCampaignError, HTTPException)):
        assert caught.value.status_code == error.status_code


@pytest.mark.asyncio
async def test_service_invalid_reason_never_acquires_database():
    service = InboundCampaignService(None)
    with pytest.raises(InboundCampaignError) as caught:
        await service.cancel_reassignment(
            request_id="22222222-2222-2222-2222-222222222222", reason="  ",
            actor_id="11111111-1111-1111-1111-111111111111", actor_role="platform_admin",
            idempotency_key="exact-reviewed-key",
        )
    assert caught.value.status_code == 422 and caught.value.code == "invalid_reason"
