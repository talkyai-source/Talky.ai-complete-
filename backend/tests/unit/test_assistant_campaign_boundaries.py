"""Direction-boundary regressions for the assistant campaign tools."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.infrastructure.assistant.tools import campaigns as campaign_tools
from app.infrastructure.assistant.tools.campaign_direction import is_outbound_campaign


class _Query:
    def __init__(self, db: "_Db", table: str, operation: str):
        self.db = db
        self.table = table
        self.operation = operation
        self.eq_calls: list[tuple[str, object]] = []
        self.payload = None

    def select(self, columns, **_kwargs):
        self.db.selects.append((self.table, columns))
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def eq(self, field, value):
        self.eq_calls.append((field, value))
        return self

    def single(self):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == "update":
            self.db.updates.append((self.table, self.payload, self.eq_calls))
            return SimpleNamespace(data=[self.payload], error=None, count=1)
        if self.operation == "insert":
            self.db.inserts.append((self.table, self.payload))
            return SimpleNamespace(data=[self.payload], error=None, count=1)
        rows = self.db.rows.get(self.table, [])
        return SimpleNamespace(data=rows, error=None, count=len(rows))


class _Table:
    def __init__(self, db: "_Db", table: str):
        self.db = db
        self.table = table

    def select(self, columns, **kwargs):
        return _Query(self.db, self.table, "select").select(columns, **kwargs)

    def update(self, payload):
        return _Query(self.db, self.table, "update").update(payload)

    def insert(self, payload):
        return _Query(self.db, self.table, "insert").insert(payload)


class _Db:
    def __init__(self, campaign):
        self.rows = {"campaigns": [campaign]}
        self.selects: list[tuple[str, str]] = []
        self.updates: list[tuple] = []
        self.inserts: list[tuple] = []

    def table(self, table):
        return _Table(self, table)


class _CampaignService:
    def __init__(self, campaign):
        self.campaign = campaign
        self.get_calls: list[tuple[str, str]] = []
        self.start_calls: list[tuple[str, str]] = []

    async def get_campaign(self, campaign_id, tenant_id=None):
        self.get_calls.append((campaign_id, tenant_id))
        return self.campaign

    async def start_campaign(self, campaign_id, tenant_id=None):
        self.start_calls.append((campaign_id, tenant_id))
        return SimpleNamespace(
            success=True,
            message="Campaign started",
            campaign_id=campaign_id,
            jobs_enqueued=2,
        )


def test_explicit_null_direction_fails_closed_but_missing_legacy_value_is_outbound():
    assert is_outbound_campaign({}) is True
    assert is_outbound_campaign({"direction": None}) is False


@pytest.mark.asyncio
async def test_get_campaigns_projects_direction():
    db = _Db({"id": "in-1", "name": "Main line", "direction": "inbound"})

    result = await campaign_tools.get_campaigns("tenant-1", db)

    assert result["campaigns"][0]["direction"] == "inbound"
    campaign_projection = next(columns for table, columns in db.selects if table == "campaigns")
    assert "direction" in campaign_projection


@pytest.mark.asyncio
async def test_start_campaign_delegates_to_domain_service_and_reports_enqueued_jobs(monkeypatch):
    campaign = {
        "id": "out-1",
        "tenant_id": "tenant-1",
        "name": "Outbound",
        "status": "draft",
        "direction": "outbound",
    }
    db = _Db(campaign)
    service = _CampaignService(campaign)
    monkeypatch.setattr(
        campaign_tools,
        "_get_campaign_service",
        lambda _db: service,
        raising=False,
    )

    result = await campaign_tools.start_campaign(
        tenant_id="tenant-1",
        db_client=db,
        campaign_id="out-1",
        conversation_id="conversation-1",
    )

    assert result["success"] is True
    assert result["jobs_enqueued"] == 2
    assert service.start_calls == [("out-1", "tenant-1")]
    assert db.updates == [], "the assistant must not bypass CampaignService with a direct status update"


@pytest.mark.asyncio
async def test_start_campaign_refuses_inbound_with_the_shared_error(monkeypatch):
    campaign = {
        "id": "in-1",
        "tenant_id": "tenant-1",
        "name": "Main line",
        "status": "draft",
        "direction": "inbound",
    }
    db = _Db(campaign)
    service = _CampaignService(campaign)
    monkeypatch.setattr(
        campaign_tools,
        "_get_campaign_service",
        lambda _db: service,
        raising=False,
    )

    result = await campaign_tools.start_campaign(
        tenant_id="tenant-1",
        db_client=db,
        campaign_id="in-1",
    )

    assert result["success"] is False
    assert result["error"] == "inbound_campaign_managed_separately"
    assert result["campaign_ids"] == ["in-1"]
    assert service.start_calls == []
    assert db.updates == []
    assert db.inserts == []
