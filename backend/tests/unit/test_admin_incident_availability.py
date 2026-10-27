"""Incident storage failure must not become a successful empty health view."""

from datetime import datetime, timezone

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1 import dependencies
from app.api.v1.endpoints.admin.health import incidents
from app.core.postgres_adapter import PostgrestResponse


class Query:
    def __init__(self, result):
        self.result = result

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args):
        return self

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Database:
    def __init__(self, results):
        self.results = iter(results)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        assert name == "incidents"  # No synthetic fallback queries.
        return Query(next(self.results))


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


@pytest.mark.parametrize("failed_query", [0, 1, 2])
@pytest.mark.parametrize("failure", ["error_envelope", "exception"])
async def test_any_incident_query_failure_is_sanitized_unavailable(failed_query, failure):
    results = [PostgrestResponse(data=[], count=0) for _ in range(3)]
    private_error = "private database credentials and SQL details"
    results[failed_query] = (
        PostgrestResponse(error=private_error) if failure == "error_envelope" else RuntimeError(private_error)
    )
    db = Database(results)
    response = await request(db)
    assert response.status_code == 503
    assert response.json() == {"detail": "Incident data is temporarily unavailable"}
    assert len(db.tables) == failed_query + 1


async def test_successful_empty_storage_remains_an_empty_success():
    db = Database([PostgrestResponse(data=[], count=0) for _ in range(3)])
    response = await request(db)
    assert response.status_code == 200
    assert response.json() == {
        "items": [], "total": 0, "page": 1, "page_size": 20, "open_count": 0, "critical_count": 0,
    }


async def test_native_postgres_timestamps_and_persisted_counts_are_returned():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    db = Database([
        PostgrestResponse(data=[{
            "id": "stored-incident", "title": "Persisted incident", "severity": "critical", "status": "open",
            "triggered_at": None, "created_at": now, "acknowledged_at": now, "acknowledged_by": "operator",
        }], count=1),
        PostgrestResponse(data=[{"id": "stored-incident"}], count=1),
        PostgrestResponse(data=[{"id": "stored-incident"}], count=1),
    ])
    response = await request(db)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == body["open_count"] == body["critical_count"] == 1
    assert body["items"][0]["id"] == "stored-incident"
    assert body["items"][0]["triggered_at"] == body["items"][0]["acknowledged_at"] == now.isoformat()


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user"])
async def test_tenant_roles_cannot_read_incidents(role):
    db = Database([])
    response = await request(db, role)
    assert response.status_code == 403 and db.tables == []
