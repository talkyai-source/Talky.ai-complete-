"""Normal-auth media deletion admission on owned migrated PostgreSQL records.

Authentication, administrator changes, deletion intents and locks are real.
Only the irreversible storage eraser is synthetic; no provider object is used.
The claim-entry barrier runs after the normal HTTP dependency has resolved and
does not replace the authority predicate being tested.
"""

import asyncio
import re
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.api.v1.endpoints.admin import media
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    reviewed_write,
    signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
REASON = "Synthetic owned authority admission check"


@pytest_asyncio.fixture
async def media_authority_db(crud_db, monkeypatch):  # noqa: F811
    db = crud_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(
        f'GRANT SELECT,INSERT,UPDATE,DELETE ON recordings_s3,call_feedback,'
        f'admin_media_deletion_intents,admin_media_deletion_request_keys TO "{role}"'
    )
    await db.owner.execute(f'GRANT UPDATE ON calls TO "{role}"')
    # Migration0041's outbound artifact trigger takes FOR SHARE on campaigns,
    # which requires a write privilege in addition to the inherited SELECT.
    await db.owner.execute(f'GRANT UPDATE ON campaigns TO "{role}"')
    # The existing calls recording_url update executes its CRM enqueue trigger.
    await db.owner.execute(f'GRANT SELECT ON connectors TO "{role}"')
    await db.owner.execute(f'GRANT SELECT,INSERT,UPDATE ON crm_deliveries TO "{role}"')
    # A second independent administrator can remove the requesting operator's
    # authority through the actual reviewed Admin HTTP contract.
    await db.owner.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[2]
    )
    db.media_call_id, db.recording_id, db.feedback_id = uuid4(), uuid4(), uuid4()
    db.media_campaign_id = uuid4()
    db.eraser_calls = []
    db.storage_fails = False

    class SyntheticS3:
        def is_available(self):
            return True

        def delete_permanently(self, key, bucket):
            db.eraser_calls.append({"kind": "recording", "key": key, "bucket": bucket})
            if db.storage_fails:
                raise RuntimeError("Synthetic object erasure unavailable")

    async def erase_feedback(_storage, stored):
        db.eraser_calls.append({"kind": "feedback", "key": stored.key, "bucket": stored.bucket})
        if db.storage_fails:
            raise RuntimeError("Synthetic object erasure unavailable")

    monkeypatch.setattr(media, "S3Client", SyntheticS3)
    monkeypatch.setattr(media.FeedbackAudioStorage, "delete", erase_feedback)
    complete_recording = media._complete_recording_deletion

    async def observed_complete(*args, **kwargs):
        try:
            return await complete_recording(*args, **kwargs)
        except Exception as exc:
            # This exact owned operation only uses resource UUIDs and synthetic
            # object paths. Record the missing relation, never auth parameters.
            RUN_EVIDENCE.append({"case": "recording_finalization_failure",
                                 "type": type(exc).__name__, "message": str(exc)[:500]})
            raise

    monkeypatch.setattr(media, "_complete_recording_deletion", observed_complete)
    try:
        await db.owner.execute(
            "INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic media authority')",
            db.media_campaign_id, db.tenants[1],
        )
        await db.owner.execute(
            "INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status) "
            "VALUES($1,$2,$3,'+15555550100','completed')",
            db.media_call_id,
            db.tenants[1],
            db.media_campaign_id,
        )
        await db.owner.execute(
            """INSERT INTO recordings_s3(id,tenant_id,call_id,s3_bucket,s3_key,status)
               VALUES($1,$2,$3,'synthetic-authority-media',$4,'uploaded')""",
            db.recording_id,
            db.tenants[1],
            db.media_call_id,
            f"recording/{db.recording_id}.wav",
        )
        await db.owner.execute(
            """INSERT INTO call_feedback(id,tenant_id,call_id,created_by,
               audio_storage_provider,audio_bucket,audio_key,audio_mime_type,
               audio_size_bytes,audio_sha256,transcript_status)
               VALUES($1,$2,$3,$4,'s3','synthetic-authority-media',$5,
                      'audio/webm',4,$6,'failed')""",
            db.feedback_id,
            db.tenants[1],
            db.media_call_id,
            db.users[1],
            f"feedback/{db.feedback_id}.webm",
            "a" * 64,
        )
        yield db
    finally:
        # These are exact fixture UUIDs, including objects created by triggers.
        # Parent journey_db verifies the complete protected snapshot afterwards.
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            intent_ids = await db.owner.fetch(
                "SELECT id FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id)
            old_replication = await db.owner.fetchval("SHOW session_replication_role")
            # Only the disposable owner can remove the exact immutable fixture
            # intent/key records. Restore triggers before ordinary FK cleanup.
            await db.owner.execute("SET LOCAL session_replication_role='replica'")
            await db.owner.execute(
                "DELETE FROM admin_media_deletion_request_keys WHERE intent_id=ANY($1::uuid[])",
                [row["id"] for row in intent_ids],
            )
            await db.owner.execute(
                "DELETE FROM admin_media_deletion_intents WHERE call_id=$1 AND id=ANY($2::uuid[])",
                db.media_call_id, [row["id"] for row in intent_ids],
            )
            await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", old_replication)
            assert await db.owner.fetchval("SHOW session_replication_role") == old_replication
            await db.owner.execute("DELETE FROM call_feedback WHERE id=$1", db.feedback_id)
            await db.owner.execute("DELETE FROM recordings_s3 WHERE id=$1", db.recording_id)
            await db.owner.execute("DELETE FROM crm_deliveries WHERE call_id=$1", db.media_call_id)
            await db.owner.execute("DELETE FROM call_events WHERE call_id=$1", db.media_call_id)
            await db.owner.execute("DELETE FROM call_legs WHERE call_id=$1", db.media_call_id)
            await db.owner.execute("DELETE FROM calls WHERE id=$1", db.media_call_id)
            await db.owner.execute("DELETE FROM campaigns WHERE id=$1", db.media_campaign_id)


def resource(db, kind):
    return ("recording", db.recording_id) if kind == "recordings" else ("call_feedback", db.feedback_id)


async def remove_authority(db, second, mode="demote"):
    if mode == "demote":
        result = await reviewed_write(
            db, second, "PATCH", db.users[0], role="tenant_admin", expected_role="platform_admin"
        )
    else:
        result = await reviewed_write(db, second, "PATCH", db.users[0], is_active=False)
    assert result.status_code == 200, result.text


async def delete(db, client, kind, key):
    _, media_id = resource(db, kind)
    return await call(
        db, client, "DELETE", f"/admin/{kind}/{media_id}",
        json={"reason": REASON}, headers={"Idempotency-Key": key},
    )


def pause_claim(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = media._claim_media_deletion

    async def paused(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(*args, **kwargs)

    monkeypatch.setattr(media, "_claim_media_deletion", paused)
    return entered, release


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_revoked_actor_cannot_admit_delete_after_normal_authentication(
    media_authority_db, monkeypatch, kind,
):
    """Expected to fail on baseline: it returns200 and erases after demotion."""
    db = media_authority_db
    entered, release = pause_claim(monkeypatch)
    async with signed_in(db) as first, signed_in(db, 2) as second:
        task = asyncio.create_task(delete(db, first, kind, "media-authority:" + uuid4().hex))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await remove_authority(db, second)
        finally:
            release.set()
            result = await asyncio.wait_for(task, 15)
        intents = await db.owner.fetchval(
            "SELECT count(*) FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id
        )
        RUN_EVIDENCE.append({"case": "authority_removed_after_dependency", "kind": kind,
                             "status": result.status_code, "durable_intents": intents,
                             "synthetic_eraser_calls": len(db.eraser_calls)})
        assert result.status_code == 403, result.text
        assert intents == 0 and not db.eraser_calls


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_authorized_delete_commits_intent_and_same_key_replay_has_no_second_effect(
    media_authority_db, kind,
):
    db = media_authority_db
    key = "media-authority:" + uuid4().hex
    async with signed_in(db) as admin:
        first = await delete(db, admin, kind, key)
        assert first.status_code == 200, first.text
        intent_id = UUID(first.json()["deletion_intent_id"])
        assert await db.owner.fetchval(
            "SELECT status FROM admin_media_deletion_intents WHERE id=$1", intent_id
        ) == "completed"
        replay = await delete(db, admin, kind, key)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert len(db.eraser_calls) == 1


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
@pytest.mark.parametrize("stage", ["failed", "completed"])
async def test_retry_and_completed_replay_need_current_actor_admission(
    media_authority_db, monkeypatch, kind, stage,
):
    db = media_authority_db
    key = "media-authority:" + uuid4().hex
    db.storage_fails = stage == "failed"
    async with signed_in(db) as first, signed_in(db, 2) as second:
        initial = await delete(db, first, kind, key)
        assert initial.status_code == (503 if stage == "failed" else 200), initial.text
        before = await db.owner.fetchrow(
            "SELECT id,status,attempt_count FROM admin_media_deletion_intents WHERE call_id=$1",
            db.media_call_id,
        )
        assert before and before["status"] == stage
        db.storage_fails = False
        entered, release = pause_claim(monkeypatch)
        task = asyncio.create_task(delete(db, first, kind, key))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await remove_authority(db, second)
        finally:
            release.set()
            response = await asyncio.wait_for(task, 15)
        after = await db.owner.fetchrow(
            "SELECT id,status,attempt_count FROM admin_media_deletion_intents WHERE call_id=$1",
            db.media_call_id,
        )
        assert response.status_code == 403, response.text
        assert dict(after) == dict(before) and len(db.eraser_calls) == 1


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_committed_admission_releases_authority_lock_before_storage_io(
    media_authority_db, monkeypatch, kind,
):
    """A later demotion cannot undo a previously admitted durable deletion."""
    db = media_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = media._execute_serialized_media_deletion

    async def paused(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 15)
        return await original(*args, **kwargs)

    monkeypatch.setattr(media, "_execute_serialized_media_deletion", paused)
    async with signed_in(db) as first, signed_in(db, 2) as second:
        task = asyncio.create_task(delete(db, first, kind, "media-authority:" + uuid4().hex))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert await db.owner.fetchval(
                "SELECT status FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id
            ) == "intent_committed"
            async with db.owner.transaction():
                assert await db.owner.fetchval(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended('talky:platform-admin-authority',0))"
                ) is True
            # A real competing authority write completes while storage is paused.
            await asyncio.wait_for(remove_authority(db, second), 8)
        finally:
            release.set()
            response = await asyncio.wait_for(task, 15)
        assert response.status_code == 200, response.text
        assert len(db.eraser_calls) == 1
        assert await db.owner.fetchval(
            "SELECT status FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id
        ) == "completed"


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_authority_read_failure_cannot_admit_deletion(media_authority_db, monkeypatch, kind):
    """A real SQL permission error after route auth is unavailable, never allowed."""
    db = media_authority_db
    entered, release = pause_claim(monkeypatch)
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    async with signed_in(db) as admin:
        task = asyncio.create_task(delete(db, admin, kind, "media-authority:" + uuid4().hex))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            await db.owner.execute(f'REVOKE SELECT ON tenant_users FROM "{role}"')
            release.set()
            response = await asyncio.wait_for(task, 15)
            intents = await db.owner.fetchval(
                "SELECT count(*) FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id)
            RUN_EVIDENCE.append({"case": "authority_read_unavailable", "kind": kind,
                                 "status": response.status_code, "durable_intents": intents,
                                 "synthetic_eraser_calls": len(db.eraser_calls)})
            assert response.status_code == 503, response.text
            assert intents == 0 and not db.eraser_calls
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db.owner.execute(f'GRANT SELECT ON tenant_users TO "{role}"')


async def test_admin_media_row_lock_timeout_releases_authority_fence(media_authority_db):
    db = media_authority_db
    async with signed_in(db) as admin:
        async with db.owner.transaction():
            await db.owner.fetchrow("SELECT id FROM recordings_s3 WHERE id=$1 FOR UPDATE", db.recording_id)
            started = asyncio.get_running_loop().time()
            response = await asyncio.wait_for(
                delete(db, admin, "recordings", "media-authority:" + uuid4().hex), 9)
            elapsed = asyncio.get_running_loop().time() - started
            assert response.status_code == 503, response.text
            assert 4.5 <= elapsed < 9, "The actual configured five-second admission budget must bound contention"
            assert not db.eraser_calls
            assert await db.owner.fetchval(
                "SELECT count(*) FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id) == 0
            assert await db.owner.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('talky:platform-admin-authority',0))") is True
            RUN_EVIDENCE.append({"case": "real_resource_lock_timeout", "status": response.status_code,
                                 "elapsed_seconds": elapsed, "intent_count": 0,
                                 "synthetic_eraser_calls": 0, "authority_fence_released": True})
