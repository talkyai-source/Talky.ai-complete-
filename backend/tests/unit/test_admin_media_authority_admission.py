"""Admin-only admission boundary; shared tenant/retention claims stay unchanged."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.admin import media


def claim_args():
    return dict(resource_type="recording", resource_id=uuid4(), actor_id=str(uuid4()),
                idempotency_key="media-authority:unit", reason="Reviewed test deletion")


@pytest.mark.parametrize("route,resource_arg", [
    (media.delete_admin_recording, "recording_id"),
    (media.delete_admin_feedback, "feedback_id"),
])
async def test_both_admin_routes_explicitly_request_current_authority(monkeypatch, route, resource_arg):
    captured = []

    async def claim(_db, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    await route(**{resource_arg: uuid4()}, payload=media.AdminMediaDeleteRequest(
        reason="Reviewed test deletion"), idempotency_key="media-authority:route",
        admin_user=SimpleNamespace(id=str(uuid4())), db_client=object(), audit_logger=object())
    assert len(captured) == 1 and captured[0]["require_admin"] is True


@pytest.mark.parametrize("status", [403, 503])
async def test_authority_failure_precedes_every_media_lock_and_replay(monkeypatch, status):
    events = []
    conn = object()  # Any query before the guard would fail the test.

    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant is None
        events.append("transaction")
        try:
            yield conn
        finally:
            events.append("rollback")

    async def reject(actual, actor):
        assert actual is conn
        events.append("authority")
        raise HTTPException(status, "Current authority unavailable")

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    monkeypatch.setattr(media, "require_platform_admin_mutation", reject)
    with pytest.raises(HTTPException) as failure:
        await media._claim_admin_media_deletion(SimpleNamespace(pool=object()), **claim_args())
    assert failure.value.status_code == status
    assert events == ["transaction", "authority", "rollback"]


async def test_shared_nonadmin_claim_keeps_tenant_and_retention_policy(monkeypatch):
    class Conn:
        async def fetchval(self, query, *args):
            assert "media-delete-key" in query
            assert isinstance(args[0], str)  # PostgreSQL text codec, not UUID codec.
            return True

        async def fetchrow(self, *_args):
            return None

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    async def forbidden(*_args):
        pytest.fail("Shared tenant/retention deletion must not acquire the Admin authority fence")

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    monkeypatch.setattr(media, "require_platform_admin_mutation", forbidden)
    assert await media._claim_media_deletion(
        SimpleNamespace(pool=object()), **claim_args(), expected_tenant_id=uuid4()) is None


async def test_admin_admission_timeout_is_bounded_and_cleans_up_before_storage(monkeypatch):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def claim(_db, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    original_timeout = asyncio.timeout
    requested_budgets = []

    def short_timeout(seconds):
        requested_budgets.append(seconds)
        return original_timeout(0.01)

    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    monkeypatch.setattr(media.asyncio, "timeout", short_timeout)
    with pytest.raises(HTTPException) as failure:
        await media._claim_admin_media_deletion(object(), **claim_args())
    assert failure.value.status_code == 503
    assert requested_budgets == [5]
    assert entered.is_set() and cleaned.is_set()


async def test_external_cancellation_is_not_changed_to_an_admission_receipt(monkeypatch):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def claim(_db, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    task = asyncio.create_task(media._claim_admin_media_deletion(object(), **claim_args()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
