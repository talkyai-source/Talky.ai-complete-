"""Support attachments: private storage, permission-checked short-lived download.

Objects live in the recordings bucket (the existing ``S3Client``) under a
``support/<tenant>/`` prefix, or ``support/staff/<user>/`` for staff uploads
until they are attached. Without S3 they fall back to local disk outside
production, or in production only when SUPPORT_ALLOW_LOCAL_STORAGE=true.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import require_current_platform_admin
from app.domain.services.recording_service import S3Client
from app.domain.services.support.access import classify_missing
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.attachment_files import (
    SIGNED_URL_TTL_SECONDS,
    check_file,
    sign_download,
)
from app.domain.services.support.errors import SupportForbidden, SupportNotFound, SupportUnavailable

logger = logging.getLogger(__name__)

UNATTACHED_TTL = timedelta(hours=24)


def _local_root() -> str:
    return os.path.abspath(os.getenv("SUPPORT_ATTACHMENT_LOCAL_DIR", "./support_attachments"))


def _local_allowed() -> bool:
    if os.getenv("ENVIRONMENT", "development").strip().lower() != "production":
        return True
    return os.getenv("SUPPORT_ALLOW_LOCAL_STORAGE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _local_path(key: str) -> str:
    root = os.path.realpath(_local_root())
    path = os.path.realpath(os.path.join(root, *key.split("/")))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("storage key escapes the attachment root")
    return path


def _write_local(key: str, data: bytes) -> None:
    path = _local_path(key)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def read_local(key: str) -> bytes:
    with open(_local_path(key), "rb") as handle:
        return handle.read()


def _s3() -> S3Client:
    client = S3Client()
    client.presigned_expiry = SIGNED_URL_TTL_SECONDS
    return client


async def upload(pool, actor: SupportActor, *, raw_name: str | None, data: bytes, max_bytes: int) -> dict:
    checked = check_file(raw_name, data, max_bytes=max_bytes)
    staff_upload = actor.can_manage and not actor.tenant_id
    if not staff_upload:
        actor.require_tenant()
    attachment_id = uuid4()
    owner = f"staff/{actor.user_id}" if staff_upload else actor.tenant_id
    key = f"support/{owner}/{attachment_id}{checked.extension}"
    s3 = _s3()
    try:
        if s3.is_available():
            await asyncio.to_thread(s3.upload, key, data, content_type=checked.content_type)
            backend = "s3"
        elif _local_allowed():
            await asyncio.to_thread(_write_local, key, data)
            backend = "local"
        else:
            raise SupportUnavailable("Attachment storage is not configured.", code="attachment_storage_unavailable")
    except SupportUnavailable:
        raise
    except Exception as exc:
        logger.error("support_attachment_store_failed error_type=%s", type(exc).__name__)
        raise SupportUnavailable("The file could not be stored. Try again.", code="attachment_storage_failed") from exc
    tenant_id = None if staff_upload else actor.tenant_id
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        await conn.execute(
            """INSERT INTO support_ticket_attachments
                   (id, tenant_id, uploaded_by, storage_backend, storage_key, filename, content_type, size_bytes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            attachment_id, tenant_id, actor.user_id, backend, key, checked.filename, checked.content_type, len(data))
    return {"attachment_id": attachment_id, "filename": checked.filename,
            "content_type": checked.content_type, "size_bytes": len(data)}


_ATTACHMENT_COLUMNS = """a.id, a.tenant_id, a.ticket_id, a.message_id, a.uploaded_by, a.storage_backend,
    a.storage_key, a.filename, a.content_type, m.is_internal, t.requester_user_id"""
_ATTACHMENT_FROM = """support_ticket_attachments a
    LEFT JOIN support_ticket_messages m ON m.id = a.message_id AND m.tenant_id = a.tenant_id
    LEFT JOIN support_tickets t ON t.id = a.ticket_id AND t.tenant_id = a.tenant_id"""


async def authorize_download(pool, actor: SupportActor, attachment_id: UUID) -> dict:
    """Return the attachment row when this actor may download it, else 403/404."""
    if actor.can_manage:
        async with acquire_with_tenant(pool, None, user_id=actor.user_id) as conn:
            await require_current_platform_admin(conn, actor.user_id)
            row = await conn.fetchrow(f"SELECT {_ATTACHMENT_COLUMNS} FROM {_ATTACHMENT_FROM} WHERE a.id = $1",
                                      attachment_id)
        if row is None:
            raise SupportNotFound("Attachment not found.")
        return dict(row)
    tenant_id = actor.require_tenant()
    async with acquire_with_tenant(pool, tenant_id, user_id=actor.user_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_ATTACHMENT_COLUMNS} FROM {_ATTACHMENT_FROM} WHERE a.id = $1 AND a.tenant_id = $2",
            attachment_id, tenant_id)
        if row is None:
            await classify_missing(conn, "support_ticket_attachments", attachment_id)
    row = dict(row)
    if row["message_id"] is None:
        if str(row["uploaded_by"]) != str(actor.user_id):
            raise SupportForbidden()
        return row
    if row["is_internal"]:
        raise SupportForbidden()
    if str(row["requester_user_id"]) != str(actor.user_id) and not actor.can_read_team:
        raise SupportForbidden()
    return row


def download_url(row: dict, *, api_prefix: str) -> str:
    """A link that works for at most SIGNED_URL_TTL_SECONDS."""
    if row["storage_backend"] == "s3":
        s3 = _s3()
        if not s3.is_available():
            raise SupportUnavailable("Attachment storage is not configured.", code="attachment_storage_unavailable")
        return s3.presigned_url(row["storage_key"], download_filename=row["filename"])
    expires, signature = sign_download(str(row["id"]))
    return f"{api_prefix}/support/attachments/{row['id']}/content?expires={expires}&signature={signature}"


async def fetch_for_signed_download(pool, attachment_id: UUID) -> dict | None:
    """The signed link is the capability; this read only locates the file."""
    async with acquire_with_tenant(pool, None) as conn:
        row = await conn.fetchrow(
            """SELECT id, storage_backend, storage_key, filename, content_type
               FROM support_ticket_attachments WHERE id = $1""", attachment_id)
    return dict(row) if row else None


async def delete_unattached(pool, *, now: datetime | None = None, limit: int = 500) -> int:
    """Remove uploads never attached within 24 hours.

    The row goes first, in one statement that skips rows being attached right
    now, so a concurrent attach either wins (row kept) or finds nothing to
    attach (and the customer re-uploads). A failed object delete leaves an
    unreferenced object, never a ticket pointing at a missing file.
    """
    cutoff = (now or datetime.now(timezone.utc)) - UNATTACHED_TTL
    s3 = _s3()
    backends = ["local", "s3"] if s3.is_available() else ["local"]
    async with acquire_with_tenant(pool, None) as conn:
        rows = await conn.fetch(
            """DELETE FROM support_ticket_attachments
               WHERE id IN (SELECT id FROM support_ticket_attachments
                            WHERE message_id IS NULL AND created_at < $1 AND storage_backend = ANY($3::text[])
                            ORDER BY created_at LIMIT $2 FOR UPDATE SKIP LOCKED)
               RETURNING id, storage_backend, storage_key""", cutoff, limit, backends)
    failed = 0
    for row in rows:
        try:
            if row["storage_backend"] == "s3":
                await asyncio.to_thread(s3.delete, row["storage_key"])
            else:
                path = _local_path(row["storage_key"])
                if os.path.exists(path):
                    os.remove(path)
        except Exception as exc:
            failed += 1
            logger.warning("support_attachment_object_delete_failed error_type=%s", type(exc).__name__)
    if failed:
        logger.warning("support_attachment_cleanup_orphaned_objects count=%d", failed)
    return len(rows)
