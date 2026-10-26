"""Bounded read/cursor errors; database/auth semantics have a separate PG gate."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from app.domain.services import inbound_campaign_service as module
from app.domain.services.inbound_campaign_service import InboundCampaignError, InboundCampaignService


def cursor(**updates):
    payload = {"v": 1, "status": "all", "requested_at": "2026-01-01T00:00:00+00:00", "id": str(uuid4()), **updates}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.mark.parametrize("value", ["", "!", "a" * 513, "a", cursor(v=True), cursor(v=2),
    cursor(status="pending"), cursor(id="not-a-uuid"), cursor(requested_at="2026-01-01"),
    cursor(requested_at=None), cursor(extra="unexpected")])
def test_bad_or_wrong_filter_cursor_is_rejected_before_database(value):
    with pytest.raises(InboundCampaignError) as error:
        InboundCampaignService._reassignment_cursor(value, "all")
    assert error.value.status_code == 422 and error.value.code == "invalid_cursor"


def test_cursor_roundtrip_keeps_equal_time_uuid_tie_breaker():
    row = {"id": uuid4(), "requested_at": datetime(2026, 1, 1, 10, 2, 3, 987654, tzinfo=timezone.utc)}
    encoded = InboundCampaignService._next_reassignment_cursor(row, "all")
    assert InboundCampaignService._reassignment_cursor(encoded, "all") == (row["requested_at"], row["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), RuntimeError()])
async def test_pool_or_storage_error_is_never_empty_history(monkeypatch, error):
    @asynccontextmanager
    async def unavailable(pool, tenant, *, timeout):
        assert tenant is None and timeout == 3
        raise error
        yield

    monkeypatch.setattr(module, "acquire_with_tenant", unavailable)
    service = InboundCampaignService(object())
    for operation in (service.list_reassignment_requests(), service.get_reassignment_request(str(uuid4()))):
        with pytest.raises(InboundCampaignError) as failure:
            await operation
        assert failure.value.status_code == 503 and failure.value.code == "inbound_reassignment_read_unavailable"


@pytest.mark.asyncio
async def test_read_context_cancellation_propagates_and_closes_scope(monkeypatch):
    entered, exited = asyncio.Event(), asyncio.Event()

    class Connection:
        async def fetch(self, *_args):
            entered.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def scoped(_pool, _tenant, *, timeout):
        assert timeout == 3
        try:
            yield Connection()
        finally:
            exited.set()

    monkeypatch.setattr(module, "acquire_with_tenant", scoped)
    task = asyncio.create_task(InboundCampaignService(object()).list_reassignment_requests())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.asyncio
async def test_ordinary_read_deadline_releases_scope_with_unavailable(monkeypatch):
    exited = asyncio.Event()

    class Connection:
        async def fetch(self, *_args):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def scoped(_pool, _tenant, *, timeout):
        assert timeout == 3
        try:
            yield Connection()
        finally:
            exited.set()

    monkeypatch.setattr(module, "acquire_with_tenant", scoped)
    with pytest.raises(InboundCampaignError) as failure:
        await InboundCampaignService(object()).list_reassignment_requests()
    assert failure.value.status_code == 503 and exited.is_set()
