"""POST /telephony/sip/runtime/compile/preview — deterministic compile."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.core.tenant_rls import apply_tenant_rls_context
from app.domain.services.telephony_runtime_policy import (
    PolicyCompilationError,
    compile_tenant_runtime_policy,
)

from ._shared import _load_active_snapshot, _problem, _require_tenant
from .schemas import RuntimeCompilePreviewResponse

router = APIRouter(tags=["Telephony SIP Runtime"])


@router.post("/compile/preview", response_model=RuntimeCompilePreviewResponse)
async def preview_runtime_policy(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
):
    tenant_problem = _require_tenant(request, current_user)
    if tenant_problem:
        return tenant_problem

    async with db_pool.acquire() as conn:
        await apply_tenant_rls_context(conn, current_user.tenant_id, current_user.id, request_id=request.headers.get("x-request-id"))
        snapshot = await _load_active_snapshot(conn, current_user.tenant_id)

    try:
        compiled = compile_tenant_runtime_policy(
            tenant_id=current_user.tenant_id,
            trunks=snapshot["trunks"],
            codec_policies=snapshot["codecs"],
            route_policies=snapshot["routes"],
            trust_policies=snapshot["trust_policies"],
        )
    except PolicyCompilationError as exc:
        return _problem(
            request,
            status_code=422,
            title="Runtime Policy Validation Failed",
            detail="Active policy data failed compilation validation.",
            type_suffix="runtime-policy-validation-failed",
            extras={"errors": [issue.to_dict() for issue in exc.issues]},
        )

    return RuntimeCompilePreviewResponse(
        schema_version=compiled.schema_version,
        source_hash=compiled.source_hash,
        active_trunks=len(snapshot["trunks"]),
        active_codecs=len(snapshot["codecs"]),
        active_routes=len(snapshot["routes"]),
        active_trust_policies=len(snapshot["trust_policies"]),
        artifact=compiled.artifact,
    )
