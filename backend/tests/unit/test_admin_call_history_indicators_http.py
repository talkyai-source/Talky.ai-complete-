"""Unavailable media metadata must remain distinct from confirmed absence."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_db_client, require_platform_admin
from app.api.v1.endpoints.admin import calls
from app.core.postgres_adapter import PostgrestResponse

CALL_ID = "10000000-0000-4000-8000-000000000001"
TENANT_ID = "20000000-0000-4000-8000-000000000001"
ROW = {"id": CALL_ID, "tenant_id": TENANT_ID, "phone_number": "+15550000000",
       "status": "completed", "duration_seconds": 42, "created_at": "2026-10-21T00:00:00Z"}


class Query:
    def __init__(self, table, failures, present):
        self.table, self.failures, self.present = table, failures, present

    def __getattr__(self, _name):
        return lambda *args, **kwargs: self

    def execute(self):
        failure = self.failures.get(self.table)
        if failure == "raise":
            raise RuntimeError("synthetic indicator dependency unavailable")
        if failure == "error":
            return PostgrestResponse(error="synthetic indicator dependency unavailable")
        if failure == "missing":
            return PostgrestResponse(data=None)
        if self.table == "calls":
            return PostgrestResponse(data=[dict(ROW)], count=1)
        if self.table == "tenants":
            return PostgrestResponse(data=[{"id": TENANT_ID, "business_name": "Synthetic tenant"}])
        if self.table == "recordings_s3":
            return PostgrestResponse(data=[{"call_id": CALL_ID}] if self.present else [])
        if self.table == "call_feedback":
            return PostgrestResponse(data=[{"call_id": CALL_ID, "transcript_status": "completed"}] if self.present else [])
        raise AssertionError(f"Unexpected table {self.table}")


async def request(failures=None, present=True):
    failures = failures or {}
    app = FastAPI()
    app.include_router(calls.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(
        table=lambda table: Query(table, failures, present)
    )
    app.dependency_overrides[require_platform_admin] = lambda: SimpleNamespace(
        id="30000000-0000-4000-8000-000000000001", role="platform_admin"
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
        return await client.get("/api/v1/admin/calls/history?page=1&page_size=20")


@pytest.mark.parametrize("source,field,other", [
    ("recordings_s3", "has_recording", "has_feedback"),
    ("call_feedback", "has_feedback", "has_recording"),
])
@pytest.mark.parametrize("failure", ["error", "raise", "missing"])
async def test_independent_indicator_failure_preserves_call_and_other_known_indicator(source, field, other, failure):
    response = await request({source: failure})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1 and body["page"] == 1 and body["page_size"] == 20
    row = body["items"][0]
    assert row["id"] == CALL_ID and row["duration_seconds"] == 42
    assert row[field] is None, row
    assert row[other] is True
    assert row["feedback_transcript_status"] == (None if source == "call_feedback" else "completed")


async def test_both_indicator_failures_are_unknown_without_hiding_call():
    response = await request({"recordings_s3": "error", "call_feedback": "error"})
    row = response.json()["items"][0]
    assert row["id"] == CALL_ID
    assert row["has_recording"] is None and row["has_feedback"] is None


async def test_successfully_observed_empty_metadata_remains_false():
    response = await request(present=False)
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["has_recording"] is False and row["has_feedback"] is False


async def test_successfully_observed_present_metadata_remains_true():
    response = await request()
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["has_recording"] is True and row["has_feedback"] is True
    assert row["feedback_transcript_status"] == "completed"
