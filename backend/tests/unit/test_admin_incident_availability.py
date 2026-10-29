"""Incident storage failure must not become a successful empty health view."""

from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin.health import incidents


class Database:
    """Current async aggregate contract; keep actual RLS-context handling."""
    def __init__(self, row, failure=None):
        self.fetchrow = AsyncMock(return_value=row)
        self.execute = AsyncMock()
        self.acquisitions = 0
        self.failure = failure
        self.pool = SimpleNamespace(acquire=self.acquire)

    @asynccontextmanager
    async def acquire(self, **kwargs):
        self.acquisitions += 1
        if self.failure == "acquire":
            raise RuntimeError("private database credentials and SQL details")
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield
        if self.failure == "transaction":
            raise RuntimeError("private database credentials and SQL details")


def empty_row():
    return {"items": [], "total": 0, "open_count": 0, "critical_count": 0}


async def request(db, role="platform_admin"):
    app = FastAPI()
    app.include_router(incidents.router, prefix="/admin")
    app.dependency_overrides[dependencies.get_current_user] = lambda: dependencies.CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        email="operator@example.invalid", role=role,
    )
    app.dependency_overrides[dependencies.get_db_client] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/admin/incidents")


@pytest.mark.parametrize("failure", ["acquire", "query", "transaction", "missing", "bad_json", "invalid_item"])
async def test_incident_storage_failure_is_sanitized_unavailable(failure):
    row = empty_row()
    if failure == "missing":
        row = None
    elif failure == "bad_json":
        row["items"] = "private invalid SQL result"
    elif failure == "invalid_item":
        row["items"] = [{"title": "private incomplete incident"}]
    db = Database(row, failure)
    if failure == "query":
        db.fetchrow.side_effect = RuntimeError("private database credentials and SQL details")
    response = await request(db)
    assert response.status_code == 503
    assert response.json() == {"detail": "Incident records could not be read; their state is unavailable."}
    assert "private" not in response.text
    assert db.acquisitions == 1
    assert db.fetchrow.await_count == (0 if failure == "acquire" else 1)


async def test_successful_empty_storage_remains_an_empty_success():
    db = Database(empty_row())
    response = await request(db)
    assert response.status_code == 200
    assert response.json() == {
        "items": [], "total": 0, "page": 1, "page_size": 20, "open_count": 0, "critical_count": 0,
    }
    assert db.fetchrow.await_count == 1
    assert "WITH filtered AS" in db.fetchrow.await_args.args[0]


async def test_native_postgres_timestamps_and_persisted_counts_are_returned():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    identity = UUID("00000000-0000-0000-0000-000000000003")
    db = Database({"items": [{
        "id": identity, "title": "Persisted incident", "severity": "critical", "status": "open",
        "triggered_at": now, "acknowledged_at": now,
        "acknowledged_by": UUID("00000000-0000-0000-0000-000000000001"),
    }], "total": 1, "open_count": 1, "critical_count": 1})
    response = await request(db)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == body["open_count"] == body["critical_count"] == 1
    assert body["items"][0]["id"] == str(identity)
    assert body["items"][0]["triggered_at"] == body["items"][0]["acknowledged_at"] == now.isoformat()


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user"])
async def test_tenant_roles_cannot_read_incidents(role):
    db = Database(empty_row())
    response = await request(db, role)
    assert response.status_code == 403
    assert db.acquisitions == db.fetchrow.await_count == 0
