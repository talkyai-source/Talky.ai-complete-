"""Enforce tenant ``retention_days`` on stored call recordings.

``tenant_recording_policy.retention_days`` is shown to tenants as "Keep for
(days)", so audio older than that must actually go. This module is the job.

Safety contract (deliberate, do not relax without a product decision):
  * DRY-RUN IS THE DEFAULT. Nothing is deleted unless
    ``RECORDING_RETENTION_PURGE_ENABLED`` is set to true/1/yes. Until then the
    pass only logs how many recordings it WOULD delete, so the owner can review
    the count before enabling it.
  * Fail closed: a recording is only a candidate when its tenant HAS a
    ``tenant_recording_policy`` row with ``retention_days > 0`` (inner join).
    No policy row, no deletion.
  * Legal holds: tenants under an active COMPLIANCE hold are excluded from the
    selection, and the shared deletion claim re-checks the hold (423) so a hold
    created mid-run still wins.
  * Deletion goes through the same durable, serialized state machine as the
    tenant/admin DELETE endpoints (``admin.media``): intent committed, then the
    object is erased under the tenant hold lock, then the row is removed. The
    object is always erased BEFORE the row, and a missing object is not an
    error, so a retry after a partial failure converges.
  * ``admin_media_deletion_intents.actor_id`` is a NOT NULL FK to
    ``user_profiles``, so a live run needs a real user id in
    ``RECORDING_RETENTION_PURGE_ACTOR_ID`` (a dedicated system/admin user the
    owner chooses). A live run without it raises instead of guessing.

Env:
  RECORDING_RETENTION_PURGE_ENABLED   default "false" (dry-run)
  RECORDING_RETENTION_PURGE_ACTOR_ID  user_profiles.id recorded on the intents
  RECORDING_RETENTION_PURGE_BATCH     default "200" recordings per run
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException

from app.api.v1.endpoints import recordings as recordings_endpoint
from app.api.v1.endpoints.admin import media
from app.core.db_utils import acquire_with_tenant
from app.domain.services.recording_service import S3Client

logger = logging.getLogger("recording_retention_purge")

ENABLED_ENV = "RECORDING_RETENTION_PURGE_ENABLED"
ACTOR_ENV = "RECORDING_RETENTION_PURGE_ACTOR_ID"
BATCH_ENV = "RECORDING_RETENTION_PURGE_BATCH"
DEFAULT_BATCH = 200

# The reason is part of the idempotency binding (a replay must carry the same
# text), so it is a constant. Per-recording detail goes to the log.
_REASON = "Automatic retention expiry: recording is older than the tenant retention policy"


def purge_enabled() -> bool:
    """True only when the owner has deliberately switched the purge on."""
    return (os.getenv(ENABLED_ENV, "false") or "").strip().lower() in {"1", "true", "yes"}


def purge_actor_id() -> Optional[str]:
    return (os.getenv(ACTOR_ENV) or "").strip() or None


def purge_batch_size() -> int:
    raw = (os.getenv(BATCH_ENV) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_BATCH
    except ValueError:
        logger.warning("%s=%r is not an integer - using %d", BATCH_ENV, raw, DEFAULT_BATCH)
        return DEFAULT_BATCH
    return value if value > 0 else DEFAULT_BATCH


_SELECT_EXPIRED_SQL = f"""
    SELECT r.id, r.tenant_id, r.call_id, r.s3_bucket, r.s3_key, p.retention_days
    FROM recordings_s3 r
    JOIN tenant_recording_policy p ON p.tenant_id = r.tenant_id
    WHERE r.status = 'uploaded'
      AND p.retention_days > 0
      AND r.created_at < NOW() - make_interval(days => p.retention_days)
      AND NOT {media._ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression="r.tenant_id")}
    ORDER BY r.created_at
    LIMIT $1
"""


async def select_expired_recordings(pool: Any, limit: int) -> list[Any]:
    # Cross-tenant worker read (no single tenant context); the tenant binding
    # is the JOIN on tenant_id and every later write carries expected_tenant_id.
    async with acquire_with_tenant(pool, None) as conn:
        return list(await conn.fetch(_SELECT_EXPIRED_SQL, limit))


async def _erase_object(snapshot: dict[str, Any]) -> None:
    if not snapshot.get("s3_key"):
        return
    if snapshot.get("s3_bucket") == "local":
        path = recordings_endpoint._validated_recording_path(str(snapshot["s3_key"]))
        with suppress(FileNotFoundError):
            await asyncio.to_thread(os.remove, path)
        return
    s3 = S3Client()
    if not s3.is_available():
        raise RuntimeError("recording object storage is unavailable")
    await asyncio.to_thread(
        s3.delete_permanently, str(snapshot["s3_key"]), str(snapshot["s3_bucket"])
    )


async def _purge_one(db: Any, row: Any, actor_id: str) -> str:
    """Delete one recording; returns deleted | already_gone | held | busy | failed."""
    recording_id = UUID(str(row["id"]))
    tenant_id = UUID(str(row["tenant_id"]))
    try:
        claim = await media._claim_media_deletion(
            db,
            resource_type="recording",
            resource_id=recording_id,
            actor_id=actor_id,
            idempotency_key=f"retention-purge:{recording_id}",
            reason=_REASON,
            expected_tenant_id=tenant_id,
        )
        if claim is None or claim.status == "completed":
            return "already_gone"

        if claim.status != "object_deleted":
            storage_error = await media._execute_serialized_media_deletion(
                db, claim, lambda: _erase_object(claim.snapshot)
            )
            if storage_error is not None:
                logger.error(
                    "retention_purge_storage_failed tenant=%s recording=%s err=%s",
                    tenant_id, recording_id, type(storage_error).__name__,
                )
                return "failed"

        await media._complete_recording_deletion(
            db,
            claim=claim,
            recording_id=recording_id,
            response_body={
                "detail": "Recording deleted by retention policy",
                "deletion_intent_id": str(claim.intent_id),
            },
            expected_tenant_id=tenant_id,
        )
        return "deleted"
    except HTTPException as exc:
        if exc.status_code == 423:
            logger.info("retention_purge_held tenant=%s recording=%s", tenant_id, recording_id)
            return "held"
        if exc.status_code == 409:
            logger.info("retention_purge_busy tenant=%s recording=%s", tenant_id, recording_id)
            return "busy"
        logger.error(
            "retention_purge_failed tenant=%s recording=%s status=%s",
            tenant_id, recording_id, exc.status_code,
        )
        return "failed"
    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the pass
        logger.error(
            "retention_purge_failed tenant=%s recording=%s err=%s",
            tenant_id, recording_id, type(exc).__name__,
        )
        return "failed"


async def purge_expired_recordings(
    pool: Any,
    *,
    enabled: bool,
    actor_id: Optional[str],
    limit: int,
) -> dict[str, int]:
    """One retention pass. Dry-run (``enabled=False``) deletes nothing."""
    rows = await select_expired_recordings(pool, limit)
    summary: dict[str, int] = {
        "candidates": len(rows),
        "would_delete": 0,
        "deleted": 0,
        "already_gone": 0,
        "held": 0,
        "busy": 0,
        "failed": 0,
    }
    if not rows:
        logger.info("recording_retention_purge enabled=%s candidates=0", enabled)
        return summary

    if not enabled:
        per_tenant = Counter(str(r["tenant_id"])[:8] for r in rows)
        summary["would_delete"] = len(rows)
        logger.info(
            "recording_retention_purge DRY-RUN would_delete=%d per_tenant=%s "
            "(set %s=true and %s to enable)",
            len(rows), dict(per_tenant), ENABLED_ENV, ACTOR_ENV,
        )
        return summary

    if not actor_id:
        raise RuntimeError(
            f"{ENABLED_ENV} is on but {ACTOR_ENV} is not set; refusing to delete without an actor"
        )
    UUID(str(actor_id))  # fail before touching storage on a malformed actor

    db = SimpleNamespace(pool=pool)
    for row in rows:
        outcome = await _purge_one(db, row, str(actor_id))
        summary[outcome] += 1
    logger.info("recording_retention_purge enabled=True summary=%s", summary)
    return summary
