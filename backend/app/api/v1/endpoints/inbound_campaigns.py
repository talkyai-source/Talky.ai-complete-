"""Tenant-facing inbound campaign lifecycle API."""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status

from app.api.v1.dependencies import (
    CurrentUser,
    get_db_pool,
    get_current_user,
)
from app.api.v1.schemas.inbound_campaigns import (
    InboundCampaignCreateRequest,
    InboundCampaignListResponse,
    InboundCampaignResponse,
    InboundCampaignUpdateRequest,
    InboundDidAssignmentRequest,
    InboundDidAvailabilityResponse,
    InboundReadiness,
    InboundRuntimeCapabilitiesResponse,
    InboundVersionRequest,
    TenantInboundControlsPatch,
    TenantInboundControlsResponse,
)
from app.core.security.rbac import (
    Permission,
    check_permission,
    get_effective_permissions,
)
from app.domain.services.inbound_campaign_service import (
    InboundCampaignError,
    InboundCampaignService,
    InboundReadinessError,
)


router = APIRouter(prefix="/inbound-campaigns", tags=["Inbound Campaigns"])
logger = logging.getLogger(__name__)


def _tenant(user: CurrentUser) -> str:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant context required")
    return str(user.tenant_id)


def _require_inbound_permission(permission: Permission):
    async def dependency(
        user: CurrentUser = Depends(get_current_user),
        db_pool: asyncpg.Pool = Depends(get_db_pool),
    ) -> CurrentUser:
        tenant_id = _tenant(user)
        try:
            permissions = await get_effective_permissions(db_pool, user.id, tenant_id)
        except Exception as exc:  # noqa: BLE001 - authorization fails closed
            logger.error(
                "inbound_permission_lookup_failed tenant=%s user=%s err_type=%s",
                tenant_id,
                user.id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "authorization_unavailable"},
            ) from exc

        user.set_permissions({item.value for item in permissions})
        if not check_permission(permissions, permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "required": permission.value},
            )
        return user

    return dependency


def _key(value: str = Header(..., alias="Idempotency-Key")) -> str:
    if not 8 <= len(value) <= 255:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must be between 8 and 255 characters",
            },
        )
    return value


def _raise_service(exc: InboundCampaignError) -> None:
    detail: dict = {"code": exc.code, "message": str(exc)}
    if isinstance(exc, InboundReadinessError):
        detail["readiness"] = exc.readiness
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.get("/", response_model=InboundCampaignListResponse)
async def list_inbound_campaigns(
    include_archived: bool = Query(
        False,
        description="Include archived inbound campaigns in the result.",
    ),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_READ)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        items = await InboundCampaignService(db_pool).list_campaigns(
            tenant_id=_tenant(user),
            include_archived=include_archived,
        )
        return {"items": items, "total": len(items)}
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post(
    "/",
    response_model=InboundCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_inbound_permission(Permission.INBOUND_ASSIGN))],
)
async def create_inbound_campaign(
    payload: InboundCampaignCreateRequest,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    body = payload.model_dump()
    if payload.agent is not None:
        body["agent_row"] = await prepare_inbound_agent_row(
            db_pool, tenant_id=_tenant(user), name=payload.name, agent=payload.agent.model_dump()
        )
    body.pop("agent", None)
    try:
        return await InboundCampaignService(db_pool).create_campaign(
            tenant_id=_tenant(user),
            actor_id=user.id,
            actor_role=user.role,
            payload=body,
            idempotency_key=idempotency_key,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


async def prepare_inbound_agent_row(db_pool, *, tenant_id: str, name: str, agent: dict) -> dict:
    """Turn the inline agent definition into the ``campaigns`` row the inbound
    service inserts — with the SAME validation the outbound creator applies:
    the voice must exist for the effective TTS provider and the composed
    prompt must pass the production prompt path. HTTP errors here mirror
    ``POST /campaigns`` so the two creators fail the same way."""
    from app.api.v1.endpoints.ai_options import _fetch_tenant_config
    from app.api.v1.endpoints.campaigns import _valid_voice_ids_for_provider
    from app.core.db_utils import acquire_with_tenant
    from app.domain.models.ai_config import AIProviderConfig
    from app.domain.services.campaign_prompt_service import (
        CampaignPromptValidationError,
        build_validated_script_config,
    )

    async with acquire_with_tenant(db_pool, tenant_id) as conn:
        ai_config = await _fetch_tenant_config(conn, tenant_id)
    if ai_config is None:
        ai_config = AIProviderConfig()
    effective_provider = (agent.get("tts_provider") or ai_config.tts_provider or "").strip()
    voice_id = str(agent["voice_id"]).strip()
    if voice_id not in await _valid_voice_ids_for_provider(effective_provider):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Voice '{voice_id}' is not available for TTS provider "
                f"'{effective_provider}'. Pick a matching voice or change the provider."
            ),
        )
    goal = (agent.get("goal") or "").strip()
    try:
        script_config = build_validated_script_config(
            persona_type=agent["persona_type"],
            company_name=agent["company_name"],
            agent_names=list(agent["agent_names"]),
            campaign_slots=dict(agent.get("campaign_slots") or {}),
            additional_instructions=goal,
            knowledge_driven=bool(agent.get("knowledge_driven", True)),
            campaign_brief=agent.get("campaign_brief"),
        )
    except CampaignPromptValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent.get("agent_name_genders"):
        script_config["agent_name_genders"] = agent["agent_name_genders"]
    return {
        "name": name.strip(),
        "system_prompt": goal,
        "voice_id": voice_id,
        "tts_provider": agent.get("tts_provider") or None,
        "goal": goal or None,
        "script_config": script_config,
    }


# Literal paths must be registered before /{config_id}.
@router.get("/dids/availability", response_model=InboundDidAvailabilityResponse)
async def inbound_did_availability(
    did_number: str = Query(...),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_READ)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).did_availability(
            tenant_id=_tenant(user), did_number=did_number
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/controls", response_model=TenantInboundControlsResponse)
async def get_tenant_inbound_controls(
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_CONTROLS)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).get_tenant_controls(tenant_id=_tenant(user))
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.patch("/controls", response_model=TenantInboundControlsResponse)
async def patch_tenant_inbound_controls(
    payload: TenantInboundControlsPatch,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_CONTROLS)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).set_tenant_controls(
            tenant_id=_tenant(user),
            actor_id=user.id,
            actor_role=user.role,
            inbound_enabled=payload.inbound_enabled,
            expected_version=payload.expected_version,
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/capabilities", response_model=InboundRuntimeCapabilitiesResponse)
async def get_inbound_runtime_capabilities(
    config_id: Optional[str] = Query(
        None,
        description="Existing inbound config to evaluate against the staging proof scope.",
    ),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_READ)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).get_runtime_capabilities(
            tenant_id=_tenant(user),
            config_id=config_id,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/{config_id}", response_model=InboundCampaignResponse)
async def get_inbound_campaign(
    config_id: str,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_READ)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).get_campaign(
            tenant_id=_tenant(user), config_id=config_id
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.get("/{config_id}/readiness", response_model=InboundReadiness)
async def get_inbound_campaign_readiness(
    config_id: str,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_READ)),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    try:
        return await InboundCampaignService(db_pool).readiness(
            tenant_id=_tenant(user), config_id=config_id
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


async def _update(
    config_id: str,
    payload: InboundCampaignUpdateRequest,
    user: CurrentUser,
    idempotency_key: str,
    db_pool: asyncpg.Pool,
    assignment_workflow: bool = False,
) -> dict:
    try:
        return await InboundCampaignService(db_pool).update_campaign(
            tenant_id=_tenant(user),
            config_id=config_id,
            actor_id=user.id,
            actor_role=user.role,
            payload=payload.model_dump(exclude_unset=True),
            idempotency_key=idempotency_key,
            assignment_workflow=assignment_workflow,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.patch("/{config_id}", response_model=InboundCampaignResponse)
async def patch_inbound_campaign(
    config_id: str,
    payload: InboundCampaignUpdateRequest,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _update(config_id, payload, user, idempotency_key, db_pool)


@router.put("/{config_id}", response_model=InboundCampaignResponse)
async def replace_inbound_campaign(
    config_id: str,
    payload: InboundCampaignUpdateRequest,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    """Compatibility alias; PUT still requires optimistic expected_version."""

    return await _update(config_id, payload, user, idempotency_key, db_pool)


@router.post("/{config_id}/assign", response_model=InboundCampaignResponse)
async def assign_inbound_did(
    config_id: str,
    payload: InboundDidAssignmentRequest,
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_ASSIGN)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    update = InboundCampaignUpdateRequest(
        expected_version=payload.expected_version,
        did_number=payload.did_number,
        sip_trunk_id=payload.sip_trunk_id,
        reason=payload.reason,
    )
    return await _update(
        config_id,
        update,
        user,
        idempotency_key,
        db_pool,
        assignment_workflow=True,
    )


async def _lifecycle(
    *,
    config_id: str,
    target: str,
    payload: InboundVersionRequest,
    user: CurrentUser,
    idempotency_key: str,
    db_pool: asyncpg.Pool,
) -> dict:
    try:
        return await InboundCampaignService(db_pool).set_lifecycle(
            tenant_id=_tenant(user),
            config_id=config_id,
            target_status=target,
            actor_id=user.id,
            actor_role=user.role,
            idempotency_key=idempotency_key,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
    except InboundCampaignError as exc:
        _raise_service(exc)


@router.post("/{config_id}/activate", response_model=InboundCampaignResponse)
async def activate_inbound_campaign(
    config_id: str,
    payload: InboundVersionRequest = Body(...),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _lifecycle(
        config_id=config_id,
        target="active",
        payload=payload,
        user=user,
        idempotency_key=idempotency_key,
        db_pool=db_pool,
    )


@router.post("/{config_id}/pause", response_model=InboundCampaignResponse)
@router.post("/{config_id}/deactivate", response_model=InboundCampaignResponse)
async def pause_inbound_campaign(
    config_id: str,
    payload: InboundVersionRequest = Body(...),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _lifecycle(
        config_id=config_id,
        target="paused",
        payload=payload,
        user=user,
        idempotency_key=idempotency_key,
        db_pool=db_pool,
    )


@router.post("/{config_id}/archive", response_model=InboundCampaignResponse)
async def archive_inbound_campaign(
    config_id: str,
    payload: InboundVersionRequest = Body(...),
    user: CurrentUser = Depends(_require_inbound_permission(Permission.INBOUND_MANAGE)),
    idempotency_key: str = Depends(_key),
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict:
    return await _lifecycle(
        config_id=config_id,
        target="archived",
        payload=payload,
        user=user,
        idempotency_key=idempotency_key,
        db_pool=db_pool,
    )
