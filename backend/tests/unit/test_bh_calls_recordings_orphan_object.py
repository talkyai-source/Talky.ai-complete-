"""A stored recording whose DB row cannot be written must not be left orphaned."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_service as recording

CALL = "10000000-0000-4000-8000-000000000007"
TENANT = "20000000-0000-4000-8000-000000000007"
CAMPAIGN = "30000000-0000-4000-8000-000000000007"
RECORDING = "40000000-0000-4000-8000-000000000007"


@pytest.fixture
def fx(monkeypatch, tmp_path):
    conn = SimpleNamespace(
        fetchrow=AsyncMock(return_value={"id": UUID(CALL)}),
        fetchval=AsyncMock(return_value=False),  # "is the key referenced by a row?"
        execute=AsyncMock(),
    )

    @asynccontextmanager
    async def scoped(pool, tenant):
        assert tenant == TENANT
        yield conn

    monkeypatch.setattr(recording, "acquire_with_tenant", scoped)
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(recording, "encode_recording_audio", lambda raw: (raw, ".wav", "audio/wav"))
    decision = policy.RecordingDecision(True, False, None, None, 90, "tenant_policy_one_party")
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", AsyncMock(return_value=decision))
    s3 = SimpleNamespace(
        is_available=lambda: True,
        bucket="synthetic",
        region="r",
        upload=MagicMock(),
        delete_permanently=MagicMock(),
    )
    service = recording.RecordingService(object(), s3_client=s3)
    service._insert_recording_record = AsyncMock(return_value=None)
    service._update_call_recording_url = AsyncMock()
    service._mark_upload_failed = AsyncMock()
    buffer = recording.RecordingBuffer(call_id=CALL)
    buffer._wav_bytes_override = b"synthetic-audio-only"
    buffer.total_bytes = 20
    policy.clear_disclosure_state(CALL)
    yield SimpleNamespace(service=service, conn=conn, s3=s3, buffer=buffer, dir=tmp_path)
    policy.clear_disclosure_state(CALL)


def _files(directory):
    return [os.path.join(r, f) for r, _, fs in os.walk(directory) for f in fs]


@pytest.mark.asyncio
async def test_local_audio_is_removed_when_the_row_insert_fails(fx, caplog):
    fx.s3.is_available = lambda: False
    with caplog.at_level(logging.ERROR):
        result = await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN)
    assert result is None  # not the raw file path, which would read as success
    assert _files(fx.dir) == []
    assert any("recording_orphan_removed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_s3_object_is_deleted_when_the_row_insert_fails(fx, caplog):
    with caplog.at_level(logging.ERROR):
        result = await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN)
    assert result is None
    fx.s3.upload.assert_called_once()
    key = fx.s3.upload.call_args.args[0]
    fx.s3.delete_permanently.assert_called_once_with(key, "synthetic")
    assert any("recording_orphan_removed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_object_kept_when_an_existing_row_references_the_same_key(fx):
    fx.conn.fetchval = AsyncMock(return_value=True)  # duplicate / raced insert
    fx.s3.is_available = lambda: False
    await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN)
    assert len(_files(fx.dir)) == 1
    sql, *args = fx.conn.fetchval.call_args.args
    assert "tenant_id = $1" in sql and "s3_key" in sql
    assert args[0] == UUID(TENANT)


@pytest.mark.asyncio
async def test_object_kept_and_logged_when_reference_cannot_be_verified(fx, caplog):
    fx.conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.ERROR):
        await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN)
    fx.s3.delete_permanently.assert_not_called()
    assert any("recording_orphan_unverified" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_successful_insert_keeps_the_object(fx):
    fx.service._insert_recording_record = AsyncMock(return_value=UUID(RECORDING))
    result = await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN)
    assert result == RECORDING
    fx.s3.delete_permanently.assert_not_called()


@pytest.mark.asyncio
async def test_failed_marker_uses_the_resolved_call_id_and_logs(fx, caplog):
    service = fx.service
    service._mark_upload_failed = recording.RecordingService._mark_upload_failed.__get__(service)
    resolved = UUID("50000000-0000-4000-8000-000000000007")
    fx.conn.fetchrow = AsyncMock(return_value={"id": resolved})
    await service._mark_upload_failed("talky-out-pbx-channel", TENANT, CAMPAIGN, "boom")
    insert_args = fx.conn.execute.call_args.args
    assert insert_args[1] == resolved  # calls.id, not UUID("talky-out-pbx-channel")

    fx.conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level(logging.WARNING):
        await service._mark_upload_failed(CALL, TENANT, CAMPAIGN, "boom")
    assert any("recording_failed_row_not_written" in r.getMessage() for r in caplog.records)
