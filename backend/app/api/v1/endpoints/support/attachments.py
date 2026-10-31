"""Support attachment upload and permission-checked download."""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.api.v1.dependencies import get_db_pool
from app.api.v1.endpoints.support.deps import file_actor, not_found, run, settings_dep
from app.core.errors import ApiError
from app.domain.services.support import attachments
from app.domain.services.support.actor import SupportActor
from app.domain.services.support.attachment_files import verify_download
from app.domain.services.support.config import SupportSettings

router = APIRouter()
API_PREFIX = "/api/v1"


@router.post("/support/attachments", status_code=201)
async def upload_attachment(file: UploadFile = File(...), actor: SupportActor = Depends(file_actor),
                            pool: asyncpg.Pool = Depends(get_db_pool),
                            settings: SupportSettings = Depends(settings_dep)):
    # Read one byte past the limit so an oversized file is refused without
    # buffering all of it.
    data = await file.read(settings.attachment_max_bytes + 1)
    return await run(attachments.upload(pool, actor, raw_name=file.filename, data=data,
                                        max_bytes=settings.attachment_max_bytes))


@router.get("/support/attachments/{attachment_id}")
async def download_attachment(attachment_id: UUID, actor: SupportActor = Depends(file_actor),
                              pool: asyncpg.Pool = Depends(get_db_pool)):
    row = await run(attachments.authorize_download(pool, actor, attachment_id))
    url = await run(_url(row))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


async def _url(row: dict) -> str:
    return attachments.download_url(row, api_prefix=API_PREFIX)


@router.get("/support/attachments/{attachment_id}/content")
async def signed_content(attachment_id: UUID, expires: int = Query(...), signature: str = Query(..., max_length=128),
                         pool: asyncpg.Pool = Depends(get_db_pool), settings: SupportSettings = Depends(settings_dep)):
    """Local-disk deployments only: the short-lived signed link is the capability."""
    if not settings.enabled:
        raise not_found()
    if not verify_download(str(attachment_id), expires, signature):
        raise ApiError(status=403, code="link_expired", message="This download link has expired. Open it again.")
    row = await run(attachments.fetch_for_signed_download(pool, attachment_id))
    if row is None or row["storage_backend"] != "local":
        raise not_found()
    try:
        data = attachments.read_local(row["storage_key"])
    except FileNotFoundError as exc:
        raise not_found() from exc
    safe_name = row["filename"].replace('"', "")
    return Response(content=data, media_type=row["content_type"], headers={
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })
