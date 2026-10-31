"""Actual call-detail HTTP handler with native asyncpg-like row scalar types.

Database/auth ports are synthetic; native PostgreSQL execution is separate.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_db_client, require_platform_admin
from app.api.v1.endpoints.admin import calls
from app.core.postgres_adapter import PostgrestResponse


CALL = UUID("10000000-0000-4000-8000-000000000001")
TENANT = UUID("20000000-0000-4000-8000-000000000002")
CAMPAIGN = UUID("30000000-0000-4000-8000-000000000003")
LEAD = UUID("40000000-0000-4000-8000-000000000004")
START = datetime(2026, 10, 21, 9, 0, tzinfo=timezone(timedelta(hours=5)))
TIMES = {name: START + timedelta(seconds=index * 5) for index, name in enumerate(
    ("created_at", "started_at", "answered_at", "ended_at"))}


def test_timeline_event_accepts_native_timestamp_without_losing_offset():
    event = calls.TimelineEvent(event="Call Initiated", timestamp=START, status="initiated")
    assert event.model_dump() == {"event": "Call Initiated", "timestamp": START.isoformat(), "status": "initiated"}


def test_timeline_event_json_string_control_is_unchanged():
    stamp = "2026-10-21T04:00:00Z"
    assert calls.TimelineEvent(event="Call Initiated", timestamp=stamp).timestamp == stamp


class Query:
    def __init__(self, table, row, reads):
        self.table, self.row, self.reads = table, row, reads

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def execute(self):
        self.reads.append(self.table)
        data = {"calls": self.row, "tenants": {"business_name": "Owned native row tenant"},
                "campaigns": {"name": "Owned native row campaign"},
                "call_legs": [], "crm_deliveries": [], "call_events": []}
        assert self.table in data, f"Unplanned database read {self.table}"
        return PostgrestResponse(data=data[self.table])


async def read_detail(native):
    row = {"id": CALL, "tenant_id": TENANT, "campaign_id": CAMPAIGN, "lead_id": LEAD,
           "phone_number": "+15555550101", "status": "completed", "direction": "outbound",
           "updated_at": date(2026, 10, 22), "duration_seconds": 15, "goal_achieved": False,
           **{key: value if native in {"all", key} else value.isoformat() for key, value in TIMES.items()}}
    if native == "none":
        row = {key: value.isoformat() if isinstance(value, (date, datetime)) else str(value)
               if isinstance(value, UUID) else value for key, value in row.items()}
    reads = []
    application = FastAPI()
    application.include_router(calls.router, prefix="/api/v1/admin")
    application.dependency_overrides[get_db_client] = lambda: SimpleNamespace(
        table=lambda table: Query(table, row, reads))
    application.dependency_overrides[require_platform_admin] = lambda: SimpleNamespace(
        id="50000000-0000-4000-8000-000000000005", role="platform_admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="https://synthetic.invalid") as client:
        response = await client.get(f"/api/v1/admin/calls/{CALL}")
    return response, reads


@pytest.mark.parametrize("native", ["all", *TIMES, "none"], ids=["native-row-all-timestamps", *TIMES, "json-row-control"])
async def test_actual_call_detail_keeps_all_four_timeline_timestamps_for_database_and_json_rows(native):
    response, reads = await read_detail(native)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(CALL) and body["tenant_id"] == str(TENANT)
    assert body["campaign_id"] == str(CAMPAIGN) and body["lead_id"] == str(LEAD)
    assert body["tenant_name"] == "Owned native row tenant"
    assert body["campaign_name"] == "Owned native row campaign"
    assert body["updated_at"] == "2026-10-22"
    assert [entry["timestamp"] for entry in body["timeline"]] == [stamp.isoformat() for stamp in TIMES.values()]
    assert [entry["event"] for entry in body["timeline"]] == ["Call Initiated", "Call Started", "Call Answered", "Call Ended"]
    assert [entry["status"] for entry in body["timeline"]] == ["initiated", "ringing", "in_progress", "completed"]
    assert body["created_at"] == TIMES["created_at"].isoformat()
    assert body["started_at"] == TIMES["started_at"].isoformat()
    assert body["answered_at"] == TIMES["answered_at"].isoformat()
    assert body["ended_at"] == TIMES["ended_at"].isoformat()
    assert body["crm_receipts_available"] is True
    assert {"calls", "tenants", "campaigns", "crm_deliveries", "call_events", "call_legs"} <= set(reads)
