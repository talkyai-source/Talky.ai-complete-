"""Retention purge for recordings: dry-run default, fail-closed selection, ordering."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import recordings
from app.api.v1.endpoints.admin import media
from app.workers import cleanup_worker
from app.workers import recording_retention_purge as purge

ACTOR = str(uuid4())


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return self.rows


def _row(tmp_path, *, bucket="local", name="a.wav", create=True):
    path = tmp_path / name
    if create:
        path.write_bytes(b"audio")
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "call_id": uuid4(),
        "s3_bucket": bucket,
        "s3_key": str(path),
        "retention_days": 30,
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    events: list[tuple] = []
    state = SimpleNamespace(rows=[], conn=None, claim_error=None)

    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant is None  # cross-tenant worker select; tenant bound by the JOIN
        state.conn = _Conn(state.rows)
        yield state.conn

    async def claim(_db, **kwargs):
        events.append(("claim", kwargs))
        if state.claim_error is not None:
            raise state.claim_error
        row = next(r for r in state.rows if r["id"] == kwargs["resource_id"])
        return media._DeletionClaim(
            intent_id=uuid4(), status="intent_committed", snapshot=dict(row)
        )

    async def execute(_db, claim, delete_operation):
        events.append(("execute", claim.snapshot["id"]))
        try:
            await delete_operation()
        except Exception as exc:  # mirrors the real state machine
            return exc
        events.append(("erased", claim.snapshot["id"]))
        return None

    async def complete(_db, **kwargs):
        events.append(("complete", kwargs))

    monkeypatch.setattr(purge, "acquire_with_tenant", acquire)
    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    monkeypatch.setattr(media, "_execute_serialized_media_deletion", execute)
    monkeypatch.setattr(media, "_complete_recording_deletion", complete)
    state.events = events
    return state


def test_purge_is_dry_run_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("RECORDING_RETENTION_PURGE_ENABLED", raising=False)
    assert purge.purge_enabled() is False
    for value in ("", "false", "0", "no", "banana"):
        monkeypatch.setenv("RECORDING_RETENTION_PURGE_ENABLED", value)
        assert purge.purge_enabled() is False
    for value in ("true", "1", "YES"):
        monkeypatch.setenv("RECORDING_RETENTION_PURGE_ENABLED", value)
        assert purge.purge_enabled() is True


@pytest.mark.asyncio
async def test_dry_run_counts_but_deletes_nothing(env, tmp_path):
    row = _row(tmp_path)
    env.rows = [row]
    summary = await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=10)
    assert summary["would_delete"] == 1 and summary["deleted"] == 0
    assert (tmp_path / "a.wav").exists()
    assert env.events == []


@pytest.mark.asyncio
async def test_selection_is_fail_closed_and_tenant_joined(env):
    await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=7)
    sql, args = env.conn.queries[0]
    flat = " ".join(sql.split())
    assert "JOIN tenant_recording_policy p ON p.tenant_id = r.tenant_id" in flat
    assert "LEFT JOIN tenant_recording_policy" not in flat
    assert "r.status = 'uploaded'" in flat
    assert "p.retention_days > 0" in flat
    assert "make_interval(days => p.retention_days)" in flat
    assert "suspension_events" in flat  # legal hold excluded up front
    assert args == (7,)


@pytest.mark.asyncio
async def test_live_purge_erases_object_before_row_and_scopes_tenant(env, tmp_path):
    row = _row(tmp_path)
    env.rows = [row]
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert summary["deleted"] == 1
    assert not (tmp_path / "a.wav").exists()
    kinds = [e[0] for e in env.events]
    assert kinds == ["claim", "execute", "erased", "complete"]
    claim_kwargs = env.events[0][1]
    assert claim_kwargs["expected_tenant_id"] == row["tenant_id"]
    assert claim_kwargs["resource_type"] == "recording"
    assert claim_kwargs["actor_id"] == ACTOR
    assert len(claim_kwargs["idempotency_key"]) >= 8
    complete_kwargs = env.events[-1][1]
    assert complete_kwargs["expected_tenant_id"] == row["tenant_id"]
    assert complete_kwargs["recording_id"] == row["id"]


@pytest.mark.asyncio
async def test_missing_object_is_idempotent(env, tmp_path):
    env.rows = [_row(tmp_path, create=False)]
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert summary["deleted"] == 1 and summary["failed"] == 0
    assert env.events[-1][0] == "complete"


@pytest.mark.asyncio
async def test_storage_failure_keeps_the_row(env, tmp_path, monkeypatch):
    env.rows = [_row(tmp_path, bucket="recordings")]

    class _S3:
        def is_available(self):
            return False

    monkeypatch.setattr(purge, "S3Client", _S3)
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert summary["failed"] == 1 and summary["deleted"] == 0
    assert "complete" not in [e[0] for e in env.events]


@pytest.mark.asyncio
async def test_s3_object_is_permanently_deleted_with_its_bucket(env, tmp_path, monkeypatch):
    row = _row(tmp_path, bucket="rec-bucket")
    row["s3_key"] = "tenant/call/audio.wav"
    env.rows = [row]
    calls = []

    class _S3:
        def is_available(self):
            return True

        def delete_permanently(self, key, bucket):
            calls.append((key, bucket))

    monkeypatch.setattr(purge, "S3Client", _S3)
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert calls == [("tenant/call/audio.wav", "rec-bucket")]
    assert summary["deleted"] == 1


@pytest.mark.asyncio
async def test_legal_hold_skips_without_touching_storage(env, tmp_path):
    env.rows = [_row(tmp_path)]
    env.claim_error = HTTPException(status_code=423, detail="hold")
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert summary["held"] == 1 and summary["deleted"] == 0
    assert (tmp_path / "a.wav").exists()


@pytest.mark.asyncio
async def test_claim_for_vanished_row_counts_as_already_gone(env, tmp_path, monkeypatch):
    env.rows = [_row(tmp_path)]

    async def gone(_db, **_kw):
        return None

    monkeypatch.setattr(media, "_claim_media_deletion", gone)
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=10)
    assert summary["already_gone"] == 1 and summary["deleted"] == 0


@pytest.mark.asyncio
async def test_live_purge_without_actor_fails_loudly(env, tmp_path):
    env.rows = [_row(tmp_path)]
    with pytest.raises(RuntimeError, match="RECORDING_RETENTION_PURGE_ACTOR_ID"):
        await purge.purge_expired_recordings(object(), enabled=True, actor_id=None, limit=10)
    assert (tmp_path / "a.wav").exists()


@pytest.mark.asyncio
async def test_cleanup_worker_runs_the_purge_and_reports_failure(monkeypatch):
    import app.core.db as db

    seen = {}

    async def init():
        return "pool"

    async def close():
        return None

    async def telemetry(pool, **_kw):
        return {}

    async def run_purge(pool, **kw):
        seen.update(kw, pool=pool)
        return {"would_delete": 0}

    monkeypatch.setattr(db, "init_db_pool", init)
    monkeypatch.setattr(db, "close_db_pool", close)
    monkeypatch.setattr(cleanup_worker, "cleanup_old_records", telemetry)
    monkeypatch.setattr(cleanup_worker, "purge_expired_recordings", run_purge)
    monkeypatch.delenv("RECORDING_RETENTION_PURGE_ENABLED", raising=False)
    assert await cleanup_worker.main() == 0
    assert seen["pool"] == "pool" and seen["enabled"] is False

    async def boom(pool, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(cleanup_worker, "purge_expired_recordings", boom)
    assert await cleanup_worker.main() == 1


# --- expired recordings are no longer served ---------------------------------


def _request():
    return Request({"type": "http", "method": "GET", "path": "/x", "query_string": b"", "headers": []})


def test_expired_recording_is_not_playable(monkeypatch):
    monkeypatch.setenv("RECORDING_RETENTION_PURGE_ENABLED", "true")
    with pytest.raises(HTTPException) as err:
        recordings._assert_recording_playable({"status": "uploaded", "retention_expired": True})
    assert err.value.status_code == 410
    assert err.value.detail["code"] == "recording_retention_expired"
    recordings._assert_recording_playable({"status": "uploaded", "retention_expired": False})


@pytest.mark.asyncio
async def test_storage_row_lookup_computes_expiry_with_tenant_predicate(monkeypatch):
    captured = {}

    class _C:
        async def fetchrow(self, query, *args):
            captured["sql"] = " ".join(query.split())
            captured["args"] = args
            return {"status": "uploaded", "retention_expired": True}

    @asynccontextmanager
    async def acquire(_pool, tenant):
        yield _C()

    monkeypatch.setattr(recordings, "acquire_with_tenant", acquire)
    tenant = str(uuid4())
    rid = uuid4()
    await recordings._recording_storage_row(SimpleNamespace(pool=object()), rid, tenant)
    assert "WHERE id = $1 AND tenant_id = $2" in captured["sql"]
    assert "tenant_recording_policy" in captured["sql"]
    assert "retention_expired" in captured["sql"]
    assert captured["args"] == (rid, UUID(tenant))


@pytest.mark.asyncio
async def test_stream_and_download_refuse_expired_recordings(monkeypatch):
    monkeypatch.setenv("RECORDING_RETENTION_PURGE_ENABLED", "true")

    async def expired_row(*_a, **_k):
        return {
            "id": uuid4(), "call_id": uuid4(), "status": "uploaded", "retention_expired": True,
            "s3_bucket": "local", "s3_key": "x", "mime_type": "audio/wav",
        }

    monkeypatch.setattr(recordings, "_recording_storage_row", expired_row)
    user = CurrentUser(id=str(uuid4()), email="a@example.com", tenant_id=str(uuid4()), role="tenant_admin")
    for fn in (recordings.stream_recording, recordings.download_recording, recordings.get_recording_url):
        kwargs = dict(recording_id=uuid4(), current_user=user, db_client=SimpleNamespace(), audit_logger=None)
        if fn is not recordings.get_recording_url:
            kwargs["request"] = _request()
        with pytest.raises(HTTPException) as err:
            await fn(**kwargs)
        assert err.value.status_code == 410


def test_expired_recordings_keep_playing_until_retention_enforcement_is_switched_on(monkeypatch):
    # Deploy safety: retention_days defaults to 90 for every tenant with a
    # policy row, so ungated enforcement would make every older recording
    # unplayable the moment this ships, while the purge is still a dry-run.
    monkeypatch.delenv("RECORDING_RETENTION_PURGE_ENABLED", raising=False)
    recordings._assert_recording_playable({"status": "uploaded", "retention_expired": True})
