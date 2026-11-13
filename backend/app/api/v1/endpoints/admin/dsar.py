"""Platform-admin DSAR preview/export only; destructive apply is unavailable."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_pool,
    require_platform_admin,
)
from app.domain.services.dsar_service import DsarScope, DsarService


router = APIRouter(prefix="/dsar", tags=["admin-dsar"])


class _PrivateExportResponse(FileResponse):
    """Remove owned PII files even on Range rejection or interrupted sends."""

    def __init__(self, path, *, directory, **kwargs):
        super().__init__(path, **kwargs)
        self._directory = directory

    async def __call__(self, scope, receive, send):
        # Own byte-stream completion rather than hand the path to a server
        # extension that might finish reading after its send() call returns.
        scope = {**scope, "extensions": {
            key: value for key, value in scope.get("extensions", {}).items()
            if key != "http.response.pathsend"
        }}
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Synchronous cleanup is not skipped by cancellation at an await.
            # The superclass unwinds its open file before reaching this block.
            shutil.rmtree(self._directory, ignore_errors=True)


def get_dsar_service(pool=Depends(get_db_pool), audit=Depends(get_audit_logger)):
    return DsarService(pool, audit=audit)


@router.post("/preview")
async def preview_dsar(
    scope: DsarScope,
    current_user: CurrentUser = Depends(require_platform_admin),
    service: DsarService = Depends(get_dsar_service),
):
    """Count the supported erase scope without changing business data/files."""
    return await service.preview(scope, actor_id=current_user.id)


@router.post("/export")
async def export_dsar(
    scope: DsarScope,
    current_user: CurrentUser = Depends(require_platform_admin),
    service: DsarService = Depends(get_dsar_service),
):
    """Download a private audited ZIP; manifest records all known coverage gaps."""
    # mkdtemp creates an owner-only directory; the service creates its file with
    # O_EXCL/0600. The request cannot choose the server destination or filename.
    directory = Path(tempfile.mkdtemp(prefix="talky-dsar-"))
    try:
        result = await service.export(scope, actor_id=current_user.id, output=directory / "export.zip")
        return _PrivateExportResponse(
            result.path, media_type="application/zip", filename="dsar-export.zip",
            directory=directory,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
