from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns
from app.domain.services.campaign_service import (
    CampaignDirectionError,
    CampaignNotFoundError,
    CampaignReadinessError,
    CampaignService,
)
from app.domain.services.telephony import caller_id_guard, trunk_resolver


def _service(monkeypatch, *, requires_sip=True, route_ready=True, caller_verified=True, caller_id="+14165550123"):
    service = CampaignService(SimpleNamespace(pool=object()))
    service.get_campaign = AsyncMock(return_value={"id": "campaign", "direction": "outbound", "status": "paused", "calling_config": {}})
    service._get_pending_leads = AsyncMock()
    service._update_campaign_status = AsyncMock()
    route = SimpleNamespace(refused=not route_ready, reason="selected_trunk_not_ready", trunk_id="trunk", caller_id=caller_id, endpoint="trunk-selected")
    resolver = AsyncMock(return_value=route)
    ownership = AsyncMock(return_value=SimpleNamespace(allowed=caller_verified))
    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", AsyncMock(return_value=requires_sip))
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", resolver)
    monkeypatch.setattr(caller_id_guard, "check_caller_id_ownership", ownership)
    return service, resolver, ownership


@pytest.mark.asyncio
@pytest.mark.parametrize("list_id", [None, "contact-list"])
@pytest.mark.parametrize("route_ready,verified,code", [(False, True, "selected_trunk_not_ready"), (True, False, "caller_id_not_verified")])
async def test_readonly_preview_and_start_share_the_same_rejection(monkeypatch, list_id, route_ready, verified, code):
    service, _, ownership = _service(monkeypatch, route_ready=route_ready, caller_verified=verified)
    preview = await service.get_outbound_readiness("campaign", "tenant")
    service.get_campaign.assert_awaited_once_with("campaign", tenant_id="tenant")
    assert preview["ready"] is False and preview["reason_code"] == code
    with pytest.raises(CampaignReadinessError) as exc:
        await service.start_campaign("campaign", tenant_id="tenant", list_id=list_id)
    assert exc.value.reason_code == code
    service._get_pending_leads.assert_not_awaited()
    service._update_campaign_status.assert_not_awaited()
    assert ownership.await_count == (2 if route_ready else 0)


@pytest.mark.asyncio
async def test_cloud_preview_does_not_resolve_sip_or_require_sip_caller_id(monkeypatch):
    service, resolver, ownership = _service(monkeypatch, requires_sip=False)
    result = await service.get_outbound_readiness("campaign", "tenant")
    assert result == {"campaign_id": "campaign", "ready": True, "reason_code": "sip_not_required", "reason": None, "caller_id": None, "trunk_id": None}
    resolver.assert_not_awaited()
    ownership.assert_not_awaited()
    service._get_pending_leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_preview_validates_selected_caller_id_and_does_not_enqueue(monkeypatch):
    service, _, ownership = _service(monkeypatch)
    result = await service.get_outbound_readiness("campaign", "tenant")
    assert result["ready"] is True and result["reason_code"] == "ready"
    assert result["caller_id"] == "+14165550123" and result["trunk_id"] == "trunk"
    assert ownership.await_args.kwargs["caller_id"] == "+14165550123"
    service._get_pending_leads.assert_not_awaited()
    service._update_campaign_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_endpoint_refuses_even_if_route_is_not_marked_refused(monkeypatch):
    service, resolver, ownership = _service(monkeypatch)
    resolver.return_value.endpoint = None
    result = await service.get_outbound_readiness("campaign", "tenant")
    assert result["ready"] is False
    assert result["reason_code"] == "trunk_endpoint_missing"
    ownership.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_redial_keeps_supplied_effective_caller_id_without_extra_db_reads(monkeypatch):
    from app.domain.services.telephony import outbound_readiness

    _, resolver, ownership = _service(monkeypatch, requires_sip=False)
    acquire = AsyncMock(side_effect=AssertionError("Cloud readiness must not read SIP rules"))
    monkeypatch.setattr(outbound_readiness, "acquire_with_tenant", acquire)
    result = await outbound_readiness.evaluate_outbound_readiness(
        object(), tenant_id="tenant", campaign_id="campaign", campaign={},
        calling_rules={"caller_id": "+14165550123"},
    )
    assert result["ready"] is True and result["caller_id"] == "+14165550123"
    resolver.assert_not_awaited()
    ownership.assert_not_awaited()
    acquire.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_caller_id_matches_existing_worker_rules(monkeypatch):
    from app.domain.services.telephony import outbound_readiness

    service, _, ownership = _service(monkeypatch, caller_id=None)
    service.get_campaign.return_value["calling_config"] = {"caller_id": "+14165550999"}
    conn = SimpleNamespace(fetchval=AsyncMock(return_value={"caller_id": "+442055501234"}))

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(outbound_readiness, "acquire_with_tenant", acquire)
    result = await service.get_outbound_readiness("campaign", "tenant")
    # effective_rules deliberately does not support a free-form caller_id
    # campaign override. Only the resolved trunk may replace the tenant DID.
    assert result["caller_id"] == "+442055501234"
    assert ownership.await_args.kwargs["caller_id"] == "+442055501234"


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(CampaignNotFoundError("missing"), 404), (CampaignDirectionError(), 409), (RuntimeError("database unavailable"), 503)])
async def test_endpoint_distinguishes_missing_inbound_and_unavailable(monkeypatch, error, status):
    service = SimpleNamespace(get_outbound_readiness=AsyncMock(side_effect=error))
    monkeypatch.setattr(campaigns, "_get_campaign_service", lambda _db: service)
    tid, cid = str(uuid4()), uuid4()
    with pytest.raises(HTTPException) as exc:
        await campaigns.get_campaign_readiness(cid, SimpleNamespace(tenant_id=tid), object())
    assert exc.value.status_code == status
    service.get_outbound_readiness.assert_awaited_once_with(str(cid), tid)


@pytest.mark.asyncio
async def test_endpoint_without_tenant_does_not_read_or_enqueue(monkeypatch):
    service = SimpleNamespace(get_outbound_readiness=AsyncMock())
    monkeypatch.setattr(campaigns, "_get_campaign_service", lambda _db: service)
    with pytest.raises(HTTPException) as exc:
        await campaigns.get_campaign_readiness(uuid4(), SimpleNamespace(tenant_id=None), object())
    assert exc.value.status_code == 403
    service.get_outbound_readiness.assert_not_awaited()
