"""D4-7: campaign start refuses a suspended account with a coded 403.

Before this change start only checked minutes; a suspended tenant's campaign
went to ``running`` and every call was then refused one by one by the guard.
The refusal must come before any pacing write or enqueue.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep

TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
CAMPAIGN = "11111111-2222-4333-8444-555555555555"


def _authority_row(**overrides):
    row = {"id": TENANT, "status": "active", "subscription_status": "active",
           "suspended_at": None, "suspended_by": None, "suspension_reason": None,
           "white_label_partner_id": None, "formal_partner_id": None,
           "formal_partner_status": None, "active_holds": [], "authority_scope_ok": True}
    row.update(overrides)
    return row


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(row=_authority_row(), starts=[], pacing_writes=[], leases=[], fail_read=False)

    class Conn:
        async def fetchrow(self, query, *args):
            if state.fail_read:
                raise RuntimeError("synthetic authority read failure")
            assert "tenants" in query.lower()
            return state.row

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        state.leases.append(tenant_id)
        yield Conn()

    class Service:
        async def get_campaign(self, campaign_id):
            return {"id": campaign_id, "script_config": {}, "calling_config": {}}

        async def start_campaign(self, **kwargs):
            state.starts.append(kwargs)
            return SimpleNamespace(message="ok", jobs_enqueued=0, queue_stats={},
                                   campaign_id=kwargs["campaign_id"])

    async def minutes_status(tenant_id):
        return SimpleNamespace(state="known", exhausted=False, allocated=100,
                               used_minutes=0, remaining_minutes=100)

    async def no_event(*args, **kwargs):
        return None

    monkeypatch.setattr(campaigns_ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: Service())
    monkeypatch.setattr(campaigns_ep, "_reject_inbound_campaign_mutation", lambda *a, **k: None)
    monkeypatch.setattr(campaigns_ep, "_update_owned_outbound_campaign",
                        lambda *a, **k: state.pacing_writes.append(k))
    monkeypatch.setattr(campaigns_ep, "emit_event_via_pool", no_event)
    monkeypatch.setattr("app.domain.services.minutes_quota.tenant_minutes_status", minutes_status)
    return state


async def _start(body=None):
    user = SimpleNamespace(id="actor-1", tenant_id=TENANT)
    return await campaigns_ep.start_campaign(
        campaign_id=CAMPAIGN, request=SimpleNamespace(), start_request=body,
        current_user=user, idempotency_key=None, db_client=SimpleNamespace(pool=object()))


@pytest.mark.parametrize("row,reason", [
    ({"subscription_status": "suspended"}, "tenant_suspended"),
    ({"subscription_status": "canceled"}, "tenant_cancelled"),
    ({"status": "suspended"}, "tenant_suspended"),
], ids=["admin-suspend", "cancelled", "formal-status"])
async def test_a_suspended_account_gets_a_coded_403_before_anything_is_written(harness, row, reason):
    harness.row = _authority_row(**row)
    body = campaigns_ep.CampaignStartRequest(batch_size=5, call_gap_seconds=10)
    with pytest.raises(HTTPException) as refused:
        await _start(body)
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == "tenant_suspended"
    assert refused.value.detail["reason"] == reason
    assert refused.value.detail["message"]
    assert harness.starts == [] and harness.pacing_writes == []
    assert harness.leases == [None], "authority is read service-scoped, as the call guard reads it"


async def test_an_unreadable_account_state_fails_closed_with_503(harness):
    harness.fail_read = True
    with pytest.raises(HTTPException) as refused:
        await _start()
    assert refused.value.status_code == 503
    assert refused.value.detail["code"] == "account_status_unavailable"
    assert harness.starts == []


async def test_an_active_account_still_starts(harness):
    response = await _start()
    assert response["campaign"]["status"] == "running"
    assert len(harness.starts) == 1
