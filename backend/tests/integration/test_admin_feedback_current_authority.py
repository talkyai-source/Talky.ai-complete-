"""Admin retry admission uses real HTTP/PG; audio/transcription are synthetic."""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from app.domain.interfaces.prerecorded_transcription_provider import PrerecordedTranscript
from app.domain.services.call_feedback_service import CallFeedbackRepository, CallFeedbackService, FeedbackAudioStorage
from app.infrastructure.stt.deepgram_prerecorded import DeepgramPrerecordedTranscriber
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, crud_db, signed_in  # noqa: F401
from tests.integration.test_admin_media_current_authority import media_authority_db, remove_authority  # noqa: F401

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def feedback_authority_db(media_authority_db, monkeypatch):  # noqa: F811
    db = media_authority_db
    db.feedback_reads = []
    db.transcriptions = []

    async def read(_storage, row):
        db.feedback_reads.append(str(row["id"]))
        return b"synthetic reviewed audio"

    async def transcribe(_provider, **kwargs):
        db.transcriptions.append({"tenant_id": kwargs["tenant_id"], "mime_type": kwargs["mime_type"]})
        return PrerecordedTranscript(text="Synthetic transcription", provider_request_id="synthetic-local")

    monkeypatch.setattr(FeedbackAudioStorage, "read", read)
    monkeypatch.setattr(FeedbackAudioStorage, "presigned_url", lambda *_args: None)
    monkeypatch.setattr(DeepgramPrerecordedTranscriber, "transcribe", transcribe)
    yield db


async def retry(db, client):
    return await call(db, client, "POST", f"/admin/feedback/{db.feedback_id}/transcription/retry")


def pause_attempt(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = CallFeedbackRepository.begin_retry_attempt

    async def paused(repository, *args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(repository, *args, **kwargs)

    monkeypatch.setattr(CallFeedbackRepository, "begin_retry_attempt", paused)
    return entered, release


async def test_revoked_admin_cannot_claim_transcription_after_normal_admission(feedback_authority_db, monkeypatch):
    db = feedback_authority_db
    entered, release = pause_attempt(monkeypatch)
    async with signed_in(db) as actor, signed_in(db, 2) as revoker:
        task = asyncio.create_task(retry(db, actor))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await remove_authority(db, revoker)
        finally:
            release.set()
            response = await asyncio.wait_for(task, 15)
        row = await db.owner.fetchrow("SELECT transcript_status,transcription_attempts FROM call_feedback WHERE id=$1", db.feedback_id)
        RUN_EVIDENCE.append({"case": "retry_after_admin_demotion", "status": response.status_code,
                             "saved": dict(row), "audio_reads": len(db.feedback_reads),
                             "provider_calls_synthetic": len(db.transcriptions)})
        assert response.status_code == 403, response.text
        assert dict(row) == {"transcript_status": "failed", "transcription_attempts": 0}
        assert not db.feedback_reads and not db.transcriptions


async def test_admin_retry_does_not_transcribe_replacement_of_reviewed_feedback(feedback_authority_db, monkeypatch):
    db = feedback_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = CallFeedbackService.retry_transcription
    replacement = uuid4()

    async def paused(service, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(service, **kwargs)

    monkeypatch.setattr(CallFeedbackService, "retry_transcription", paused)
    async with signed_in(db) as admin:
        task = asyncio.create_task(retry(db, admin))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await db.owner.execute("UPDATE call_feedback SET id=$2 WHERE id=$1", db.feedback_id, replacement)
            release.set()
            response = await asyncio.wait_for(task, 15)
            row = await db.owner.fetchrow("SELECT transcript_status,transcription_attempts FROM call_feedback WHERE id=$1", replacement)
            RUN_EVIDENCE.append({"case": "reviewed_feedback_replaced", "status": response.status_code,
                                 "saved_replacement": dict(row), "audio_reads": len(db.feedback_reads),
                                 "provider_calls_synthetic": len(db.transcriptions)})
            assert response.status_code in {404, 409}, response.text
            assert not db.transcriptions and not db.feedback_reads
            assert dict(row) == {"transcript_status": "failed", "transcription_attempts": 0}
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.owner.execute("UPDATE call_feedback SET id=$2 WHERE id=$1", replacement, db.feedback_id)


async def test_current_admin_retry_commits_attempt_and_completed_replay_is_read_only(feedback_authority_db):
    db = feedback_authority_db
    async with signed_in(db) as admin:
        response = await retry(db, admin)
        assert response.status_code == 200, response.text
        assert response.json()["transcript_status"] == "done"
        replay = await retry(db, admin)
        assert replay.status_code == 200
        row = await db.owner.fetchrow("SELECT transcript,transcript_status,transcription_attempts FROM call_feedback WHERE id=$1", db.feedback_id)
        assert dict(row) == {"transcript": "Synthetic transcription", "transcript_status": "done", "transcription_attempts": 1}
        assert len(db.feedback_reads) == len(db.transcriptions) == 1


async def test_retry_lookup_failure_is_unavailable_before_attempt_or_provider(feedback_authority_db, monkeypatch):
    db = feedback_authority_db
    entered, release = pause_attempt(monkeypatch)
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    async with signed_in(db) as admin:
        task = asyncio.create_task(retry(db, admin))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await db.owner.execute(f'REVOKE SELECT ON tenant_users FROM "{role}"')
            release.set()
            response = await asyncio.wait_for(task, 15)
            assert response.status_code == 503, response.text
            assert await db.owner.fetchval("SELECT transcription_attempts FROM call_feedback WHERE id=$1", db.feedback_id) == 0
            assert not db.feedback_reads and not db.transcriptions
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.owner.execute(f'GRANT SELECT ON tenant_users TO "{role}"')


async def test_admitted_retry_releases_authority_fence_before_reading_audio(feedback_authority_db, monkeypatch):
    db = feedback_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = FeedbackAudioStorage.read

    async def paused(storage, row):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(storage, row)

    monkeypatch.setattr(FeedbackAudioStorage, "read", paused)
    async with signed_in(db) as actor, signed_in(db, 2) as revoker:
        task = asyncio.create_task(retry(db, actor))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            row = await db.owner.fetchrow("SELECT transcript_status,transcription_attempts FROM call_feedback WHERE id=$1", db.feedback_id)
            assert dict(row) == {"transcript_status": "pending", "transcription_attempts": 1}
            async with db.owner.transaction():
                assert await db.owner.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended('talky:platform-admin-authority',0))") is True
            await asyncio.wait_for(remove_authority(db, revoker), 8)
        finally:
            release.set()
            response = await asyncio.wait_for(task, 15)
        assert response.status_code == 200, response.text
        assert len(db.feedback_reads) == len(db.transcriptions) == 1
        assert await db.owner.fetchval("SELECT transcript_status FROM call_feedback WHERE id=$1", db.feedback_id) == "done"
        RUN_EVIDENCE.append({"case": "accepted_retry_before_demotion", "status": 200,
                             "durable_attempt_before_audio": True, "authority_lock_free_during_audio": True})


async def test_feedback_audio_uses_the_actual_expanded_storage_query(feedback_authority_db):
    db = feedback_authority_db
    async with signed_in(db) as admin:
        response = await call(db, admin, "GET", f"/admin/feedback/{db.feedback_id}/audio")
        assert response.status_code == 200, response.text
        assert response.content == b"synthetic reviewed audio"
        assert response.headers["content-type"] == "audio/webm"
        assert db.feedback_reads == [str(db.feedback_id)]
        assert not db.transcriptions
