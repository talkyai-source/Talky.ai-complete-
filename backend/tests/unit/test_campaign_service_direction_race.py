"""Atomic outbound-start boundary for campaign direction changes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns
from app.domain.services.campaign_service import (
    CampaignDirectionError,
    CampaignService,
    CampaignStateError,
)


class _UpdateQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters: list[tuple[str, object]] = []

    def update(self, _payload):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, rows):
        self.query = _UpdateQuery(rows)

    def table(self, name):
        assert name == "campaigns"
        return self.query


@pytest.mark.asyncio
async def test_outbound_start_status_update_fails_if_campaign_changed_direction():
    db = _DB(rows=[])
    service = CampaignService(db)

    with pytest.raises(CampaignDirectionError):
        await service._update_campaign_status(
            "campaign-1",
            "running",
            tenant_id="tenant-1",
        )

    assert ("direction", "outbound") in db.query.filters


@pytest.mark.asyncio
async def test_outbound_start_status_update_accepts_one_outbound_row():
    db = _DB(rows=[{"id": "campaign-1", "direction": "outbound"}])
    service = CampaignService(db)

    await service._update_campaign_status(
        "campaign-1",
        "running",
        tenant_id="tenant-1",
    )

    assert db.query.filters == [
        ("id", "campaign-1"),
        ("tenant_id", "tenant-1"),
        ("direction", "outbound"),
    ]


@pytest.mark.asyncio
async def test_start_endpoint_maps_typed_direction_error_without_message_matching(
    monkeypatch,
):
    class _Service:
        async def get_campaign(self, _campaign_id):
            return {"id": "campaign-1", "direction": "outbound", "script_config": {}}

        async def start_campaign(self, **_kwargs):
            raise CampaignDirectionError("conversion won")

    monkeypatch.setattr(campaigns, "_get_campaign_service", lambda _db: _Service())
    monkeypatch.setattr(campaigns, "_reject_inbound_campaign_mutation", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.domain.services.minutes_quota.tenant_minutes_status",
        AsyncMock(return_value=SimpleNamespace(exhausted=False)),
    )

    with pytest.raises(HTTPException) as exc:
        await campaigns.start_campaign(
            "campaign-1",
            SimpleNamespace(),
            None,
            CurrentUser(
                id="user-1",
                email="user@example.test",
                tenant_id="tenant-1",
                role="owner",
            ),
            None,
            SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert exc.value.detail["campaign_ids"] == ["campaign-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "stop"])
async def test_outbound_lifecycle_refuses_conversion_winner_before_side_effects(
    monkeypatch,
    operation,
):
    """A committed inbound conversion between read and UPDATE wins the race."""

    db = _DB(rows=[])
    service = CampaignService(db)
    service.get_campaign = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "campaign-1", "direction": "outbound"}
    )
    cancel_jobs = AsyncMock()
    hangup_calls = AsyncMock()
    monkeypatch.setattr(
        "app.domain.services.dialer.job_lifecycle.cancel_active_jobs_for_campaign",
        cancel_jobs,
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.telephony_bridge.hangup_calls_for_campaign",
        hangup_calls,
    )

    with pytest.raises(CampaignDirectionError):
        if operation == "pause":
            await service.pause_campaign("campaign-1", tenant_id="tenant-1")
        else:
            await service.stop_campaign("campaign-1", tenant_id="tenant-1")

    assert ("direction", "outbound") in db.query.filters
    cancel_jobs.assert_not_awaited()
    hangup_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "stop"])
@pytest.mark.parametrize(
    ("service_error", "expected_status", "expected_error"),
    [
        (CampaignDirectionError("conversion won"), 409, "inbound_campaign_managed_separately"),
        (CampaignStateError("ordinary invalid state"), 400, None),
    ],
)
async def test_lifecycle_endpoints_only_label_typed_direction_errors_as_inbound(
    monkeypatch,
    operation,
    service_error,
    expected_status,
    expected_error,
):
    class _Service:
        async def pause_campaign(self, *_args, **_kwargs):
            raise service_error

        async def stop_campaign(self, *_args, **_kwargs):
            raise service_error

    monkeypatch.setattr(campaigns, "_get_campaign_service", lambda _db: _Service())
    monkeypatch.setattr(campaigns, "_reject_inbound_campaign_mutation", lambda *_a, **_k: None)
    user = CurrentUser(
        id="user-1",
        email="user@example.test",
        tenant_id="tenant-1",
        role="owner",
    )

    with pytest.raises(HTTPException) as exc:
        if operation == "pause":
            await campaigns.pause_campaign(
                "campaign-1",
                SimpleNamespace(),
                user,
                SimpleNamespace(),
            )
        else:
            await campaigns.stop_campaign(
                "campaign-1",
                False,
                user,
                SimpleNamespace(),
            )

    assert exc.value.status_code == expected_status
    if expected_error is None:
        assert exc.value.detail == "ordinary invalid state"
    else:
        assert exc.value.detail["error"] == expected_error
        assert exc.value.detail["campaign_ids"] == ["campaign-1"]
