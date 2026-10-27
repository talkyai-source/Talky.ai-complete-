"""Actual migrated PostgreSQL attempt fencing; provider responses are synthetic."""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.interfaces.prerecorded_transcription_provider import PrerecordedTranscript
from app.domain.services.call_feedback_service import CallFeedbackRepository, CallFeedbackService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def feedback_db():
    dsn = os.getenv('TEST_ADMIN_DATABASE_URL') or os.getenv('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Explicit disposable local TEST_ADMIN_DATABASE_URL required')
    parsed = urlparse(dsn)
    assert parsed.hostname in {'localhost', '127.0.0.1'} and parsed.path.endswith('_test')
    owner = await asyncpg.connect(dsn)
    tenant, call, user, feedback, campaign = (uuid4() for _ in range(5))
    role = 'ad15_' + uuid4().hex
    pool = None
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT,UPDATE ON call_feedback TO "{role}"')
        await owner.execute("INSERT INTO tenants(id,business_name) VALUES($1,'AD15 isolated fixture')", tenant)
        await owner.execute("INSERT INTO user_profiles(id,tenant_id,email,role) VALUES($1,$2,$3,'user')", user, tenant, f'{user}@example.invalid')
        await owner.execute("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'AD15 fixture campaign')", campaign, tenant)
        await owner.execute("INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status) VALUES($1,$2,$3,'+15555550100','completed')", call, tenant, campaign)
        await owner.execute("""INSERT INTO call_feedback(id,tenant_id,call_id,created_by,audio_storage_provider,
            audio_bucket,audio_key,audio_mime_type,audio_size_bytes,audio_sha256)
            VALUES($1,$2,$3,$4,'s3','synthetic',$5,'audio/webm',1,$6)""",
            feedback, tenant, call, user, str(feedback), '0' * 64)

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='10s'")

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            assert tuple(flags.values()) == (False, False)
            assert await conn.fetchval('SELECT count(*) FROM call_feedback WHERE id=$1', feedback) == 0
        yield SimpleNamespace(owner=owner, pool=pool, tenant=str(tenant), feedback=str(feedback),
                              repo=CallFeedbackRepository(pool))
    finally:
        if pool:
            await pool.close()
        await owner.execute('DELETE FROM call_feedback WHERE id=$1', feedback)
        await owner.execute('DELETE FROM calls WHERE id=$1', call)
        await owner.execute('DELETE FROM campaigns WHERE id=$1', campaign)
        await owner.execute('DELETE FROM user_profiles WHERE id=$1', user)
        await owner.execute('DELETE FROM tenants WHERE id=$1', tenant)
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


@pytest.mark.parametrize('late_outcome', ['done', 'failed', 'cancelled'])
@pytest.mark.parametrize('newer_done', [False, True])
async def test_old_attempt_cannot_overwrite_new_claim(feedback_db, late_outcome, newer_done):
    db = feedback_db
    original = await db.repo.begin_initial_attempt(db.tenant, db.feedback)
    assert original['transcription_attempts'] == 1
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_transcribe(**_):
        started.set()
        await release.wait()
        if late_outcome == 'failed':
            raise RuntimeError('synthetic old provider failure')
        return PrerecordedTranscript(text='obsolete attempt', provider_request_id='old', duration_seconds=10)

    service = CallFeedbackService(db.pool, repository=db.repo, storage=object(),
                                  transcriber=SimpleNamespace(transcribe=delayed_transcribe))
    task = asyncio.create_task(service._transcribe_claimed(db.tenant, original, b'synthetic'))
    try:
        await asyncio.wait_for(started.wait(), 3)
        # Advance only this fixture's claim age; no wall-clock sleep or shortened production lease.
        await db.owner.execute("UPDATE call_feedback SET transcription_started_at=NOW()-INTERVAL '2 minutes' WHERE id=$1::uuid", db.feedback)
        newer = await db.repo.begin_retry_attempt(db.tenant, db.feedback, stale_after_seconds=30)
        assert newer['transcription_attempts'] == 2
        if newer_done:
            async def current_transcribe(**_):
                return PrerecordedTranscript(text='current attempt', provider_request_id='new', duration_seconds=3)
            current = CallFeedbackService(db.pool, repository=db.repo, storage=object(),
                                         transcriber=SimpleNamespace(transcribe=current_transcribe))
            await current._transcribe_claimed(db.tenant, newer, b'synthetic')
        before = await db.repo.get_by_id(db.tenant, db.feedback)
        if late_outcome == 'cancelled':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            returned = await asyncio.wait_for(task, 3)
            assert returned == before, 'Losing request must return the current durable row'
        after = await db.repo.get_by_id(db.tenant, db.feedback)
        assert after == before, 'Old success, failure or cancellation must not mutate a newer claim'
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
