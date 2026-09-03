from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints import contact_lists as lists_ep
from app.domain.services.campaign_service import CampaignError, CampaignService


@dataclass
class _Response:
    data: list
    count: int | None = None
    error: str | None = None


class _Query:
    def __init__(self, db: "_DB", table: str):
        self.db = db
        self.table = table
        self.operation = "select"
        self.payload = None
        self.filters: list[tuple[str, str, object]] = []
        self.count_mode = None

    def select(self, *_columns, count=None):
        self.operation = "select"
        self.count_mode = count
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, tuple(values)))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def _matches(self, row):
        for operation, column, value in self.filters:
            actual = row.get(column)
            if operation == "eq" and actual != value:
                return False
            if operation == "in" and actual not in value:
                return False
            if operation == "is":
                token = str(value).strip().upper()
                if token == "NOT TRUE" and actual is True:
                    return False
                if token == "TRUE" and actual is not True:
                    return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.operation == "select":
            selected = [dict(row) for row in rows if self._matches(row)]
            return _Response(
                selected,
                count=len(selected) if self.count_mode == "exact" else None,
            )
        if self.operation == "insert":
            if self.table == "dialer_jobs" and self.db.dialer_insert_error:
                return _Response([], error=self.db.dialer_insert_error)
            values = self.payload if isinstance(self.payload, list) else [self.payload]
            inserted = [dict(value) for value in values]
            rows.extend(inserted)
            returned = [dict(value) for value in inserted]
            if self.table == "dialer_jobs" and self.db.mismatched_insert_ids:
                returned[0]["id"] = "wrong-job-id"
            return _Response(returned)
        if self.operation == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    updated.append(dict(row))
            return _Response(updated)
        raise AssertionError(f"unsupported operation {self.operation}")


class _DB:
    def __init__(
        self,
        *,
        dialer_insert_error: str | None = None,
        mismatched_insert_ids: bool = False,
        lead_count: int = 1,
    ):
        self.dialer_insert_error = dialer_insert_error
        self.mismatched_insert_ids = mismatched_insert_ids
        self.tables = {
            "campaigns": [
                {
                    "id": "campaign-1",
                    "tenant_id": "tenant-1",
                    "status": "draft",
                    "direction": "outbound",
                    "voice_id": "voice-1",
                }
            ],
            "leads": [
                {
                    "id": f"lead-{index}",
                    "tenant_id": "tenant-1",
                    "campaign_id": "campaign-1",
                    "phone_number": f"+1415555010{index}",
                    "priority": 5,
                    "status": "pending",
                    "do_not_call": False,
                    "list_id": None,
                    "created_at": "2026-09-03T00:00:00+00:00",
                }
                for index in range(1, lead_count + 1)
            ],
            "dialer_jobs": [],
            "contact_lists": [],
        }

    def table(self, name):
        return _Query(self, name)


class _Queue:
    def __init__(self, results: list[bool]):
        self.results = list(results)
        self.enqueued = []

    async def enqueue_job(self, job):
        self.enqueued.append(job)
        return self.results.pop(0)

    async def get_queue_stats(self):
        return {}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_db_error_envelope_prevents_any_redis_enqueue():
    db = _DB(dialer_insert_error="database unavailable")
    queue = _Queue([True])

    with pytest.raises(CampaignError) as exc:
        await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1"
        )

    assert queue.enqueued == []
    assert getattr(exc.value, "jobs_enqueued", 0) == 0
    assert db.tables["dialer_jobs"] == []


@pytest.mark.asyncio
async def test_unconfirmed_inserted_ids_prevent_any_redis_enqueue():
    db = _DB(mismatched_insert_ids=True)
    queue = _Queue([True])

    with pytest.raises(CampaignError):
        await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1"
        )

    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_false_redis_result_is_failure_and_durable_job_remains_pending():
    db = _DB()
    queue = _Queue([False])

    with pytest.raises(CampaignError) as exc:
        await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1"
        )

    assert getattr(exc.value, "jobs_enqueued", None) == 0
    assert getattr(exc.value, "jobs_pending", None) == 1
    assert len(db.tables["dialer_jobs"]) == 1
    assert db.tables["dialer_jobs"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_partial_redis_dispatch_reports_only_confirmed_work():
    db = _DB(lead_count=2)
    queue = _Queue([True, False])

    with pytest.raises(CampaignError) as exc:
        await CampaignService(db, queue_service=queue).start_campaign(
            "campaign-1", tenant_id="tenant-1"
        )

    assert getattr(exc.value, "jobs_enqueued", None) == 1
    assert getattr(exc.value, "jobs_pending", None) == 1
    assert [row["status"] for row in db.tables["dialer_jobs"]].count("queued") == 1
    assert [row["status"] for row in db.tables["dialer_jobs"]].count("pending") == 1


@pytest.mark.asyncio
async def test_retry_reconciles_pending_dispatch_without_inserting_second_job():
    db = _DB()
    first_queue = _Queue([False])
    with pytest.raises(CampaignError):
        await CampaignService(db, queue_service=first_queue).start_campaign(
            "campaign-1", tenant_id="tenant-1"
        )
    durable_job_id = db.tables["dialer_jobs"][0]["id"]

    second_queue = _Queue([True])
    result = await CampaignService(db, queue_service=second_queue).start_campaign(
        "campaign-1", tenant_id="tenant-1"
    )

    assert result.jobs_enqueued == 1
    assert len(db.tables["dialer_jobs"]) == 1
    assert second_queue.enqueued[0].job_id == durable_job_id
    assert db.tables["dialer_jobs"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_contact_list_503_reports_confirmed_partial_dispatch(monkeypatch):
    class _PartialDispatchFailure(CampaignError):
        def __init__(self):
            super().__init__("redis dispatch failed", status_code=503)
            self.jobs_enqueued = 1
            self.jobs_pending = 2

    class _Service:
        async def start_campaign(self, **_kwargs):
            raise _PartialDispatchFailure()

    class _ListDB:
        def table(self, name):
            return _Query(self, name)

        tables = {
            "contact_lists": [
                {
                    "id": "list-1",
                    "tenant_id": "tenant-1",
                    "campaign_id": "campaign-1",
                    "name": "List",
                    "is_active": False,
                }
            ],
            "leads": [],
        }
        dialer_insert_error = None

    class _User:
        tenant_id = "tenant-1"

    monkeypatch.setattr(
        lists_ep,
        "require_owned_outbound_campaign",
        lambda *_args, **_kwargs: {"id": "campaign-1", "direction": "outbound"},
    )
    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _Service())

    with pytest.raises(HTTPException) as exc:
        await lists_ep.call_contact_list("list-1", _User(), _ListDB())

    assert exc.value.status_code == 503
    assert exc.value.detail["jobs_enqueued"] == 1
    assert exc.value.detail["jobs_pending"] == 2
