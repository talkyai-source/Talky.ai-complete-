"""Direction-aware operator issues must use the matching durable source."""

import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.api.v1.endpoints.calls as calls_module
from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints.calls import list_call_issues


@pytest.mark.asyncio
async def test_inbound_issues_use_failed_inbound_calls_without_duplicating_rejections(monkeypatch):
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    campaign_id = UUID("22222222-2222-2222-2222-222222222222")
    now = datetime.now(timezone.utc)
    captured: dict[str, object] = {}

    class Conn:
        async def fetch(self, query, *args):
            captured["query"] = query
            captured["args"] = args
            return [
                {
                    "id": UUID("44444444-4444-4444-4444-444444444444"),
                    # The SQL, not response code, must replace private ANI with
                    # the business DID (or an empty string).
                    "phone_number": "+15550009999",
                    "campaign_id": campaign_id,
                    "campaign_name": "Reception",
                    "status": "failed",
                    "processing_status": "failed",
                    "outcome": None,
                    "updated_at": now,
                },
                {
                    "id": UUID("77777777-7777-4777-8777-777777777777"),
                    "phone_number": "+15550008888",
                    "campaign_id": campaign_id,
                    "campaign_name": "Reception",
                    "status": "completed",
                    # A voice session existed, so lifecycle processing closed
                    # normally, but its crashed provider pipeline persisted
                    # the durable failed outcome.
                    "processing_status": "completed",
                    "outcome": "failed",
                    "updated_at": now,
                },
            ]

    @asynccontextmanager
    async def acquire(_pool, actual_tenant):
        assert str(actual_tenant) == str(tenant_id)
        yield Conn()

    monkeypatch.setattr(calls_module, "acquire_with_tenant", acquire)

    result = await list_call_issues(
        campaign_id=str(campaign_id),
        window_minutes=60,
        direction="inbound",
        current_user=CurrentUser(
            id="user-1",
            email="user@example.com",
            tenant_id=str(tenant_id),
        ),
        db_client=SimpleNamespace(pool=object()),
    )

    query = str(captured["query"])
    assert "FROM calls c" in query
    assert "inbound_rejections" not in query
    assert "c.direction='inbound'" in query
    assert "c.admission_status='allowed'" in query
    assert "c.processing_status='failed'" in query
    assert "c.outcome='failed'" in query
    assert "COALESCE(c.admission_reason, '') <> 'after_hours_closed'" in query
    assert "caller_ani_private" in query
    assert captured["args"][0] == tenant_id
    assert captured["args"][1] == 60
    assert captured["args"][-1] == campaign_id
    assert [item.reason_code for item in result.items] == [
        "inbound_processing_failed",
        "inbound_pipeline_failed",
    ]
    assert result.items[0].title == "Inbound call processing failed"
    assert result.items[1].title == "Inbound voice pipeline failed"


@pytest.mark.asyncio
async def test_missing_tenant_returns_empty_inbound_issue_feed_without_querying(monkeypatch):
    @asynccontextmanager
    async def fail_if_acquired(*_args, **_kwargs):
        raise AssertionError("database must not be queried without a tenant")
        yield  # pragma: no cover

    monkeypatch.setattr(calls_module, "acquire_with_tenant", fail_if_acquired)

    result = await list_call_issues(
        campaign_id=None,
        window_minutes=60,
        direction="inbound",
        current_user=CurrentUser(id="user-1", email="user@example.com", tenant_id=None),
        db_client=SimpleNamespace(pool=object()),
    )

    assert result.items == []


@pytest.mark.asyncio
async def test_omitted_direction_preserves_outbound_dialer_job_feed(monkeypatch):
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    now = datetime.now(timezone.utc)
    fetched_sql: list[str] = []

    class Conn:
        async def fetch(self, query, *_args):
            fetched_sql.append(query)
            return [
                {
                    "id": UUID("55555555-5555-5555-5555-555555555555"),
                    "phone_number": "+15550001111",
                    "campaign_id": UUID("22222222-2222-2222-2222-222222222222"),
                    "lead_id": UUID("66666666-6666-6666-6666-666666666666"),
                    "status": "failed",
                    "last_outcome": None,
                    "last_error": None,
                    "failure_category": "carrier",
                    "failure_reason": "provider_error",
                    "attempt_number": 2,
                    "updated_at": now,
                    "campaign_name": "Outbound",
                    "campaign_status": "running",
                    "calling_config": None,
                }
            ]

        async def fetchval(self, query, *_args):
            fetched_sql.append(query)
            return None

    @asynccontextmanager
    async def acquire(_pool, actual_tenant):
        assert str(actual_tenant) == str(tenant_id)
        yield Conn()

    async def minutes_available(_tenant_id):
        return SimpleNamespace(exhausted=False)

    monkeypatch.setattr(calls_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        "app.domain.services.minutes_quota.tenant_minutes_status",
        minutes_available,
    )

    direction_query = inspect.signature(list_call_issues).parameters["direction"].default
    assert direction_query.default == "outbound"

    # Deliberately omit direction: the public default must remain outbound.
    result = await list_call_issues(
        campaign_id=None,
        window_minutes=60,
        current_user=CurrentUser(
            id="user-1",
            email="user@example.com",
            tenant_id=str(tenant_id),
        ),
        db_client=SimpleNamespace(pool=object()),
    )

    assert "FROM   dialer_jobs dj" in fetched_sql[0]
    assert "FROM inbound_rejections" not in fetched_sql[0]
    assert [item.job_id for item in result.items] == [
        "55555555-5555-5555-5555-555555555555"
    ]
    assert result.items[0].attempts == 2
