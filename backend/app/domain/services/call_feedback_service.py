"""Durable-first storage and transcription for call feedback audio notes.

The invariant is deliberate: ``submit`` does not contact Deepgram until both
the audio object exists and its ``call_feedback`` row has committed. Provider
failure can therefore lose a transcript, never the user's recording.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.interfaces.prerecorded_transcription_provider import (
    PrerecordedTranscript,
    PrerecordedTranscriptionProvider,
)
from app.domain.services.recording_service import S3Client
from app.infrastructure.stt.deepgram_prerecorded import (
    DeepgramPrerecordedTranscriber,
    PrerecordedTranscriptionError,
)

logger = logging.getLogger(__name__)


class CallFeedbackError(RuntimeError):
    """Base class for expected call-feedback failures."""


class CallNotFoundError(CallFeedbackError):
    pass


class FeedbackNotFoundError(CallFeedbackError):
    pass


class FeedbackAlreadyExistsError(CallFeedbackError):
    pass


class FeedbackTranscriptionInProgressError(CallFeedbackError):
    pass


class FeedbackStorageError(CallFeedbackError):
    pass


@dataclass(frozen=True)
class StoredFeedbackAudio:
    provider: str
    bucket: str
    key: str


@dataclass(frozen=True)
class FeedbackAudioDelivery:
    mime_type: str
    data: bytes | None = None
    redirect_url: str | None = None


_MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def normalized_mime_type(value: str | None) -> str:
    """Strip MediaRecorder codec parameters and normalize case."""
    return (value or "").split(";", 1)[0].strip().lower()


def supported_feedback_mime_types() -> frozenset[str]:
    return frozenset(_MIME_EXTENSIONS)


def feedback_max_audio_bytes() -> int:
    default = 10 * 1024 * 1024
    try:
        value = int(os.getenv("CALL_FEEDBACK_MAX_AUDIO_BYTES", str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _local_storage_allowed_in_production() -> bool:
    """Whether this deployment's local disk is durable enough to store notes on.

    Defaults to False. On a container with no mounted volume, writing here would
    let us acknowledge a note that the next rollout erases — worse than refusing
    it, because the reviewer believes their feedback was captured.

    It is opt-in rather than automatic because only the operator knows which
    kind of host they are on, and getting it wrong is silent either way.
    """
    return os.getenv("CALL_FEEDBACK_ALLOW_LOCAL_STORAGE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _write_local(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(data)


def _read_local(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class FeedbackAudioStorage:
    """S3-compatible feedback storage with the project's local-dev fallback."""

    def __init__(
        self,
        *,
        s3_client: S3Client | None = None,
        local_dir: str | None = None,
    ) -> None:
        self._s3 = s3_client or S3Client()
        self._local_dir = os.path.abspath(
            local_dir or os.getenv("LOCAL_FEEDBACK_DIR", "./feedback_audio")
        )

    @staticmethod
    def _key(tenant_id: str, call_id: str, feedback_id: str, mime_type: str) -> str:
        # All identifiers have already passed UUID parsing. Keeping a dedicated
        # prefix prevents feedback notes inheriting call-recording lifecycle rules.
        extension = _MIME_EXTENSIONS.get(mime_type, ".audio")
        return f"feedback/{tenant_id}/{call_id}/{feedback_id}{extension}"

    async def save(
        self,
        *,
        tenant_id: str,
        call_id: str,
        feedback_id: str,
        mime_type: str,
        audio: bytes,
    ) -> StoredFeedbackAudio:
        key = self._key(tenant_id, call_id, feedback_id, mime_type)
        try:
            if self._s3.is_available():
                await asyncio.to_thread(
                    self._s3.upload,
                    key,
                    audio,
                    content_type=mime_type,
                )
                return StoredFeedbackAudio("s3", self._s3.bucket, key)

            # LOCAL DISK IN PRODUCTION IS A CHOICE, NOT A DEFAULT (2026-08-22)
            # ----------------------------------------------------------------
            # This guard used to be unconditional, on the reasoning that
            # container-local disk has no persistent volume. That holds for a
            # compose deployment — but this API runs as a systemd unit on the
            # host (talky-api.service), where /opt/talky/backend/recordings has
            # carried call audio across every rollout to date. On such a host
            # the fallback is exactly as durable as the recordings the product
            # already depends on, and refusing it captures nothing at all.
            #
            # So: still refused by default, because the unsafe case is the one
            # that fails silently. Enabled per-deployment by an operator who
            # knows their disk survives. Configure S3 and this branch is
            # unreachable — the S3 path above always wins.
            if (
                os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
                and not _local_storage_allowed_in_production()
            ):
                raise FeedbackStorageError("Feedback object storage is not configured")

            extension = _MIME_EXTENSIONS.get(mime_type, ".audio")
            local_path = os.path.join(self._local_dir, f"{feedback_id}{extension}")
            await asyncio.to_thread(_write_local, local_path, audio)
            return StoredFeedbackAudio("local", "local", local_path)
        except FeedbackStorageError:
            raise
        except Exception as exc:
            logger.error(
                "feedback_audio_store_failed tenant=%s call=%s error=%s",
                tenant_id,
                call_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise FeedbackStorageError("Could not store feedback audio") from exc

    def _validated_local_path(self, raw_path: str) -> str:
        path = os.path.abspath(raw_path)
        try:
            inside_root = os.path.commonpath((self._local_dir, path)) == self._local_dir
        except ValueError:
            inside_root = False
        if not inside_root:
            raise FeedbackStorageError("Feedback audio path is invalid")
        return path

    async def read(self, row: Mapping[str, Any]) -> bytes:
        provider = str(row["audio_storage_provider"])
        key = str(row["audio_key"])
        try:
            if provider == "s3":
                if not self._s3.is_available():
                    raise FeedbackStorageError("Feedback object storage is unavailable")
                return await asyncio.to_thread(self._s3.download, key)
            if provider == "local":
                return await asyncio.to_thread(
                    _read_local,
                    self._validated_local_path(key),
                )
        except FeedbackStorageError:
            raise
        except Exception as exc:
            raise FeedbackStorageError("Could not read feedback audio") from exc
        raise FeedbackStorageError("Unknown feedback storage provider")

    async def delete(self, stored: StoredFeedbackAudio) -> None:
        if stored.provider == "s3":
            if not self._s3.is_available():
                raise FeedbackStorageError("Feedback object storage is unavailable for cleanup")
            await asyncio.to_thread(self._s3.delete, stored.key)
            return
        if stored.provider == "local":
            path = self._validated_local_path(stored.key)
            with suppress(FileNotFoundError):
                await asyncio.to_thread(os.remove, path)

    def presigned_url(self, row: Mapping[str, Any]) -> str | None:
        if row["audio_storage_provider"] != "s3" or not self._s3.is_available():
            return None
        try:
            return self._s3.presigned_url(str(row["audio_key"]))
        except Exception as exc:
            raise FeedbackStorageError("Could not open feedback audio") from exc


_FEEDBACK_COLUMNS = """
    id, tenant_id, call_id, created_by,
    audio_storage_provider, audio_bucket, audio_key, audio_mime_type,
    audio_size_bytes, audio_sha256, duration_seconds,
    transcript, transcript_status, transcript_error,
    transcription_attempts, transcript_provider,
    transcript_provider_request_id, transcription_started_at, transcribed_at,
    created_at, updated_at
"""


class CallFeedbackRepository:
    """Small RLS-aware persistence boundary for ``call_feedback``."""

    def __init__(self, db_pool: Any) -> None:
        self._pool = db_pool

    @staticmethod
    def _uuid(value: str) -> UUID:
        return UUID(str(value))

    async def get_call_context(self, tenant_id: str, call_id: str) -> dict[str, Any] | None:
        try:
            tenant_uuid = self._uuid(tenant_id)
            call_uuid = self._uuid(call_id)
        except (ValueError, TypeError, AttributeError):
            return None
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT id, campaign_id FROM calls WHERE id = $1 AND tenant_id = $2",
                call_uuid,
                tenant_uuid,
            )
        return dict(row) if row else None

    async def get_for_call(self, tenant_id: str, call_id: str) -> dict[str, Any] | None:
        try:
            call_uuid = self._uuid(call_id)
        except (ValueError, TypeError, AttributeError):
            return None
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"SELECT {_FEEDBACK_COLUMNS} FROM call_feedback WHERE call_id = $1",
                call_uuid,
            )
        return dict(row) if row else None

    async def get_by_id(self, tenant_id: str, feedback_id: str) -> dict[str, Any] | None:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"SELECT {_FEEDBACK_COLUMNS} FROM call_feedback WHERE id = $1",
                self._uuid(feedback_id),
            )
        return dict(row) if row else None

    async def insert(
        self,
        *,
        feedback_id: str,
        tenant_id: str,
        call_id: str,
        created_by: str,
        stored: StoredFeedbackAudio,
        mime_type: str,
        audio_size_bytes: int,
        audio_sha256: str,
        duration_seconds: float | None,
    ) -> dict[str, Any]:
        try:
            async with acquire_with_tenant(self._pool, tenant_id) as conn:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO call_feedback (
                        id, tenant_id, call_id, created_by,
                        audio_storage_provider, audio_bucket, audio_key,
                        audio_mime_type, audio_size_bytes, audio_sha256,
                        duration_seconds, transcript_status
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'pending')
                    RETURNING {_FEEDBACK_COLUMNS}
                    """,
                    self._uuid(feedback_id),
                    self._uuid(tenant_id),
                    self._uuid(call_id),
                    self._uuid(created_by),
                    stored.provider,
                    stored.bucket,
                    stored.key,
                    mime_type,
                    audio_size_bytes,
                    audio_sha256,
                    duration_seconds,
                )
        except asyncpg.UniqueViolationError as exc:
            raise FeedbackAlreadyExistsError("Feedback already exists for this call") from exc
        if row is None:
            raise RuntimeError("Feedback insert returned no row")
        return dict(row)

    async def replace(
        self,
        *,
        previous_id: str,
        feedback_id: str,
        tenant_id: str,
        call_id: str,
        created_by: str,
        stored: StoredFeedbackAudio,
        mime_type: str,
        audio_size_bytes: int,
        audio_sha256: str,
        duration_seconds: float | None,
    ) -> tuple[dict[str, Any], StoredFeedbackAudio] | None:
        """Swap this call's note for a new one inside a single transaction.

        ``acquire_with_tenant`` yields from inside ``conn.transaction()``, so
        the DELETE and the INSERT below either both land or neither does. The
        call is therefore never observably without a note -- which a DELETE
        endpoint followed by a POST could not promise, because a second request
        that failed would already have destroyed the reviewer's only copy.

        Returns ``(new_row, superseded_audio)``, or ``None`` when the row we
        meant to supersede is no longer the current one. That means someone
        else re-recorded first, and their note must not be clobbered by ours.
        """
        try:
            async with acquire_with_tenant(self._pool, tenant_id) as conn:
                removed = await conn.fetchrow(
                    """
                    DELETE FROM call_feedback
                    WHERE call_id = $1 AND id = $2
                    RETURNING audio_storage_provider, audio_bucket, audio_key
                    """,
                    self._uuid(call_id),
                    self._uuid(previous_id),
                )
                if removed is None:
                    return None
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO call_feedback (
                        id, tenant_id, call_id, created_by,
                        audio_storage_provider, audio_bucket, audio_key,
                        audio_mime_type, audio_size_bytes, audio_sha256,
                        duration_seconds, transcript_status
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'pending')
                    RETURNING {_FEEDBACK_COLUMNS}
                    """,
                    self._uuid(feedback_id),
                    self._uuid(tenant_id),
                    self._uuid(call_id),
                    self._uuid(created_by),
                    stored.provider,
                    stored.bucket,
                    stored.key,
                    mime_type,
                    audio_size_bytes,
                    audio_sha256,
                    duration_seconds,
                )
        except asyncpg.UniqueViolationError as exc:
            raise FeedbackAlreadyExistsError("Feedback already exists for this call") from exc
        if row is None:
            raise RuntimeError("Feedback replace returned no row")
        return dict(row), StoredFeedbackAudio(
            provider=str(removed["audio_storage_provider"]),
            bucket=str(removed["audio_bucket"]),
            key=str(removed["audio_key"]),
        )

    async def begin_initial_attempt(
        self, tenant_id: str, feedback_id: str
    ) -> dict[str, Any] | None:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE call_feedback
                   SET transcription_attempts = transcription_attempts + 1,
                       transcription_started_at = NOW(),
                       transcript_error = NULL,
                       updated_at = NOW()
                 WHERE id = $1 AND transcript_status = 'pending'
                   AND transcription_attempts = 0
                RETURNING {_FEEDBACK_COLUMNS}
                """,
                self._uuid(feedback_id),
            )
        return dict(row) if row else None

    async def begin_retry_attempt(
        self,
        tenant_id: str,
        feedback_id: str,
        *,
        stale_after_seconds: int,
    ) -> dict[str, Any] | None:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE call_feedback
                   SET transcript_status = 'pending',
                       transcription_attempts = transcription_attempts + 1,
                       transcription_started_at = NOW(),
                       transcript_error = NULL,
                       updated_at = NOW()
                 WHERE id = $1
                   AND (
                       transcript_status = 'failed'
                       OR (
                           transcript_status = 'pending'
                           AND (
                               transcription_started_at IS NULL
                               OR transcription_started_at
                                  < NOW() - ($2 * INTERVAL '1 second')
                           )
                       )
                   )
                RETURNING {_FEEDBACK_COLUMNS}
                """,
                self._uuid(feedback_id),
                stale_after_seconds,
            )
        return dict(row) if row else None

    async def mark_done(
        self,
        tenant_id: str,
        feedback_id: str,
        result: PrerecordedTranscript,
    ) -> dict[str, Any] | None:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE call_feedback
                   SET transcript = $2,
                       transcript_status = 'done',
                       transcript_error = NULL,
                       transcript_provider_request_id = $3,
                       duration_seconds = COALESCE(duration_seconds, $4),
                       transcribed_at = NOW(),
                       updated_at = NOW()
                 WHERE id = $1
                RETURNING {_FEEDBACK_COLUMNS}
                """,
                self._uuid(feedback_id),
                result.text,
                result.provider_request_id,
                result.duration_seconds,
            )
        return dict(row) if row else None

    async def mark_failed(
        self,
        tenant_id: str,
        feedback_id: str,
        error: str,
    ) -> dict[str, Any] | None:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE call_feedback
                   SET transcript_status = 'failed',
                       transcript_error = $2,
                       updated_at = NOW()
                 WHERE id = $1
                RETURNING {_FEEDBACK_COLUMNS}
                """,
                self._uuid(feedback_id),
                error[:1000],
            )
        return dict(row) if row else None


class CallFeedbackService:
    """Orchestrates storage, committed metadata, and best-effort transcription."""

    def __init__(
        self,
        db_pool: Any,
        *,
        repository: CallFeedbackRepository | None = None,
        storage: FeedbackAudioStorage | None = None,
        transcriber: PrerecordedTranscriptionProvider | None = None,
    ) -> None:
        self._repository = repository or CallFeedbackRepository(db_pool)
        self._storage = storage or FeedbackAudioStorage()
        self._transcriber = transcriber or DeepgramPrerecordedTranscriber(db_pool)

    async def _require_call(self, tenant_id: str, call_id: str) -> dict[str, Any]:
        call = await self._repository.get_call_context(tenant_id, call_id)
        if not call:
            # 404, not 403: do not reveal cross-tenant call IDs.
            raise CallNotFoundError("Call not found")
        return call

    async def submit(
        self,
        *,
        tenant_id: str,
        call_id: str,
        created_by: str,
        audio: bytes,
        mime_type: str,
        duration_seconds: float | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        await self._require_call(tenant_id, call_id)
        digest = hashlib.sha256(audio).hexdigest()

        existing = await self._repository.get_for_call(tenant_id, call_id)
        if existing:
            if existing["audio_sha256"] == digest:
                return existing
            if not replace:
                raise FeedbackAlreadyExistsError("This call already has a feedback note")
            return await self._replace_existing(
                tenant_id=tenant_id,
                call_id=call_id,
                created_by=created_by,
                existing=existing,
                audio=audio,
                mime_type=mime_type,
                digest=digest,
                duration_seconds=duration_seconds,
            )

        feedback_id = str(uuid4())
        stored = await self._storage.save(
            tenant_id=tenant_id,
            call_id=call_id,
            feedback_id=feedback_id,
            mime_type=mime_type,
            audio=audio,
        )

        try:
            # The repository method exits its transaction before returning.
            # Nothing below this line can contact Deepgram until that commit.
            inserted = await self._repository.insert(
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                call_id=call_id,
                created_by=created_by,
                stored=stored,
                mime_type=mime_type,
                audio_size_bytes=len(audio),
                audio_sha256=digest,
                duration_seconds=duration_seconds,
            )
        except FeedbackAlreadyExistsError:
            await self._cleanup_unlinked_audio(stored)
            winner = await self._repository.get_for_call(tenant_id, call_id)
            if winner and winner["audio_sha256"] == digest:
                return winner
            raise FeedbackAlreadyExistsError("This call already has a feedback note") from None
        except Exception:
            await self._cleanup_unlinked_audio(stored)
            raise

        try:
            claimed = await self._repository.begin_initial_attempt(tenant_id, feedback_id)
        except Exception:
            # The audio and pending row are already committed. A transient DB
            # failure while claiming the derived-data attempt must not turn a
            # successfully saved note into a 500 response; the retry endpoint
            # can claim this pending/never-started row immediately.
            logger.exception(
                "feedback_transcription_claim_failed feedback=%s tenant=%s",
                feedback_id,
                tenant_id,
            )
            return inserted
        if not claimed:
            return inserted
        return await self._transcribe_claimed(tenant_id, claimed, audio)

    async def _replace_existing(
        self,
        *,
        tenant_id: str,
        call_id: str,
        created_by: str,
        existing: Mapping[str, Any],
        audio: bytes,
        mime_type: str,
        digest: str,
        duration_seconds: float | None,
    ) -> dict[str, Any]:
        """Re-record over an existing note, keeping the durable-first ordering.

        Same discipline as ``submit``: the new object exists before the swap
        commits, and the superseded object is deleted only afterwards. The two
        failure directions are not symmetric -- an orphaned object costs some
        storage, whereas a row pointing at a deleted object costs the reviewer
        the note itself.
        """
        feedback_id = str(uuid4())
        stored = await self._storage.save(
            tenant_id=tenant_id,
            call_id=call_id,
            feedback_id=feedback_id,
            mime_type=mime_type,
            audio=audio,
        )

        try:
            swapped = await self._repository.replace(
                previous_id=str(existing["id"]),
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                call_id=call_id,
                created_by=created_by,
                stored=stored,
                mime_type=mime_type,
                audio_size_bytes=len(audio),
                audio_sha256=digest,
                duration_seconds=duration_seconds,
            )
        except Exception:
            await self._cleanup_unlinked_audio(stored)
            raise

        if swapped is None:
            # Someone re-recorded between our read and our write. Theirs is the
            # current note; discard ours rather than silently overwrite it.
            await self._cleanup_unlinked_audio(stored)
            current = await self._repository.get_for_call(tenant_id, call_id)
            if current and current["audio_sha256"] == digest:
                return current
            raise FeedbackAlreadyExistsError(
                "This call's feedback note changed while you were recording; "
                "reload the call and try again"
            )

        inserted, superseded = swapped
        # Committed. The old object is unreferenced from here on.
        await self._cleanup_unlinked_audio(superseded)

        try:
            claimed = await self._repository.begin_initial_attempt(tenant_id, feedback_id)
        except Exception:
            logger.exception(
                "feedback_transcription_claim_failed feedback=%s tenant=%s",
                feedback_id,
                tenant_id,
            )
            return inserted
        if not claimed:
            return inserted
        return await self._transcribe_claimed(tenant_id, claimed, audio)

    async def retry_transcription(
        self,
        *,
        tenant_id: str,
        call_id: str,
    ) -> dict[str, Any]:
        await self._require_call(tenant_id, call_id)
        feedback = await self._repository.get_for_call(tenant_id, call_id)
        if not feedback:
            raise FeedbackNotFoundError("Feedback not found")
        if feedback["transcript_status"] == "done":
            return feedback

        try:
            stale_after = int(os.getenv("CALL_FEEDBACK_PENDING_RETRY_AFTER_SECONDS", "30"))
        except (TypeError, ValueError):
            stale_after = 30
        stale_after = max(1, stale_after)

        claimed = await self._repository.begin_retry_attempt(
            tenant_id,
            str(feedback["id"]),
            stale_after_seconds=stale_after,
        )
        if not claimed:
            latest = await self._repository.get_for_call(tenant_id, call_id)
            if latest and latest["transcript_status"] == "done":
                return latest
            raise FeedbackTranscriptionInProgressError(
                "Feedback transcription is already in progress"
            )

        try:
            audio = await self._storage.read(claimed)
        except Exception as exc:
            return await self._mark_failed_or_latest(tenant_id, claimed, exc)
        return await self._transcribe_claimed(tenant_id, claimed, audio)

    async def get_feedback(
        self,
        *,
        tenant_id: str,
        call_id: str,
    ) -> dict[str, Any]:
        await self._require_call(tenant_id, call_id)
        feedback = await self._repository.get_for_call(tenant_id, call_id)
        if not feedback:
            raise FeedbackNotFoundError("Feedback not found")
        return feedback

    async def get_audio(
        self,
        *,
        tenant_id: str,
        call_id: str,
    ) -> FeedbackAudioDelivery:
        feedback = await self.get_feedback(tenant_id=tenant_id, call_id=call_id)
        url = self._storage.presigned_url(feedback)
        if url:
            return FeedbackAudioDelivery(
                mime_type=str(feedback["audio_mime_type"]),
                redirect_url=url,
            )
        data = await self._storage.read(feedback)
        return FeedbackAudioDelivery(
            mime_type=str(feedback["audio_mime_type"]),
            data=data,
        )

    async def _transcribe_claimed(
        self,
        tenant_id: str,
        feedback: Mapping[str, Any],
        audio: bytes,
    ) -> dict[str, Any]:
        feedback_id = str(feedback["id"])
        try:
            result = await self._transcriber.transcribe(
                audio=audio,
                mime_type=str(feedback["audio_mime_type"]),
                tenant_id=tenant_id,
            )
        except asyncio.CancelledError:
            # A disconnected client must not strand the durable row in pending.
            try:
                await asyncio.shield(
                    self._repository.mark_failed(
                        tenant_id,
                        feedback_id,
                        "Transcription request was cancelled",
                    )
                )
            except Exception:
                logger.exception(
                    "feedback_transcription_cancel_update_failed feedback=%s",
                    feedback_id,
                )
            raise
        except Exception as exc:
            logger.warning(
                "feedback_transcription_failed feedback=%s tenant=%s error=%s",
                feedback_id,
                tenant_id,
                type(exc).__name__,
            )
            return await self._mark_failed_or_latest(tenant_id, feedback, exc)

        try:
            updated = await self._repository.mark_done(tenant_id, feedback_id, result)
        except Exception:
            logger.exception(
                "feedback_transcription_result_persist_failed feedback=%s",
                feedback_id,
            )
            return await self._latest_or_original(tenant_id, feedback)
        return updated or dict(feedback)

    async def _mark_failed_or_latest(
        self,
        tenant_id: str,
        feedback: Mapping[str, Any],
        exc: Exception,
    ) -> dict[str, Any]:
        if isinstance(exc, (PrerecordedTranscriptionError, FeedbackStorageError)):
            message = str(exc)
        else:
            message = f"Transcription failed ({type(exc).__name__})"
        try:
            updated = await self._repository.mark_failed(
                tenant_id,
                str(feedback["id"]),
                message,
            )
        except Exception:
            logger.exception(
                "feedback_transcription_failure_persist_failed feedback=%s",
                feedback["id"],
            )
            return await self._latest_or_original(tenant_id, feedback)
        return updated or dict(feedback)

    async def _latest_or_original(
        self,
        tenant_id: str,
        feedback: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            latest = await self._repository.get_by_id(tenant_id, str(feedback["id"]))
        except Exception:
            latest = None
        return latest or dict(feedback)

    async def _cleanup_unlinked_audio(self, stored: StoredFeedbackAudio) -> None:
        try:
            await self._storage.delete(stored)
        except Exception:
            # The request still fails, and the key contains the feedback UUID so a
            # storage reconciliation job can safely identify the orphan later.
            logger.exception(
                "feedback_orphan_cleanup_failed provider=%s key=%s",
                stored.provider,
                stored.key,
            )
