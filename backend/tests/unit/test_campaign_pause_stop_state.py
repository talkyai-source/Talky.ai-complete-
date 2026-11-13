"""D3-17: pause and stop refuse a campaign that is not in a state they apply to.

Both endpoints accepted a campaign in any state: pausing a draft wrote
status='paused' and ran a hang-up sweep, and stopping a completed campaign
rewrote it to 'stopped'. Pause now needs a running campaign; stop needs a
running or paused one (a paused campaign still holds queued calls, and
stopping is how it ends without resuming). The check is also in the UPDATE,
so a state change between read and write cannot slip through. Delete still
stops a campaign in any state before removing it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.domain.services.campaign_service import (
    CampaignDirectionError,
    CampaignService,
    CampaignStateError,
)

CID, TENANT = "campaign-1", "tenant-1"


class _Query:
    def __init__(self, db):
        self.db, self.filters, self.payload, self.op = db, [], None, "select"

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def _match(self, row):
        for kind, column, value in self.filters:
            if kind == "eq" and str(row.get(column)) != str(value):
                return False
            if kind == "in" and row.get(column) not in value:
                return False
        return True

    def execute(self):
        rows = [row for row in self.db.rows if self._match(row)]
        if self.op == "update":
            if self.db.status_changes_before_update:
                for row in self.db.rows:
                    row["status"] = self.db.status_changes_before_update
                rows = [row for row in self.db.rows if self._match(row)]
            for row in rows:
                row.update(self.payload)
            self.db.updates.append((self.payload, list(self.filters)))
        return SimpleNamespace(data=[dict(row) for row in rows], error=None)


class _DB:
    def __init__(self, status):
        self.rows = [{"id": CID, "tenant_id": TENANT, "direction": "outbound", "status": status}]
        self.updates = []
        self.status_changes_before_update = None

    def table(self, name):
        assert name == "campaigns"
        return _Query(self)


@pytest.fixture
def side_effects(monkeypatch):
    effects = SimpleNamespace(
        hangup=AsyncMock(return_value={"status": "confirmed", "deferred": 0}),
        cancel=AsyncMock(return_value=0),
    )
    monkeypatch.setattr("app.api.v1.endpoints.telephony_bridge.hangup_calls_for_campaign", effects.hangup)
    monkeypatch.setattr("app.domain.services.dialer.job_lifecycle.cancel_active_jobs_for_campaign",
                        lambda *a, **k: effects.cancel(*a, **k) and 0)
    return effects


def _service(db):
    service = CampaignService(db)
    service._leads_awaiting_retry = lambda *a, **k: []
    service._release_leads_to_pending = lambda *a, **k: None
    service._get_queue_service = AsyncMock(side_effect=RuntimeError("no redis in unit tests"))
    return service


@pytest.mark.parametrize("status", ["draft", "paused", "stopped", "completed", "cancelled"])
async def test_pause_refuses_a_campaign_that_is_not_running(status, side_effects):
    db = _DB(status)
    with pytest.raises(CampaignStateError, match="running"):
        await _service(db).pause_campaign(CID, tenant_id=TENANT)
    assert db.updates == [] and db.rows[0]["status"] == status
    side_effects.hangup.assert_not_awaited()


@pytest.mark.parametrize("status", ["draft", "stopped", "completed", "cancelled"])
async def test_stop_refuses_a_campaign_that_is_not_running_or_paused(status, side_effects):
    db = _DB(status)
    with pytest.raises(CampaignStateError, match="running or paused"):
        await _service(db).stop_campaign(CID, tenant_id=TENANT)
    assert db.updates == [] and db.rows[0]["status"] == status
    side_effects.hangup.assert_not_awaited()
    side_effects.cancel.assert_not_awaited()


async def test_pause_a_running_campaign_checks_the_state_in_the_update(side_effects):
    db = _DB("running")
    result = await _service(db).pause_campaign(CID, tenant_id=TENANT)
    assert result["status"] == "paused"
    assert ("in", "status", ["running"]) in db.updates[0][1]


@pytest.mark.parametrize("status", ["running", "paused"])
async def test_stop_a_running_or_paused_campaign(status, side_effects):
    db = _DB(status)
    result = await _service(db).stop_campaign(CID, tenant_id=TENANT)
    assert result["status"] == "stopped"
    assert ("in", "status", ["running", "paused"]) in db.updates[0][1]


async def test_a_state_change_between_read_and_write_is_a_state_error_not_a_direction_error(side_effects):
    db = _DB("running")
    db.status_changes_before_update = "stopped"
    with pytest.raises(CampaignStateError) as refused:
        await _service(db).pause_campaign(CID, tenant_id=TENANT)
    assert type(refused.value) is CampaignStateError, "CampaignDirectionError is a subclass; it must not be it"
    side_effects.hangup.assert_not_awaited()


async def test_a_direction_change_is_still_reported_as_one(side_effects):
    db = _DB("running")
    service = _service(db)
    service.get_campaign = AsyncMock(side_effect=[
        {"id": CID, "direction": "outbound", "status": "running"},
        {"id": CID, "direction": "inbound", "status": "running"},
    ])
    db.rows[0]["direction"] = "inbound"
    with pytest.raises(CampaignDirectionError):
        await service.pause_campaign(CID, tenant_id=TENANT)


async def test_delete_still_stops_a_campaign_in_any_state(side_effects):
    db = _DB("draft")
    result = await _service(db).stop_campaign(CID, clear_queue=True, tenant_id=TENANT, from_statuses=None)
    assert result["status"] == "stopped"


async def test_the_delete_endpoint_stops_without_a_state_requirement(monkeypatch):
    calls = []

    class Service:
        async def stop_campaign(self, campaign_id, **kwargs):
            calls.append(kwargs)
            return {}

    class Query:
        def select(self, *_a):
            return self

        def eq(self, *_a):
            return self

        def neq(self, *_a):
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": CID, "status": "draft"}])

    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: Service())
    monkeypatch.setattr(campaigns_ep, "_reject_inbound_campaign_mutation", lambda *a, **k: None)
    monkeypatch.setattr(campaigns_ep, "_update_owned_outbound_campaign", lambda *a, **k: None)
    monkeypatch.setattr(campaigns_ep, "emit_event_via_pool", AsyncMock())
    db = SimpleNamespace(table=lambda _name: Query(), pool=object())
    await campaigns_ep.delete_campaign(CID, SimpleNamespace(id="u1", tenant_id=TENANT), db)
    assert calls and calls[0]["from_statuses"] is None


async def test_pause_endpoint_answers_400_with_the_reason_for_a_draft(monkeypatch, side_effects):
    db = _DB("draft")
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _service(db))
    monkeypatch.setattr(campaigns_ep, "_reject_inbound_campaign_mutation", lambda *a, **k: None)
    with pytest.raises(HTTPException) as refused:
        await campaigns_ep.pause_campaign(CID, SimpleNamespace(),
                                          SimpleNamespace(id="u1", tenant_id=TENANT), db)
    assert refused.value.status_code == 400
    assert "draft" in refused.value.detail and "running" in refused.value.detail
