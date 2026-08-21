"""Unit coverage for the durable-first call feedback workflow."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app.domain.interfaces.prerecorded_transcription_provider import (
    PrerecordedTranscript,
)
from app.domain.services.call_feedback_service import (
    CallFeedbackService,
    FeedbackAlreadyExistsError,
    FeedbackAudioStorage,
    FeedbackStorageError,
    StoredFeedbackAudio,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CALL_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"
FEEDBACK_ID = "44444444-4444-4444-4444-444444444444"
AUDIO = b"short-media-recorder-audio"


def _row(*, status: str = "pending", digest: str | None = None) -> dict:
    now = datetime.now(UTC)
    return {
        "id": FEEDBACK_ID,
        "tenant_id": TENANT_ID,
        "call_id": CALL_ID,
        "created_by": USER_ID,
        "audio_storage_provider": "s3",
        "audio_bucket": "feedback-test",
        "audio_key": f"feedback/{TENANT_ID}/{CALL_ID}/{FEEDBACK_ID}.webm",
        "audio_mime_type": "audio/webm",
        "audio_size_bytes": len(AUDIO),
        "audio_sha256": digest or hashlib.sha256(AUDIO).hexdigest(),
        "duration_seconds": 2.5,
        "transcript": None,
        "transcript_status": status,
        "transcript_error": None,
        "transcription_attempts": 0,
        "transcript_provider": "deepgram",
        "transcript_provider_request_id": None,
        "transcription_started_at": None,
        "transcribed_at": None,
        "created_at": now,
        "updated_at": now,
    }


class FakeRepository:
    def __init__(self, events: list[str], existing: dict | None = None) -> None:
        self.events = events
        self.row = existing
        self.insert_error: Exception | None = None
        self.initial_claim_error: Exception | None = None

    async def get_call_context(self, tenant_id: str, call_id: str):
        return {"id": CALL_ID, "campaign_id": None}

    async def get_for_call(self, tenant_id: str, call_id: str):
        return dict(self.row) if self.row else None

    async def get_by_id(self, tenant_id: str, feedback_id: str):
        return dict(self.row) if self.row else None

    async def insert(self, **kwargs):
        self.events.append("row_committed")
        if self.insert_error:
            raise self.insert_error
        self.row = _row(digest=kwargs["audio_sha256"])
        self.row["id"] = kwargs["feedback_id"]
        self.row["audio_key"] = kwargs["stored"].key
        return dict(self.row)

    async def begin_initial_attempt(self, tenant_id: str, feedback_id: str):
        self.events.append("attempt_claimed")
        if self.initial_claim_error:
            raise self.initial_claim_error
        self.row["transcription_attempts"] += 1
        return dict(self.row)

    async def begin_retry_attempt(
        self, tenant_id: str, feedback_id: str, *, stale_after_seconds: int
    ):
        self.events.append("retry_claimed")
        self.row["transcript_status"] = "pending"
        self.row["transcription_attempts"] += 1
        return dict(self.row)

    async def mark_done(self, tenant_id: str, feedback_id: str, result):
        self.events.append("marked_done")
        self.row.update(
            transcript=result.text,
            transcript_status="done",
            transcript_error=None,
            transcript_provider_request_id=result.provider_request_id,
        )
        return dict(self.row)

    async def mark_failed(self, tenant_id: str, feedback_id: str, error: str):
        self.events.append("marked_failed")
        self.row.update(transcript_status="failed", transcript_error=error)
        return dict(self.row)


class FakeStorage:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.deleted: list[StoredFeedbackAudio] = []

    async def save(self, **kwargs) -> StoredFeedbackAudio:
        self.events.append("audio_stored")
        return StoredFeedbackAudio("s3", "feedback-test", "feedback/object.webm")

    async def read(self, row) -> bytes:
        self.events.append("audio_read")
        return AUDIO

    async def delete(self, stored: StoredFeedbackAudio) -> None:
        self.events.append("orphan_deleted")
        self.deleted.append(stored)

    def presigned_url(self, row):
        return "https://storage.invalid/signed"


class FakeTranscriber:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    async def transcribe(self, **kwargs) -> PrerecordedTranscript:
        self.events.append("deepgram_called")
        self.calls += 1
        if self.error:
            raise self.error
        return PrerecordedTranscript(
            text="The customer asked for a callback.",
            provider_request_id="dg-request-1",
            duration_seconds=2.5,
        )


def _service(*, existing: dict | None = None, transcriber_error: Exception | None = None):
    events: list[str] = []
    repository = FakeRepository(events, existing=existing)
    storage = FakeStorage(events)
    transcriber = FakeTranscriber(events, error=transcriber_error)
    service = CallFeedbackService(
        object(),
        repository=repository,
        storage=storage,
        transcriber=transcriber,
    )
    return service, repository, storage, transcriber, events


async def test_submit_commits_audio_and_row_before_calling_deepgram():
    service, _, _, _, events = _service()

    result = await service.submit(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
        created_by=USER_ID,
        audio=AUDIO,
        mime_type="audio/webm",
        duration_seconds=2.5,
    )

    assert result["transcript_status"] == "done"
    assert result["transcript"] == "The customer asked for a callback."
    assert events == [
        "audio_stored",
        "row_committed",
        "attempt_claimed",
        "deepgram_called",
        "marked_done",
    ]


async def test_provider_failure_keeps_recording_and_returns_failed_row():
    service, repository, storage, _, events = _service(
        transcriber_error=RuntimeError("provider unavailable")
    )

    result = await service.submit(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
        created_by=USER_ID,
        audio=AUDIO,
        mime_type="audio/webm",
    )

    assert result["transcript_status"] == "failed"
    assert result["transcript_error"] == "Transcription failed (RuntimeError)"
    assert repository.row is not None
    assert storage.deleted == []
    assert events[-1] == "marked_failed"


async def test_database_insert_failure_compensates_uploaded_object():
    service, repository, storage, transcriber, events = _service()
    repository.insert_error = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.submit(
            tenant_id=TENANT_ID,
            call_id=CALL_ID,
            created_by=USER_ID,
            audio=AUDIO,
            mime_type="audio/webm",
        )

    assert len(storage.deleted) == 1
    assert transcriber.calls == 0
    assert events == ["audio_stored", "row_committed", "orphan_deleted"]


async def test_attempt_claim_failure_returns_the_already_durable_pending_note():
    service, repository, storage, transcriber, events = _service()
    repository.initial_claim_error = RuntimeError("database briefly unavailable")

    result = await service.submit(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
        created_by=USER_ID,
        audio=AUDIO,
        mime_type="audio/webm",
    )

    assert result["transcript_status"] == "pending"
    assert repository.row is not None
    assert storage.deleted == []
    assert transcriber.calls == 0
    assert events == ["audio_stored", "row_committed", "attempt_claimed"]


async def test_identical_resubmission_is_idempotent_and_costs_no_second_stt_call():
    service, _, storage, transcriber, events = _service(existing=_row())

    result = await service.submit(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
        created_by=USER_ID,
        audio=AUDIO,
        mime_type="audio/webm",
    )

    assert result["id"] == FEEDBACK_ID
    assert storage.deleted == []
    assert transcriber.calls == 0
    assert events == []


async def test_different_second_note_is_rejected_without_overwriting_first():
    service, _, _, transcriber, events = _service(existing=_row())

    with pytest.raises(FeedbackAlreadyExistsError):
        await service.submit(
            tenant_id=TENANT_ID,
            call_id=CALL_ID,
            created_by=USER_ID,
            audio=b"different note",
            mime_type="audio/webm",
        )

    assert transcriber.calls == 0
    assert events == []


async def test_retry_reads_durable_audio_and_reuses_same_transcription_path():
    failed = _row(status="failed")
    failed["transcription_attempts"] = 1
    service, _, _, _, events = _service(existing=failed)

    result = await service.retry_transcription(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
    )

    assert result["transcript_status"] == "done"
    assert result["transcription_attempts"] == 2
    assert events == [
        "retry_claimed",
        "audio_read",
        "deepgram_called",
        "marked_done",
    ]


async def test_production_never_acknowledges_ephemeral_local_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    class UnavailableS3:
        def is_available(self) -> bool:
            return False

    monkeypatch.setenv("ENVIRONMENT", "production")
    storage = FeedbackAudioStorage(
        s3_client=UnavailableS3(),  # type: ignore[arg-type]
        local_dir=str(tmp_path),
    )

    with pytest.raises(FeedbackStorageError, match="not configured"):
        await storage.save(
            tenant_id=TENANT_ID,
            call_id=CALL_ID,
            feedback_id=FEEDBACK_ID,
            mime_type="audio/webm",
            audio=AUDIO,
        )

    assert list(tmp_path.iterdir()) == []
