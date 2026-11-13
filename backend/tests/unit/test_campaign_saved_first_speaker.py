"""The actual dispatch path preserves a campaign's saved opening choice."""
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest

from app.api.v1.schemas.campaigns import CampaignStartRequest
from app.domain.services.campaign_service import CampaignError, CampaignService
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue


def test_omitted_start_field_does_not_manufacture_an_override():
    assert CampaignStartRequest().first_speaker is None


@pytest.mark.asyncio
@pytest.mark.parametrize("saved,override,expected", [
    ("user", None, "user"), ("agent", None, "agent"),
    ("user", "agent", "agent"), ("agent", "user", "user"),
    (None, None, "agent"),
])
async def test_start_or_resume_dispatches_saved_speaker_when_omitted(monkeypatch, saved, override, expected):
    db, queue = _DB(), _Queue([True])
    campaign = db.tables["campaigns"][0]
    campaign["status"] = "paused"
    campaign["calling_config"] = {"batch_size": 3, **({"first_speaker": saved} if saved else {})}
    service = CampaignService(db, queue_service=queue)
    monkeypatch.setattr(service, "_evaluate_outbound_readiness", AsyncMock(return_value={"ready": True}))
    db.pool = object()
    class Connection:
        async def fetchval(self, sql, campaign_id, tenant_id, speaker):
            assert "tenant_id = $2" in sql and "jsonb_set" in sql
            assert (campaign_id, tenant_id) == ("campaign-1", "tenant-1")
            campaign["calling_config"] = {**campaign["calling_config"], "first_speaker": speaker}
            return campaign_id

    @asynccontextmanager
    async def connection(pool, tenant_id):
        assert pool is db.pool and tenant_id == "tenant-1"
        yield Connection()

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", connection)
    # The actual durable insert/ack and queue job creation remain under test.
    kwargs = {"first_speaker": override} if override is not None else {}
    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", **kwargs)
    assert result.jobs_enqueued == 1
    assert queue.enqueued[0].first_speaker == expected
    assert db.tables["dialer_jobs"][0]["status"] == "queued"
    assert campaign["calling_config"]["batch_size"] == 3
    if override is not None:
        assert campaign["calling_config"]["first_speaker"] == override


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [["user"], {"role": "user"}])
async def test_malformed_stored_choice_is_refused_before_dispatch(saved):
    db, queue = _DB(), _Queue([True])
    db.tables["campaigns"][0]["calling_config"] = {"first_speaker": saved}
    service = CampaignService(db, queue_service=queue)
    with pytest.raises(CampaignError) as exc:
        await service.start_campaign("campaign-1", tenant_id="tenant-1")
    assert exc.value.status_code == 422
    assert not queue.enqueued
