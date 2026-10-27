"""Receipt and bounded history contracts; external/storage effects are forbidden."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints.admin import media


@pytest.mark.parametrize("kind", ["recording", "call_feedback"])
@pytest.mark.parametrize("saved", ["legacy", "untrusted_fields", "absent"])
async def test_exact_delete_receipt_does_not_trust_cached_payload(monkeypatch, kind, saved):
    resource_id, intent_id = uuid4(), uuid4()
    cached = {"detail": "Historical saved message", "deletion_intent_id": str(intent_id)}
    if saved == "untrusted_fields":
        cached.update(resource_id=str(uuid4()), resource_type="wrong", outcome="absent")

    async def claim(*args, **kwargs):
        return None if saved == "absent" else media._DeletionClaim(
            intent_id=intent_id, status="completed", snapshot={}, cached_response=cached)

    async def no_effect(*args, **kwargs):
        raise AssertionError("Completed receipt or absence must not repeat effects")

    monkeypatch.setattr(media, "_claim_admin_media_deletion", claim)
    monkeypatch.setattr(media, "_execute_serialized_media_deletion", no_effect)
    monkeypatch.setattr(media, "_complete_recording_deletion", no_effect)
    monkeypatch.setattr(media, "_complete_feedback_deletion", no_effect)
    endpoint = media.delete_admin_recording if kind == "recording" else media.delete_admin_feedback
    result = await endpoint(**{
        "recording_id" if kind == "recording" else "feedback_id": resource_id,
        "payload": media.AdminMediaDeleteRequest(reason="Reviewed synthetic erasure"),
        "idempotency_key": "synthetic-recovery-key", "admin_user": SimpleNamespace(id=str(uuid4())),
        "db_client": SimpleNamespace(pool=None), "audit_logger": None})
    assert set(result) == {"detail", "resource_type", "resource_id", "outcome", "deletion_intent_id"}
    assert result["resource_type"] == kind and result["resource_id"] == str(resource_id)
    assert result["outcome"] == ("absent" if saved == "absent" else "completed")
    assert result["deletion_intent_id"] == (None if saved == "absent" else str(intent_id))
    if saved == "absent":
        assert "no saved deletion outcome" in result["detail"]
    else:
        assert result["detail"] == cached["detail"]


def item(**changes):
    return media.AdminMediaDeletionItem(id=uuid4(), resource_type="recording", resource_id=uuid4(),
        tenant_id=uuid4(), call_id=uuid4(), original_actor_id=uuid4(), reason="Synthetic saved original reason",
        status=changes.pop("status", "completed"), attempt_count=1,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        object_deleted_at=None, completed_at=None, **changes)


def test_history_cursor_roundtrip_ties_and_filter_binding():
    row = item()
    filters = ["recording", None, str(row.tenant_id), None, "all"]
    cursor = media._media_history_cursor(row, filters)
    assert len(cursor) <= 1024
    assert media._media_history_position(cursor, filters) == (row.created_at, row.id)
    with pytest.raises(media.HTTPException) as error:
        media._media_history_position(cursor, [*filters[:-1], "completed"])
    assert error.value.status_code == 422


@pytest.mark.parametrize("bad", ["x" * 1025, "!!!", "e30", "W10", "bnVsbA"])
def test_malformed_history_cursor_is_422(bad):
    with pytest.raises(media.HTTPException) as error:
        media._media_history_position(bad, [None, None, None, None, "all"])
    assert error.value.status_code == 422


@pytest.mark.parametrize("changes", [{"v": True}, {"created_at": "2026-01-01T00:00:00"}, {"id": "wrong"}])
def test_structured_invalid_cursor_rejects(changes):
    filters = [None, None, None, None, "all"]
    payload = {"v": 1, "filters": filters, "created_at": datetime.now(timezone.utc).isoformat(), "id": str(uuid4()), **changes}
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(media.HTTPException) as error:
        media._media_history_position(cursor, filters)
    assert error.value.status_code == 422


@pytest.mark.parametrize("stage", ["intent_committed", "object_deleted", "failed", "completed"])
def test_saved_stage_preserves_nullable_historical_timestamps(stage):
    row = item(status=stage)
    assert row.status == stage and row.completed_at is None and row.object_deleted_at is None


@pytest.mark.parametrize("failure", ["database", "timeout", "cancel"])
async def test_bounded_read_errors_are_503_and_cancellation_propagates(monkeypatch, failure):
    @asynccontextmanager
    async def connection(*args, **kwargs):
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "timeout":
            await asyncio.Event().wait()
        raise RuntimeError("Sensitive storage error must not be returned")
        yield

    monkeypatch.setattr(media, "acquire_with_tenant", connection)
    monkeypatch.setattr(media, "_MEDIA_HISTORY_TIMEOUT_SECONDS", .01)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await media._read_media_deletions(SimpleNamespace(pool=None), "SELECT owned")
    else:
        with pytest.raises(media.HTTPException) as error:
            await media._read_media_deletions(SimpleNamespace(pool=None), "SELECT owned")
        assert error.value.status_code == 503
        assert "Sensitive" not in str(error.value.detail)


async def test_read_is_transaction_readonly_and_projects_no_storage_fields(monkeypatch):
    commands = []
    class Connection:
        async def execute(self, query):
            commands.append(query)
        async def fetch(self, query, *values):
            commands.append(query)
            return []
    @asynccontextmanager
    async def connection(*args, **kwargs):
        assert args[1] is None
        yield Connection()
    monkeypatch.setattr(media, "acquire_with_tenant", connection)
    result = await media.list_admin_media_deletions(resource_type=None, resource_id=None,
        tenant_id=None, call_id=None, status="all", limit=50, cursor=None,
        admin_user=None, db_client=SimpleNamespace(pool=None))
    assert result.items == [] and not result.has_more and result.next_cursor is None
    assert commands[0] == "SET TRANSACTION READ ONLY"
    assert all(word not in commands[1] for word in ("resource_snapshot", "last_error", "response_body", "idempotency_key", "transcript"))


async def test_exact_missing_history_is_not_an_empty_success(monkeypatch):
    async def empty(*args):
        return []
    monkeypatch.setattr(media, "_read_media_deletions", empty)
    with pytest.raises(media.HTTPException) as error:
        await media.get_admin_media_deletion(uuid4(), admin_user=None, db_client=None)
    assert error.value.status_code == 404 and error.value.detail["code"] == "media_deletion_not_found"
