"""Re-recording a note must never leave the call without one.

WHY THIS EXISTS
---------------
A call keeps exactly one feedback note, enforced by a UNIQUE constraint on
``call_feedback.call_id``. That constraint originally had no escape hatch: once
a note existed, a second recording raised ``FeedbackAlreadyExistsError`` for the
lifetime of the call. A reviewer who misspoke was stuck with it.

The obvious fix — a DELETE endpoint, then POST the new one — is the wrong shape.
Between the two requests the call has no note at all, and if the upload then
fails (network, 413, a closed laptop) the reviewer has lost the recording they
were trying to improve on. Destroying good audio to make room for audio that
never arrives is worse than the problem being solved.

So the swap is one transaction, and the ordering is the thing under test:

    new object stored  ->  row swapped (atomic)  ->  OLD object deleted

The old object is deleted only after the swap has committed. An orphaned object
costs a few kilobytes; a committed row pointing at a deleted object costs the
note itself. Those two failures are not symmetric, and the tests below exist to
keep them from being treated as though they were.
"""

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
    StoredFeedbackAudio,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CALL_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"
OLD_ID = "44444444-4444-4444-4444-444444444444"

OLD_AUDIO = b"the-first-take-with-a-stumble-in-it"
NEW_AUDIO = b"the-second-take-which-actually-says-something"

OLD_STORED = StoredFeedbackAudio("s3", "feedback-test", f"feedback/{OLD_ID}.webm")


def _row(feedback_id: str, audio: bytes, key: str) -> dict:
    now = datetime.now(UTC)
    return {
        "id": feedback_id,
        "tenant_id": TENANT_ID,
        "call_id": CALL_ID,
        "created_by": USER_ID,
        "audio_storage_provider": "s3",
        "audio_bucket": "feedback-test",
        "audio_key": key,
        "audio_mime_type": "audio/webm",
        "audio_size_bytes": len(audio),
        "audio_sha256": hashlib.sha256(audio).hexdigest(),
        "duration_seconds": 3.0,
        "transcript": None,
        "transcript_status": "pending",
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
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.row: dict | None = _row(OLD_ID, OLD_AUDIO, OLD_STORED.key)
        self.replace_error: Exception | None = None
        # None models "someone else replaced this note first".
        self.replace_returns_none = False

    async def get_call_context(self, tenant_id: str, call_id: str):
        return {"id": CALL_ID, "campaign_id": None}

    async def get_for_call(self, tenant_id: str, call_id: str):
        return dict(self.row) if self.row else None

    async def get_by_id(self, tenant_id: str, feedback_id: str):
        return dict(self.row) if self.row else None

    async def insert(self, **kwargs):  # pragma: no cover — replace path only
        raise AssertionError("insert must not be used when a note already exists")

    async def replace(self, **kwargs):
        self.events.append("row_swapped")
        if self.replace_error:
            raise self.replace_error
        if self.replace_returns_none:
            return None
        new = _row(kwargs["feedback_id"], NEW_AUDIO, kwargs["stored"].key)
        new["audio_sha256"] = kwargs["audio_sha256"]
        self.row = new
        return dict(new), OLD_STORED

    async def begin_initial_attempt(self, tenant_id: str, feedback_id: str):
        self.events.append("attempt_claimed")
        assert self.row is not None
        self.row["transcription_attempts"] += 1
        return dict(self.row)

    async def mark_done(self, tenant_id: str, feedback_id: str, result):
        assert self.row is not None
        self.row.update(transcript=result.text, transcript_status="done")
        return dict(self.row)

    async def mark_failed(self, tenant_id: str, feedback_id: str, error: str):
        assert self.row is not None
        self.row.update(transcript_status="failed", transcript_error=error)
        return dict(self.row)


class FakeStorage:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.saved: list[str] = []
        self.deleted: list[StoredFeedbackAudio] = []
        self.save_error: Exception | None = None

    async def save(self, **kwargs) -> StoredFeedbackAudio:
        if self.save_error:
            raise self.save_error
        self.events.append("audio_stored")
        key = f"feedback/{kwargs['feedback_id']}.webm"
        self.saved.append(key)
        return StoredFeedbackAudio("s3", "feedback-test", key)

    async def read(self, row) -> bytes:
        return NEW_AUDIO

    async def delete(self, stored: StoredFeedbackAudio) -> None:
        self.events.append(
            "old_object_deleted" if stored.key == OLD_STORED.key else "new_object_deleted"
        )
        self.deleted.append(stored)

    def presigned_url(self, row):
        return "https://storage.invalid/signed"


class FakeTranscriber:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def transcribe(self, **kwargs) -> PrerecordedTranscript:
        self.events.append("deepgram_called")
        self.calls += 1
        return PrerecordedTranscript(
            text="Second take.", provider_request_id="dg-2", duration_seconds=3.0
        )


def _service():
    events: list[str] = []
    repository = FakeRepository(events)
    storage = FakeStorage(events)
    transcriber = FakeTranscriber(events)
    service = CallFeedbackService(
        object(), repository=repository, storage=storage, transcriber=transcriber
    )
    return service, repository, storage, transcriber, events


async def _submit(service, *, audio=NEW_AUDIO, replace=False):
    return await service.submit(
        tenant_id=TENANT_ID,
        call_id=CALL_ID,
        created_by=USER_ID,
        audio=audio,
        mime_type="audio/webm",
        duration_seconds=3.0,
        replace=replace,
    )


# ── the default is still to refuse ──────────────────────────────────────────


async def test_a_different_recording_without_replace_still_conflicts():
    """The load-bearing negative. Without an explicit intent to replace, one
    reviewer must not silently overwrite another's note."""
    service, _, storage, transcriber, _ = _service()

    with pytest.raises(FeedbackAlreadyExistsError):
        await _submit(service, replace=False)

    assert storage.saved == [], "audio was stored for a submission that was refused"
    assert transcriber.calls == 0


async def test_resending_identical_audio_is_idempotent_not_a_replacement():
    """A retried upload (flaky connection, double-click) must not consume a
    replacement or re-run transcription — it is the same note arriving twice."""
    service, repository, storage, transcriber, _ = _service()

    result = await _submit(service, audio=OLD_AUDIO, replace=False)

    assert result["id"] == OLD_ID
    assert storage.saved == []
    assert transcriber.calls == 0
    assert repository.row is not None and repository.row["id"] == OLD_ID


# ── the swap, and its ordering ──────────────────────────────────────────────


async def test_replace_supersedes_the_note():
    service, repository, _, transcriber, _ = _service()

    result = await _submit(service, replace=True)

    assert result["id"] != OLD_ID
    assert result["audio_sha256"] == hashlib.sha256(NEW_AUDIO).hexdigest()
    assert transcriber.calls == 1
    assert repository.row is not None and repository.row["id"] == result["id"]


async def test_the_old_audio_is_deleted_only_after_the_swap_commits():
    """THE ORDERING THAT MATTERS.

    Deleting the old object first would leave a window — and, if the swap then
    failed, a permanent state — where the committed row points at audio that no
    longer exists. The reviewer's note would be gone with nothing to show for it.
    """
    service, _, _, _, events = _service()

    await _submit(service, replace=True)

    assert events.index("audio_stored") < events.index("row_swapped"), (
        "the new object must exist before the row that references it"
    )
    assert events.index("row_swapped") < events.index("old_object_deleted"), (
        "the superseded audio was deleted before the swap committed"
    )
    assert events.index("old_object_deleted") < events.index("deepgram_called"), (
        "transcription ran before the storage swap was finished"
    )


async def test_deepgram_is_only_reached_after_the_row_is_committed():
    """Same durable-first invariant as the original submit path: a provider
    failure may cost a transcript, never a recording."""
    service, _, _, _, events = _service()

    await _submit(service, replace=True)

    assert events.index("row_swapped") < events.index("deepgram_called")


# ── failure paths keep the existing note ────────────────────────────────────


async def test_a_failed_swap_cleans_up_the_new_object_and_keeps_the_old_note():
    service, repository, storage, _, _ = _service()
    repository.replace_error = RuntimeError("deadlock")

    with pytest.raises(RuntimeError):
        await _submit(service, replace=True)

    assert [d.key for d in storage.deleted] == storage.saved, (
        "the orphaned new object was not cleaned up"
    )
    assert OLD_STORED not in storage.deleted, "the old audio was destroyed by a failed swap"
    assert repository.row is not None and repository.row["id"] == OLD_ID


async def test_storage_failure_never_touches_the_existing_note():
    service, repository, storage, _, _ = _service()
    storage.save_error = RuntimeError("bucket unreachable")

    with pytest.raises(RuntimeError):
        await _submit(service, replace=True)

    assert storage.deleted == []
    assert repository.row is not None and repository.row["id"] == OLD_ID


async def test_losing_a_race_discards_our_recording_rather_than_clobbering_theirs():
    """Two reviewers re-record the same call at once. The repository reports the
    row we meant to supersede is no longer current; the correct answer is to
    drop our own upload, not to overwrite the winner."""
    service, repository, storage, transcriber, _ = _service()
    repository.replace_returns_none = True

    with pytest.raises(FeedbackAlreadyExistsError):
        await _submit(service, replace=True)

    assert [d.key for d in storage.deleted] == storage.saved, (
        "our unlinked object should have been cleaned up"
    )
    assert OLD_STORED not in storage.deleted
    assert transcriber.calls == 0
