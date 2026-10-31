"""Focused tenant recording access and permanent-deletion contract tests."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import recordings
from app.api.v1.endpoints.admin import media
from app.core.security.rbac import Permission


class _Audit:
    def __init__(self):
        self.events: list[dict] = []

    async def log(self, **kwargs):
        self.events.append(kwargs)


def _user(tenant_id: UUID | None = None) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="tenant-user@example.com",
        tenant_id=str(tenant_id or uuid4()),
        role="tenant_admin",
    )


def _request(*, range_header: str | None = None) -> Request:
    headers = [] if range_header is None else [(b"range", range_header.encode())]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/recordings/test",
            "query_string": b"",
            "headers": headers,
        }
    )


def _storage_row(
    *,
    recording_id: UUID,
    tenant_id: UUID,
    call_id: UUID | None = None,
    bucket: str = "recordings",
    key: str = "tenant/call/audio.wav",
) -> dict:
    return {
        "id": recording_id,
        "call_id": call_id or uuid4(),
        "tenant_id": tenant_id,
        "s3_key": key,
        "s3_bucket": bucket,
        "status": "uploaded",
        "mime_type": "audio/wav",
    }


def _deletion_claim(
    *,
    recording_id: UUID,
    tenant_id: UUID,
    status: str = "intent_committed",
    bucket: str = "local",
    key: str | None = None,
) -> media._DeletionClaim:
    return media._DeletionClaim(
        intent_id=uuid4(),
        status=status,
        snapshot={
            "id": recording_id,
            "call_id": uuid4(),
            "tenant_id": tenant_id,
            "s3_bucket": bucket,
            "s3_key": key,
            "status": "uploaded",
            "mime_type": "audio/wav",
        },
    )


@pytest.mark.asyncio
async def test_live_recording_permission_lookup_fails_closed(monkeypatch):
    user = _user()

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("permission database unavailable")

    monkeypatch.setattr(recordings, "get_effective_permissions", unavailable)
    dependency = recordings._require_recording_permission(
        Permission.RECORDINGS_READ
    )

    with pytest.raises(HTTPException) as exc:
        await dependency(user=user, db_pool=object())

    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "authorization_unavailable"}


@pytest.mark.asyncio
async def test_live_recording_permission_denies_separate_download_grant(monkeypatch):
    user = _user()

    async def read_only(*_args, **_kwargs):
        return {Permission.RECORDINGS_READ}

    monkeypatch.setattr(recordings, "get_effective_permissions", read_only)
    dependency = recordings._require_recording_permission(
        Permission.RECORDINGS_DOWNLOAD
    )

    with pytest.raises(HTTPException) as exc:
        await dependency(user=user, db_pool=object())

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "permission_denied",
        "required": "recordings:download",
    }
    assert user._permissions == {"recordings:read"}


@pytest.mark.asyncio
async def test_list_recordings_is_tenant_scoped_in_context_and_sql(monkeypatch):
    tenant_id = uuid4()
    recording_id = uuid4()
    call_id = uuid4()
    contexts: list[str] = []

    class Conn:
        def __init__(self):
            self.calls: list[tuple[str, tuple]] = []

        async def fetch(self, query, *args):
            self.calls.append((query, args))
            return [
                {
                    "id": recording_id,
                    "call_id": call_id,
                    "created_at": datetime(2026, 8, 26, tzinfo=timezone.utc),
                    "duration_seconds": 31,
                    "file_size_bytes": 4096,
                    "status": "uploaded",
                    "phone_number": "+15551234567",
                    "legal_hold": False,
                }
            ]

        async def fetchval(self, query, *args):
            self.calls.append((query, args))
            return 1

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        contexts.append(tenant_context)
        yield conn

    monkeypatch.setattr(recordings, "acquire_with_tenant", acquire)
    response = await recordings.list_recordings(
        call_id=str(call_id),
        page=2,
        page_size=10,
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=object()),
    )

    assert contexts == [str(tenant_id)]
    assert response.total == 1
    assert response.items[0].id == str(recording_id)
    list_query, list_args = conn.calls[0]
    count_query, count_args = conn.calls[1]
    assert "r.tenant_id = $1" in list_query
    assert "r.call_id = $2" in list_query
    assert "se.target_type = 'partner'" in list_query
    assert "held_tenant.white_label_partner_id = se.target_id" in list_query
    assert list_args == (str(tenant_id), str(call_id), 10, 10)
    assert "r.tenant_id = $1" in count_query
    assert count_args == (str(tenant_id), str(call_id))


@pytest.mark.asyncio
async def test_stream_recording_scopes_lookup_and_forwards_persisted_bucket(
    monkeypatch,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    row = _storage_row(
        recording_id=recording_id,
        tenant_id=tenant_id,
        bucket="persisted-tenant-archive",
    )
    contexts: list[str] = []
    query_calls: list[tuple[str, tuple]] = []
    signed: list[tuple[str, str | None, str | None]] = []

    class Conn:
        async def fetchrow(self, query, *args):
            query_calls.append((query, args))
            return row

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        contexts.append(tenant_context)
        yield Conn()

    class S3:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def presigned_url(key, download_filename=None, bucket=None):
            signed.append((key, download_filename, bucket))
            return "https://storage.example/signed-playback"

    monkeypatch.setattr(recordings, "acquire_with_tenant", acquire)
    monkeypatch.setattr(recordings, "S3Client", S3)
    audit = _Audit()
    response = await recordings.stream_recording(
        recording_id=recording_id,
        request=_request(),
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )

    assert contexts == [str(tenant_id)]
    assert "WHERE id = $1 AND tenant_id = $2" in query_calls[0][0]
    assert query_calls[0][1] == (recording_id, tenant_id)
    assert signed == [(row["s3_key"], None, "persisted-tenant-archive")]
    assert response.status_code == 302
    assert response.headers["location"] == "https://storage.example/signed-playback"
    assert audit.events[0]["tenant_id"] == str(tenant_id)
    assert audit.events[0]["action"] == "recording_played"


@pytest.mark.asyncio
async def test_download_recording_is_tenant_scoped_and_forces_attachment(
    monkeypatch,
    tmp_path,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    root = tmp_path / "recordings"
    root.mkdir()
    audio_path = root / "audio.wav"
    audio_path.write_bytes(b"RIFF-focused-recording")
    row = _storage_row(
        recording_id=recording_id,
        tenant_id=tenant_id,
        bucket="local",
        key=str(audio_path),
    )
    contexts: list[str] = []
    query_args: list[tuple] = []

    class Conn:
        async def fetchrow(self, _query, *args):
            query_args.append(args)
            return row

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        contexts.append(tenant_context)
        yield Conn()

    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))
    monkeypatch.setattr(recordings, "acquire_with_tenant", acquire)
    audit = _Audit()
    response = await recordings.download_recording(
        recording_id=recording_id,
        request=_request(),
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert contexts == [str(tenant_id)]
    assert query_args == [(recording_id, tenant_id)]
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="recording-{recording_id}.wav"'
    )
    assert body == b"RIFF-focused-recording"
    assert audit.events[0]["action"] == "recording_downloaded"


def test_local_recording_path_escape_is_rejected(monkeypatch, tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "recordings-escape" / "secret.wav"
    outside.parent.mkdir()
    outside.write_bytes(b"secret")
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))

    with pytest.raises(HTTPException) as exc:
        recordings._validated_recording_path(str(outside))

    assert exc.value.status_code == 500
    assert exc.value.detail == {"code": "recording_storage_invalid"}


@pytest.mark.parametrize(
    "validator",
    [recordings._validated_recording_path, media._validated_recording_path],
    ids=["tenant", "admin"],
)
def test_local_recording_symlink_escape_is_rejected(
    monkeypatch,
    tmp_path,
    validator,
):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.wav"
    secret.write_bytes(b"secret")
    link = root / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"OS cannot create a directory symlink/junction: {exc}")
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))

    with pytest.raises(HTTPException) as exc:
        validator(str(link / "secret.wav"))

    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_commits_intent_before_bytes_and_scopes_claim_and_completion(
    monkeypatch,
    tmp_path,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    root = tmp_path / "recordings"
    root.mkdir()
    audio_path = root / "delete-me.wav"
    audio_path.write_bytes(b"recording")
    claim = _deletion_claim(
        recording_id=recording_id,
        tenant_id=tenant_id,
        key=str(audio_path),
    )
    order: list[str] = []
    claim_kwargs: dict = {}
    complete_kwargs: dict = {}
    real_remove = os.remove
    deletion_conn = object()
    guard_active = False

    async def claim_deletion(*_args, **kwargs):
        claim_kwargs.update(kwargs)
        order.append("intent_committed")
        return claim

    @asynccontextmanager
    async def serialized_deletion(_db, actual_claim):
        nonlocal guard_active
        assert actual_claim is claim
        # The production helper acquires the advisory transaction lock and
        # checks tenant/partner holds before yielding this connection.
        order.append("advisory_lock_and_hold_gate")
        guard_active = True
        try:
            yield deletion_conn
        finally:
            guard_active = False
            order.append("serialized_guard_committed")

    def remove(path):
        order.append("bytes_deleted")
        real_remove(path)

    async def mark_stage(conn, _intent_id, stage, **_kwargs):
        assert conn is deletion_conn
        assert guard_active, "deletion stage must commit inside the lock/hold guard"
        order.append(f"stage:{stage}")

    async def complete_deletion(*_args, **kwargs):
        complete_kwargs.update(kwargs)
        order.append("metadata_completed")

    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))
    monkeypatch.setattr(media, "_claim_media_deletion", claim_deletion)
    monkeypatch.setattr(media, "_serialized_media_deletion", serialized_deletion)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", mark_stage)
    monkeypatch.setattr(media, "_complete_recording_deletion", complete_deletion)
    monkeypatch.setattr(recordings.os, "remove", remove)

    response = await recordings.delete_recording(
        recording_id=recording_id,
        payload=recordings.RecordingDeleteRequest(
            reason="Customer approved permanent recording erasure"
        ),
        idempotency_key="tenant-delete:durable-order",
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=_Audit(),
    )

    assert response.status_code == 204
    assert order == [
        "intent_committed",
        "advisory_lock_and_hold_gate",
        "bytes_deleted",
        "stage:object_deleted",
        "serialized_guard_committed",
        "metadata_completed",
    ]
    assert not audio_path.exists()
    assert claim_kwargs["expected_tenant_id"] == tenant_id
    assert claim_kwargs["resource_id"] == recording_id
    assert complete_kwargs["expected_tenant_id"] == tenant_id
    assert complete_kwargs["recording_id"] == recording_id


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_stage", ["claim", "pre_storage_recheck"])
async def test_delete_legal_hold_returns_stable_423_code(
    monkeypatch,
    hold_stage,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    claim = _deletion_claim(recording_id=recording_id, tenant_id=tenant_id)

    async def claim_deletion(*_args, **_kwargs):
        if hold_stage == "claim":
            raise HTTPException(status_code=423, detail="held")
        return claim

    @asynccontextmanager
    async def serialized_deletion(*_args, **_kwargs):
        if hold_stage == "pre_storage_recheck":
            raise HTTPException(status_code=423, detail="held")
        yield object()

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("legal hold must stop deletion")

    monkeypatch.setattr(media, "_claim_media_deletion", claim_deletion)
    monkeypatch.setattr(media, "_serialized_media_deletion", serialized_deletion)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", must_not_run)
    monkeypatch.setattr(media, "_complete_recording_deletion", must_not_run)

    with pytest.raises(HTTPException) as exc:
        await recordings.delete_recording(
            recording_id=recording_id,
            payload=recordings.RecordingDeleteRequest(
                reason="Litigation hold requires preserved recording"
            ),
            idempotency_key=f"tenant-delete:hold:{hold_stage}",
            current_user=_user(tenant_id),
            db_client=SimpleNamespace(pool=object()),
            audit_logger=_Audit(),
        )

    assert exc.value.status_code == 423
    assert exc.value.detail == {"code": "recording_legal_hold"}


@pytest.mark.asyncio
async def test_delete_idempotency_conflict_returns_stable_409_code(monkeypatch):
    tenant_id = uuid4()

    async def conflict(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail="key conflict")

    monkeypatch.setattr(media, "_claim_media_deletion", conflict)
    with pytest.raises(HTTPException) as exc:
        await recordings.delete_recording(
            recording_id=uuid4(),
            payload=recordings.RecordingDeleteRequest(
                reason="Customer approved permanent recording erasure"
            ),
            idempotency_key="tenant-delete:conflict",
            current_user=_user(tenant_id),
            db_client=SimpleNamespace(pool=object()),
            audit_logger=_Audit(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "idempotency_conflict"}


@pytest.mark.asyncio
async def test_completed_delete_replay_is_204_without_touching_storage_or_metadata(
    monkeypatch,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    claim = _deletion_claim(
        recording_id=recording_id,
        tenant_id=tenant_id,
        status="completed",
        bucket="recordings",
        key="must-not-delete.wav",
    )

    async def replay(*_args, **_kwargs):
        return claim

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("completed replay must not repeat deletion")

    @asynccontextmanager
    async def must_not_enter_guard(*_args, **_kwargs):
        raise AssertionError("completed replay must not enter deletion guard")
        yield  # pragma: no cover - makes this an async context manager

    class S3MustNotBeCreated:
        def __init__(self):
            raise AssertionError("completed replay must not touch storage")

    monkeypatch.setattr(media, "_claim_media_deletion", replay)
    monkeypatch.setattr(media, "_serialized_media_deletion", must_not_enter_guard)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", must_not_run)
    monkeypatch.setattr(media, "_complete_recording_deletion", must_not_run)
    monkeypatch.setattr(recordings, "S3Client", S3MustNotBeCreated)

    response = await recordings.delete_recording(
        recording_id=recording_id,
        payload=recordings.RecordingDeleteRequest(
            reason="Customer approved permanent recording erasure"
        ),
        idempotency_key="tenant-delete:completed-replay",
        current_user=_user(tenant_id),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=_Audit(),
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_storage_failure_marks_intent_failed_and_preserves_metadata(
    monkeypatch,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    claim = _deletion_claim(
        recording_id=recording_id,
        tenant_id=tenant_id,
        bucket="recordings",
        key="tenant/call/audio.wav",
    )
    stages: list[tuple[str, str | None]] = []
    completion_calls: list[dict] = []
    storage_calls: list[tuple[str, str]] = []
    deletion_conn = object()
    guard_active = False

    async def claim_deletion(*_args, **_kwargs):
        return claim

    @asynccontextmanager
    async def serialized_deletion(_db, actual_claim):
        nonlocal guard_active
        assert actual_claim is claim
        guard_active = True
        try:
            yield deletion_conn
        finally:
            guard_active = False

    async def mark_stage(conn, _intent_id, stage, *, error=None):
        assert conn is deletion_conn
        assert guard_active, "failed stage must commit before releasing the guard"
        stages.append((stage, error))

    async def complete_deletion(*_args, **kwargs):
        completion_calls.append(kwargs)

    class FailingS3:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def delete_permanently(key, bucket):
            storage_calls.append((key, bucket))
            raise RuntimeError("object store write unavailable")

    monkeypatch.setattr(media, "_claim_media_deletion", claim_deletion)
    monkeypatch.setattr(media, "_serialized_media_deletion", serialized_deletion)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", mark_stage)
    monkeypatch.setattr(media, "_complete_recording_deletion", complete_deletion)
    monkeypatch.setattr(recordings, "S3Client", FailingS3)

    with pytest.raises(HTTPException) as exc:
        await recordings.delete_recording(
            recording_id=recording_id,
            payload=recordings.RecordingDeleteRequest(
                reason="Customer approved permanent recording erasure"
            ),
            idempotency_key="tenant-delete:storage-failure",
            current_user=_user(tenant_id),
            db_client=SimpleNamespace(pool=object()),
            audit_logger=_Audit(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "recording_delete_storage_failed"}
    assert stages[0][0] == "failed"
    assert "object store write unavailable" in str(stages[0][1])
    assert storage_calls == [("tenant/call/audio.wav", "recordings")]
    assert completion_calls == []


# ========================================================================
# _require_recording_permission() — unseeded deployment vs. genuine revocation
# ========================================================================
#
# Production has 0 rows in ``role_permissions`` and 0 in ``tenant_users``, so
# the DB-only resolver returns an empty set for every non-platform-admin user
# and this dependency 403s every recording read, stream, download and erasure.
# "This deployment has no RBAC data" and "this user was denied" are different
# states and must behave differently — the same three-state contract
# ``require_permission`` uses (tests/security/test_rbac.py
# ::TestUnseededDeploymentFallback).


class _PermissionResolver:
    """Stands in for ``get_effective_permissions`` + ``rbac_data_is_seeded``.

    ``granted`` -> what the DB resolves for this user.
    ``seeded``  -> the global two-leg seeding probe answer.
    """

    def __init__(self, granted, seeded: bool):
        self.granted = set(granted)
        self.seeded = seeded
        self.probe_calls = 0

    async def resolve(self, *_args, **_kwargs):
        return set(self.granted)

    async def probe(self, *_args, **_kwargs):
        self.probe_calls += 1
        return self.seeded


def _install_resolver(monkeypatch, resolver: _PermissionResolver) -> None:
    monkeypatch.setattr(recordings, "get_effective_permissions", resolver.resolve)
    monkeypatch.setattr(recordings, "rbac_data_is_seeded", resolver.probe)


@pytest.fixture()
def _clean_probe_cache():
    from app.core.security.rbac import reset_rbac_seeding_probe_cache

    reset_rbac_seeding_probe_cache()
    yield
    reset_rbac_seeding_probe_cache()


class TestRecordingUnseededDeploymentFallback:
    @pytest.mark.asyncio
    async def test_unseeded_deployment_falls_back_to_role_defaults(
        self, monkeypatch, _clean_probe_cache
    ):
        user = _user()  # tenant_admin
        resolver = _PermissionResolver([], seeded=False)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_READ
        )
        assert await dependency(user=user, db_pool=object()) is user
        assert "recordings:read" in user._permissions

    @pytest.mark.asyncio
    async def test_unseeded_fallback_is_role_scoped_not_blanket_allow(
        self, monkeypatch, _clean_probe_cache
    ):
        """Irreversible media erasure stays with tenant_admin+ even in the
        fallback: a plain ``user`` role has no RECORDINGS_DELETE default, so
        the unseeded path is never wider than the seeded one."""
        user = _user()
        user.role = "user"
        resolver = _PermissionResolver([], seeded=False)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_DELETE
        )
        with pytest.raises(HTTPException) as exc:
            await dependency(user=user, db_pool=object())

        assert exc.value.status_code == 403
        assert exc.value.detail == {
            "code": "permission_denied",
            "required": "recordings:delete",
        }

    @pytest.mark.asyncio
    async def test_seeded_deployment_denies_user_with_no_grant(
        self, monkeypatch, _clean_probe_cache
    ):
        """The regression the DB resolver bought: a real revocation still 403s."""
        user = _user()
        resolver = _PermissionResolver([], seeded=True)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_READ
        )
        with pytest.raises(HTTPException) as exc:
            await dependency(user=user, db_pool=object())

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_seeded_deployment_revoking_one_permission_denies(
        self, monkeypatch, _clean_probe_cache
    ):
        """Non-empty grants prove the deployment is seeded — no probe needed."""
        user = _user()
        resolver = _PermissionResolver([Permission.RECORDINGS_READ], seeded=True)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_DOWNLOAD
        )
        with pytest.raises(HTTPException) as exc:
            await dependency(user=user, db_pool=object())

        assert exc.value.status_code == 403
        assert resolver.probe_calls == 0, "probe must not run when grants resolve"

    @pytest.mark.asyncio
    async def test_seeded_and_granted_allows(self, monkeypatch, _clean_probe_cache):
        user = _user()
        resolver = _PermissionResolver([Permission.RECORDINGS_DOWNLOAD], seeded=True)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_DOWNLOAD
        )
        assert await dependency(user=user, db_pool=object()) is user
        assert user._permissions == {"recordings:download"}

    @pytest.mark.asyncio
    async def test_probe_query_error_fails_closed_with_503(
        self, monkeypatch, _clean_probe_cache
    ):
        """An erroring probe is a database failure, not a licence to downgrade."""
        user = _user()

        async def empty(*_args, **_kwargs):
            return set()

        async def broken_probe(*_args, **_kwargs):
            raise RuntimeError("relation role_permissions does not exist")

        monkeypatch.setattr(recordings, "get_effective_permissions", empty)
        monkeypatch.setattr(recordings, "rbac_data_is_seeded", broken_probe)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_READ
        )
        with pytest.raises(HTTPException) as exc:
            await dependency(user=user, db_pool=object())

        assert exc.value.status_code == 503
        assert exc.value.detail == {"code": "authorization_unavailable"}

    @pytest.mark.asyncio
    async def test_unseeded_warning_logged_once_not_per_request(
        self, monkeypatch, caplog, _clean_probe_cache
    ):
        import logging as _logging

        user = _user()
        resolver = _PermissionResolver([], seeded=False)
        _install_resolver(monkeypatch, resolver)

        dependency = recordings._require_recording_permission(
            Permission.RECORDINGS_READ
        )
        with caplog.at_level(_logging.WARNING, logger="app.core.security.rbac"):
            for _ in range(5):
                await dependency(user=_user(), db_pool=object())

        unseeded = [
            r for r in caplog.records if "RBAC_UNSEEDED_FALLBACK" in r.getMessage()
        ]
        assert len(unseeded) == 1, f"expected exactly one warning, got {len(unseeded)}"
        assert user.id  # sanity: the helper produced a usable user
