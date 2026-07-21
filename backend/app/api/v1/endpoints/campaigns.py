"""
Campaigns API with Dialer Engine Integration
Handles campaign CRUD, contact management, and job enqueueing for the dialer

Day 9 Additions:
- POST /campaigns/{id}/contacts - Add single contact to campaign
- GET /campaigns/{id}/contacts - List contacts for a campaign with pagination

Refactored: Business logic delegates to CampaignService.
"""
import logging
import os
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from app.core.postgres_adapter import Client
from app.core.dotenv_compat import load_dotenv

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.queue_service import DialerQueueService
from app.domain.services.campaign_service import (
    CampaignService, CampaignError, CampaignNotFoundError, CampaignStateError
)
from app.domain.services.phone_number_normalizer import (
    normalize_phone_number,
    normalize_phone_number_lenient,
)
from app.domain.services.event_emitter import emit_event
from app.api.v1.dependencies import (
    get_db_client,
    get_db_read_client,
    get_current_user,
    CurrentUser,
)
from app.api.v1.schemas.campaigns import (
    ApplyTtsConfigRequest,
    CampaignCreateRequest,
    CampaignPromptPreviewRequest,
    CampaignPromptPreviewResponse,
    CampaignStartRequest,
    CampaignUpdateRequest,
    ContactCreate,
    ContactUpdate,
    ContactListResponse,
)

load_dotenv()

logger = logging.getLogger(__name__)


def _get_campaign_service(db_client: Client) -> CampaignService:
    """Build CampaignService using the DI container's queue service."""
    from app.core.container import get_container
    container = get_container()
    queue_service = container.queue_service if container.is_initialized else None
    return CampaignService(db_client, queue_service=queue_service)

from app.core.security.api_security import rate_limit_dependency
from app.core.security.rbac import require_permission, Permission
from app.core.security.idempotency import (
    idempotency_dependency, 
    store_idempotent_response, 
    release_idempotency_lock
)
import json

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _build_validated_script_config(
    *,
    persona_type: str,
    company_name: str,
    agent_names: List[str],
    campaign_slots: dict,
    additional_instructions: str,
    knowledge_driven: bool = False,
) -> dict[str, Any]:
    """HTTP wrapper around the domain prompt validation service."""
    from app.domain.services.campaign_prompt_service import (
        CampaignPromptValidationError,
        build_validated_script_config as _build_script_config,
    )

    try:
        return _build_script_config(
            persona_type=persona_type,
            company_name=company_name,
            agent_names=agent_names,
            campaign_slots=campaign_slots,
            additional_instructions=additional_instructions,
            knowledge_driven=knowledge_driven,
        )
    except CampaignPromptValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _valid_voice_ids_for_provider(provider: str) -> set[str]:
    """The set of voice ids valid for a given TTS provider.

    Single source of truth for create/update/bulk-apply so a campaign's
    per-campaign voice is always validated against the provider it will
    actually run on (not just the tenant global).
    """
    from app.api.v1.endpoints.ai_options import (
        _english_google_voices,
        _get_deepgram_voices_for_current_key,
        get_elevenlabs_voices_for_current_key,
    )

    if provider == "google":
        return {v.id for v in _english_google_voices()}
    if provider == "deepgram":
        return {v.id for v in await _get_deepgram_voices_for_current_key()}
    if provider == "cartesia":
        from app.api.v1.endpoints.ai_options._catalog import _get_live_cartesia_voices
        return {v.id for v in await _get_live_cartesia_voices()}
    # default / "elevenlabs"
    return {v.id for v in await get_elevenlabs_voices_for_current_key()}


@router.get("/")
async def list_campaigns(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    # Phase 3.3 — read-only endpoint routes to the replica pool when
    # READ_DATABASE_URL is configured; falls back to the primary pool
    # otherwise (no behaviour change in single-DB deploys).
    db_client: Client = Depends(get_db_read_client),
):
    """List all campaigns belonging to the current user's tenant.

    The `get_current_user` dependency MUST be present here. Without it
    the request never establishes a tenant context, RLS treats the
    connection as anonymous, and the campaigns table returns 0 rows —
    visible to the frontend as an empty list / "no campaigns found"
    even when the tenant has many. This was the root cause of the
    'frontend shows no campaigns' bug. Defense-in-depth: also apply an
    explicit tenant filter so a future RLS misconfiguration can't leak
    other tenants' data through this endpoint.
    """
    if not current_user.tenant_id:
        # Authenticated but unattached user — return empty list rather
        # than 500 so the dashboard renders cleanly.
        return {"campaigns": []}

    try:
        from app.utils.tenant_filter import apply_tenant_filter
        query = db_client.table("campaigns").select("*")
        query = apply_tenant_filter(query, current_user.tenant_id)
        # Hide soft-deleted campaigns (see delete_campaign) so a deleted
        # campaign doesn't reappear in the list on refresh.
        query = query.neq("status", "deleted")
        query = query.order("created_at", desc=True)
        response = query.execute()
        return {"campaigns": response.data}
    except Exception as e:
        logger.error(f"Error listing campaigns: {e}")
        raise HTTPException(status_code=500, detail="Failed to list campaigns")


@router.post(
    "/preview-prompt",
    response_model=CampaignPromptPreviewResponse,
    dependencies=[Depends(rate_limit_dependency)],
)
async def preview_prompt(
    body: CampaignPromptPreviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Render the system prompt + TTS opener for a campaign draft (T4-B4).

    Read-only — never writes to the DB. Operators use this to see
    exactly what the AI will be told before starting a campaign,
    catching slot typos, missing pronunciations, and direction
    mismatches without burning a real call.

    Authentication is required (rate-limited; same dependency stack
    as the create endpoint), but no tenant filter is applied because
    the request body carries the entire campaign draft. Returns
    400 with a clear message when ``compose_prompt`` rejects the
    inputs (unknown persona, missing slot, etc.) — same error class
    the create / update flows surface.
    """
    from app.domain.services.telephony_session_config import (
        build_persona_greeting,
    )
    from app.services.scripts.prompts.composer import (
        PromptCompositionError,
        compose_prompt,
    )
    from app.services.scripts.prompts.direction import (
        INBOUND_DIRECTIVE_SENTINEL,
    )

    try:
        system_prompt = compose_prompt(
            persona_type=body.persona_type,
            agent_name=body.agent_name,
            company_name=body.company_name,
            campaign_slots=body.campaign_slots,
            additional_instructions=body.additional_instructions,
            direction=body.direction,
            knowledge_driven=body.knowledge_driven,
        )
    except PromptCompositionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    greeting = build_persona_greeting(
        persona_type=body.persona_type,
        agent_name=body.agent_name,
        company_name=body.company_name,
        direction=body.direction,
    )

    return CampaignPromptPreviewResponse(
        system_prompt=system_prompt,
        greeting=greeting,
        direction=body.direction,
        has_inbound_directive=INBOUND_DIRECTIVE_SENTINEL in system_prompt,
        prompt_chars=len(system_prompt),
    )


def _calling_config_from_schedule(schedule) -> Optional[dict]:
    """Build the ``campaigns.calling_config`` payload from a client-supplied
    calling schedule. Returns None when no schedule was sent so update never
    clobbers an existing config. Only fields the client actually set are
    written; the worker overlays them onto the tenant defaults at dial time."""
    if schedule is None:
        return None
    cfg: dict = {"ignore_schedule": bool(getattr(schedule, "ignore_schedule", False))}
    for key in ("timezone", "time_window_start", "time_window_end", "allowed_days"):
        val = getattr(schedule, key, None)
        if val not in (None, ""):
            cfg[key] = val
    return cfg


@router.post("/", dependencies=[Depends(rate_limit_dependency), Depends(require_permission(Permission.CAMPAIGNS_CREATE))])
async def create_campaign(
    campaign_data: CampaignCreateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(idempotency_dependency),
    db_client: Client = Depends(get_db_client)
):
    """Create a new campaign"""
    try:
        if not current_user.tenant_id:
            raise HTTPException(status_code=400, detail="Current user is not associated with a tenant")

        from app.api.v1.endpoints.ai_options import _fetch_tenant_config
        from app.domain.models.ai_config import AIProviderConfig

        selected_voice_id = campaign_data.voice_id.strip()

        async with db_client.pool.acquire() as conn:
            ai_config = await _fetch_tenant_config(conn, current_user.tenant_id)
        if ai_config is None:
            ai_config = AIProviderConfig()

        # Per-campaign provider: validate the voice against the campaign's chosen
        # provider (NULL falls back to the tenant global).
        effective_provider = (campaign_data.tts_provider or ai_config.tts_provider or "").strip()
        valid_voice_ids = await _valid_voice_ids_for_provider(effective_provider)
        if selected_voice_id not in valid_voice_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Voice '{selected_voice_id}' is not available for TTS provider "
                    f"'{effective_provider}'. Pick a matching voice or change the provider."
                ),
            )

        script_config = _build_validated_script_config(
            persona_type=campaign_data.persona_type,
            company_name=campaign_data.company_name,
            agent_names=campaign_data.agent_names,
            campaign_slots=campaign_data.campaign_slots,
            additional_instructions=campaign_data.system_prompt,
            knowledge_driven=campaign_data.knowledge_driven,
        )
        # Persist per-name gender tags so each call picks a name matching the
        # selected voice's gender. Backward compatible: absent ⇒ legacy pick.
        if campaign_data.agent_name_genders:
            script_config["agent_name_genders"] = campaign_data.agent_name_genders

        insert_payload = {
            "tenant_id": current_user.tenant_id,
            "name": campaign_data.name.strip(),
            "description": campaign_data.description.strip() if campaign_data.description else None,
            "system_prompt": campaign_data.system_prompt.strip(),
            "voice_id": selected_voice_id,
            "tts_provider": (campaign_data.tts_provider or None),
            "goal": campaign_data.goal.strip() if campaign_data.goal else None,
            "script_config": script_config,
        }
        _sched = _calling_config_from_schedule(campaign_data.calling_schedule)
        if _sched is not None:
            insert_payload["calling_config"] = _sched

        response = db_client.table("campaigns").insert(insert_payload).execute()
        if response.error:
            logger.error(f"Error creating campaign: {response.error}")
            raise HTTPException(status_code=500, detail=f"Failed to create campaign: {response.error}")
        if not response.data:
            logger.error("Campaign insert returned no rows for tenant=%s name=%s", current_user.tenant_id, insert_payload["name"])
            raise HTTPException(status_code=500, detail="Failed to create campaign")
        result = {"campaign": response.data[0]}

        # Store for idempotency (Hisham's billing layer — replays return the
        # cached response instead of double-creating).
        if idempotency_key:
            await store_idempotent_response(request, 200, json.dumps(result))

        return result
    except HTTPException:
        raise
    except Exception as e:
        if idempotency_key:
            await release_idempotency_lock(request)
        logger.error(f"Error creating campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to create campaign")


@router.post("/apply-tts-config", dependencies=[Depends(rate_limit_dependency), Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def apply_tts_config(
    body: ApplyTtsConfigRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Apply a TTS provider+voice to a chosen set of the tenant's campaigns.

    Backs the AI Options 'Save → apply to these campaigns' flow. Per-campaign
    provider means unselected campaigns keep their own engine; only the listed
    ones are changed. The voice is validated against the provider once.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="Current user is not associated with a tenant")

    provider = (body.tts_provider or "").strip()
    voice_id = (body.tts_voice_id or "").strip()
    valid_voice_ids = await _valid_voice_ids_for_provider(provider)
    if voice_id not in valid_voice_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Voice '{voice_id}' is not available for TTS provider '{provider}'.",
        )

    updated: list[str] = []
    for cid in body.campaign_ids:
        try:
            resp = (
                db_client.table("campaigns")
                .update({"tts_provider": provider, "voice_id": voice_id})
                .eq("id", cid)
                .eq("tenant_id", current_user.tenant_id)
                .execute()
            )
            if getattr(resp, "data", None):
                updated.append(str(cid))
        except Exception as exc:
            logger.warning("apply_tts_config failed for campaign=%s: %s", cid, exc)
    return {"updated": updated, "count": len(updated), "tts_provider": provider, "voice_id": voice_id}


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    # Phase 3.3 — read-only; routes to replica pool when configured.
    db_client: Client = Depends(get_db_read_client),
):
    """Get campaign details — delegates to CampaignService.

    `get_current_user` is required so the per-request RLS tenant context
    (`app.current_tenant_id`) is set; without it the SELECT returns zero
    rows and the edit page sees "Campaign not found".
    """
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.get_campaign(campaign_id)
        # asyncpg returns uuid columns as `uuid.UUID`, but current_user.tenant_id
        # is a string. Compare as strings so the tenant check doesn't reject
        # rows the user actually owns.
        row_tenant = campaign.get("tenant_id")
        row_tenant_str = str(row_tenant) if row_tenant is not None else None
        if current_user.tenant_id and row_tenant_str not in (None, current_user.tenant_id):
            raise CampaignNotFoundError(campaign_id)
        return {"campaign": campaign}
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error fetching campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get campaign")


@router.put("/{campaign_id}", dependencies=[Depends(rate_limit_dependency), Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def update_campaign(
    campaign_id: str,
    campaign_data: CampaignUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Edit a campaign without allowing prompt-composer bypass."""
    try:
        if not current_user.tenant_id:
            raise HTTPException(status_code=400, detail="Current user is not associated with a tenant")

        # Per-campaign provider: validate the voice against the campaign's chosen
        # provider (NULL falls back to the tenant global) — same gate as create.
        from app.api.v1.endpoints.ai_options import _fetch_tenant_config
        from app.domain.models.ai_config import AIProviderConfig

        selected_voice_id = campaign_data.voice_id.strip()
        async with db_client.pool.acquire() as conn:
            ai_config = await _fetch_tenant_config(conn, current_user.tenant_id)
        if ai_config is None:
            ai_config = AIProviderConfig()
        effective_provider = (campaign_data.tts_provider or ai_config.tts_provider or "").strip()
        valid_voice_ids = await _valid_voice_ids_for_provider(effective_provider)
        if selected_voice_id not in valid_voice_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Voice '{selected_voice_id}' is not available for TTS provider "
                    f"'{effective_provider}'. Pick a matching voice or change the provider."
                ),
            )

        script_config = _build_validated_script_config(
            persona_type=campaign_data.persona_type,
            company_name=campaign_data.company_name,
            agent_names=campaign_data.agent_names,
            campaign_slots=campaign_data.campaign_slots,
            additional_instructions=campaign_data.system_prompt,
            knowledge_driven=campaign_data.knowledge_driven,
        )
        if campaign_data.agent_name_genders:
            script_config["agent_name_genders"] = campaign_data.agent_name_genders

        update_payload = {
            "name": campaign_data.name.strip(),
            "description": campaign_data.description.strip() if campaign_data.description else None,
            "system_prompt": campaign_data.system_prompt.strip(),
            "voice_id": selected_voice_id,
            "tts_provider": (campaign_data.tts_provider or None),
            "goal": campaign_data.goal.strip() if campaign_data.goal else None,
            "script_config": script_config,
        }
        _sched = _calling_config_from_schedule(campaign_data.calling_schedule)
        if _sched is not None:
            update_payload["calling_config"] = _sched

        response = (
            db_client.table("campaigns")
            .update(update_payload)
            .eq("id", campaign_id)
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        if response.error:
            logger.error(f"Error updating campaign {campaign_id}: {response.error}")
            raise HTTPException(status_code=500, detail=f"Failed to update campaign: {response.error}")
        if not response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        return {"campaign": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update campaign")


@router.get("/{campaign_id}/calls")
async def list_campaign_calls_with_transcripts(
    campaign_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Paginated list of calls for a campaign with their transcripts.

    Powers the Script Card on the campaign detail page.
    """
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="User is not associated with a tenant")

    campaign_resp = (
        db_client.table("campaigns")
        .select("id, tenant_id")
        .eq("id", campaign_id)
        .eq("tenant_id", current_user.tenant_id)
        .limit(1)
        .execute()
    )
    if not campaign_resp.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    from app.services.scripts import fetch_campaign_transcripts
    try:
        result = await fetch_campaign_transcripts(
            pool=db_client.pool,
            tenant_id=current_user.tenant_id,
            campaign_id=campaign_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"fetch_campaign_transcripts failed campaign={campaign_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to load campaign transcripts")
    return result


@router.post("/{campaign_id}/start", dependencies=[Depends(rate_limit_dependency), Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def start_campaign(
    campaign_id: str,
    request: Request,
    start_request: Optional[CampaignStartRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
    idempotency_key: Optional[str] = Depends(idempotency_dependency),
    db_client: Client = Depends(get_db_client)
):
    """
    Start a campaign — delegates to CampaignService.

    ``get_current_user`` is required so the per-request RLS tenant
    context (``app.current_tenant_id``) is set on the connection;
    without it the campaign-existence SELECT returns zero rows and
    the endpoint 404s even though the campaign is owned by the
    calling user. Same fix as ``get_campaign`` and the sibling
    stats / contacts / jobs endpoints.

    Validates, enqueues jobs, and updates status atomically.
    """
    try:
        service = _get_campaign_service(db_client)

        # Trust the authenticated user's tenant_id over any value the
        # client ships in the body — clients shouldn't be able to start
        # someone else's campaign by spoofing tenant_id in JSON.
        tenant_id = current_user.tenant_id or (
            start_request.tenant_id if start_request else None
        )
        priority_override = (start_request.priority_override if start_request else None)
        first_speaker = (start_request.first_speaker if start_request else "agent")

        # Persist the client's pacing choices (batch size + inter-call gap) onto
        # the campaign's calling_config so the dialer's gates read them. None →
        # leave that setting untouched. Merged (not overwritten) so schedule keys
        # already in calling_config survive.
        _batch_size = (start_request.batch_size if start_request else None)
        _call_gap = (start_request.call_gap_seconds if start_request else None)
        if _batch_size is not None or _call_gap is not None:
            try:
                _existing = await service.get_campaign(campaign_id)
                _cfg = dict(_existing.get("calling_config") or {})
                if _batch_size is not None:
                    _cfg["batch_size"] = int(_batch_size)
                if _call_gap is not None:
                    _cfg["call_gap_seconds"] = int(_call_gap)
                db_client.table("campaigns").update(
                    {"calling_config": _cfg}
                ).eq("id", campaign_id).execute()
            except Exception as _bs_exc:
                logger.warning(
                    "failed to persist pacing config for campaign %s: %s",
                    campaign_id, _bs_exc,
                )

        # Refuse to START a campaign when the tenant is already out of plan
        # minutes. The dialer also skips individual jobs when exhausted, but
        # blocking here means we never enqueue work that can't run and the
        # user gets an immediate, clear reason instead of a campaign that
        # silently dials nothing. 402 Payment Required is the precise code.
        if tenant_id:
            from app.domain.services.minutes_quota import tenant_minutes_status
            minutes = await tenant_minutes_status(tenant_id)
            if minutes.exhausted:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "out_of_minutes",
                        "message": (
                            f"You've used all {minutes.allocated} of your plan "
                            f"minutes this month ({minutes.used_minutes} used). "
                            "Add minutes or upgrade your plan to start campaigns."
                        ),
                        "allocated": minutes.allocated,
                        "used_minutes": minutes.used_minutes,
                        "remaining_minutes": minutes.remaining_minutes,
                    },
                )

        result = await service.start_campaign(
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            priority_override=priority_override,
            first_speaker=first_speaker,
        )
        
        response_data = {
            "message": result.message,
            "jobs_enqueued": result.jobs_enqueued,
            "queue_stats": result.queue_stats,
            "campaign": {"id": result.campaign_id, "status": "running"}
        }

        # Store for idempotency
        if idempotency_key:
            await store_idempotent_response(request, 200, json.dumps(response_data))

        if tenant_id:
            async with db_client.pool.acquire() as conn:
                await emit_event(
                    conn,
                    tenant_id=tenant_id,
                    category="campaign",
                    title="Campaign started",
                    description=f"Campaign began processing — {result.jobs_enqueued} jobs queued.",
                    related_campaign_id=str(result.campaign_id),
                    actor_user_id=current_user.id,
                    metadata={"jobs_enqueued": result.jobs_enqueued},
                )

        return response_data
    except CampaignNotFoundError:
        if idempotency_key:
            await release_idempotency_lock(request)
        raise HTTPException(status_code=404, detail="Campaign not found")
    except CampaignStateError as e:
        if idempotency_key:
            await release_idempotency_lock(request)
        raise HTTPException(status_code=400, detail=e.message)
    except CampaignError as e:
        if idempotency_key:
            await release_idempotency_lock(request)
        logger.error(f"Campaign service error for {campaign_id}: {e}")
        raise HTTPException(status_code=e.status_code, detail="Failed to start campaign")
    except HTTPException:
        # Our own deliberate HTTP errors (e.g. the 402 out-of-minutes gate
        # above) must propagate with their real status code, not be masked
        # as a 500 by the catch-all below.
        if idempotency_key:
            await release_idempotency_lock(request)
        raise
    except Exception as e:
        if idempotency_key:
            await release_idempotency_lock(request)
        logger.error(f"Unexpected error starting campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{campaign_id}/pause", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def pause_campaign(
    campaign_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Pause a campaign — delegates to CampaignService.

    ``get_current_user`` is required so the per-request RLS tenant
    context is set; without it the campaign lookup inside the service
    returns nothing and the user sees a spurious 404. Same fix as
    ``start_campaign`` / ``stop_campaign``.
    """
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.pause_campaign(campaign_id)

        if current_user.tenant_id:
            async with db_client.pool.acquire() as conn:
                await emit_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    category="user_action",
                    title="Campaign paused",
                    description="Operator paused the campaign.",
                    related_campaign_id=str(campaign_id),
                    actor_user_id=current_user.id,
                )

        return {"message": f"Campaign {campaign_id} paused", "campaign": campaign}
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error pausing campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to pause campaign")


@router.post("/{campaign_id}/stop", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def stop_campaign(
    campaign_id: str,
    clear_queue: bool = Query(False, description="Clear pending jobs from queue"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Stop a campaign — delegates to CampaignService.

    ``get_current_user`` is required so the per-request RLS tenant
    context is set; without it the campaign lookup returns nothing
    and the user sees a spurious 404. Same fix as ``start_campaign``
    / ``pause_campaign``.

    Args:
        campaign_id: Campaign UUID
        clear_queue: If True, mark pending jobs as skipped
    """
    try:
        service = _get_campaign_service(db_client)
        campaign = await service.stop_campaign(campaign_id, clear_queue=clear_queue)

        if current_user.tenant_id:
            async with db_client.pool.acquire() as conn:
                await emit_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    category="user_action",
                    title="Campaign stopped",
                    description="Operator stopped the campaign."
                                + (" Pending jobs cleared." if clear_queue else ""),
                    related_campaign_id=str(campaign_id),
                    actor_user_id=current_user.id,
                    metadata={"clear_queue": clear_queue},
                )

        return {
            "message": f"Campaign {campaign_id} stopped",
            "campaign": campaign
        }
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except Exception as e:
        logger.error(f"Error stopping campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop campaign")


@router.delete("/{campaign_id}", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_DELETE))])
async def delete_campaign(
    campaign_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Soft-delete a campaign.

    Marks status='deleted' (``list_campaigns`` filters these out) rather
    than hard-deleting, so the campaign's calls/leads history and any FK
    references are preserved — mirroring how contacts are soft-deleted.
    A running campaign is stopped and its pending dialer jobs cleared
    first so the dialer can't keep placing calls for a deleted campaign.

    ``get_current_user`` is required so the per-request RLS tenant context
    is set; without it the lookup/update matches no rows and the caller
    gets a spurious 404 (same rationale as start/stop/pause).

    NOTE: before this endpoint existed the frontend 'Delete' button called
    the STOP endpoint and only hid the row from its local cache, so the
    campaign reappeared on refresh. This is the real delete.
    """
    try:
        # Confirm it exists for THIS tenant (RLS-scoped) before mutating.
        existing = db_client.table("campaigns").select("id, status").eq(
            "id", campaign_id
        ).neq("status", "deleted").execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Best-effort stop + clear queue so a running campaign stops dialing.
        # Already-stopped/draft campaigns raise or no-op here — that's fine.
        try:
            service = _get_campaign_service(db_client)
            await service.stop_campaign(campaign_id, clear_queue=True)
        except CampaignNotFoundError:
            raise HTTPException(status_code=404, detail="Campaign not found")
        except Exception as stop_err:
            logger.info(
                f"delete_campaign: stop step skipped for {campaign_id}: {stop_err}"
            )

        updated = db_client.table("campaigns").update(
            {"status": "deleted"}
        ).eq("id", campaign_id).execute()
        if getattr(updated, "error", None):
            logger.error(
                f"Error soft-deleting campaign {campaign_id}: {updated.error}"
            )
            raise HTTPException(status_code=500, detail="Failed to delete campaign")

        if current_user.tenant_id:
            async with db_client.pool.acquire() as conn:
                await emit_event(
                    conn,
                    tenant_id=current_user.tenant_id,
                    category="user_action",
                    title="Campaign deleted",
                    description="Operator deleted the campaign.",
                    related_campaign_id=str(campaign_id),
                    actor_user_id=current_user.id,
                )

        logger.info(f"Campaign {campaign_id} soft-deleted by {current_user.id}")
        return {"message": f"Campaign {campaign_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete campaign")


@router.get("/{campaign_id}/jobs")
async def get_campaign_jobs(
    campaign_id: str,
    status: Optional[str] = Query(None, description="Filter by job status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Get dialer jobs for a campaign.

    ``get_current_user`` is required so the per-request RLS tenant
    context is set on the connection; without it the SELECT returns
    zero rows. Same fix as the sibling stats / contacts endpoints.
    """
    try:
        # RLS now scopes to the current tenant automatically. If
        # current_user has no tenant_id, return empty rather than 500.
        if not current_user.tenant_id:
            return {"jobs": [], "total": 0, "page": page, "page_size": page_size}

        query = db_client.table("dialer_jobs").select(
            "*", count="exact"
        ).eq("campaign_id", campaign_id)
        
        if status:
            query = query.eq("status", status)
        
        offset = (page - 1) * page_size
        response = query.order("created_at", desc=True).range(offset, offset + page_size - 1).execute()
        
        return {
            "jobs": response.data,
            "total": response.count,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        logger.error(f"Error fetching jobs for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch campaign jobs")


@router.get("/minutes/status")
async def get_minutes_status(
    current_user: CurrentUser = Depends(get_current_user),
):
    """This tenant's monthly call-minute quota.

    Drives the frontend's remaining-minutes display and the disabled
    state of the Start button. Same figure the dialer and the
    start-campaign gate use (shared ``minutes_quota`` helper), so the UI
    never says "go" when the backend will say "out of minutes". A static
    two-segment path so it can't be shadowed by ``/{campaign_id}``.
    """
    from app.domain.services.minutes_quota import tenant_minutes_status
    tenant_id = current_user.tenant_id
    if not tenant_id:
        return {
            "allocated": 0, "used_minutes": 0, "remaining_minutes": 0,
            "unlimited": True, "exhausted": False,
        }
    status = await tenant_minutes_status(tenant_id)
    return status.as_dict()


@router.get("/{campaign_id}/stats")
async def get_campaign_stats(
    campaign_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """Get statistics for a campaign.

    ``get_current_user`` is required so the per-request RLS tenant
    context (``app.current_tenant_id``) is set on the connection;
    without it the SELECT returns zero rows and the detail page's
    Promise.all bailout shows a 404 — even though the campaign exists
    in the DB. This is the same fix already applied to ``get_campaign``;
    keep stats / contacts / jobs in sync.
    """
    try:
        # Get campaign — RLS scopes to the current tenant automatically
        # once get_current_user is in the dependency chain.
        campaign_response = db_client.table("campaigns").select("*").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")

        campaign = campaign_response.data[0]
        # Defense-in-depth tenant check matching ``get_campaign`` —
        # protects against a future RLS misconfiguration leaking other
        # tenants' rows through this endpoint.
        row_tenant = campaign.get("tenant_id")
        row_tenant_str = str(row_tenant) if row_tenant is not None else None
        if current_user.tenant_id and row_tenant_str not in (None, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Get job counts by status
        jobs_response = db_client.table("dialer_jobs").select("status").eq("campaign_id", campaign_id).execute()
        
        status_counts = {}
        for job in jobs_response.data or []:
            status = job.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get call outcomes. "goals_achieved" = the live resolver flag OR the
        # post-call AI verdict reading as a success (qualified/callback). The
        # goal_achieved flag alone is currently never set on the call path, so
        # the AI summary verdict is the real success signal — without this the
        # card sticks at 0 even when calls clearly qualified leads.
        import json as _json
        calls_response = db_client.table("calls").select("outcome, goal_achieved, summary_json").eq("campaign_id", campaign_id).execute()

        outcome_counts = {}
        goals_achieved = 0
        for call in calls_response.data or []:
            outcome = call.get("outcome", "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            sj = call.get("summary_json")
            if isinstance(sj, str):
                try:
                    sj = _json.loads(sj)
                except Exception:
                    sj = None
            verdict = str((sj or {}).get("outcome") or "").strip().lower() if isinstance(sj, dict) else ""
            if call.get("goal_achieved") or verdict.startswith("qualified") or verdict.startswith("callback"):
                goals_achieved += 1

        # Real contact + qualified-lead counts. The campaigns.total_leads column
        # drifts (set at create, not updated on bulk contact upload — it showed 1
        # while 5 contacts existed), so count the leads table directly.
        total_leads = (
            db_client.table("leads").select("id", count="exact")
            .eq("campaign_id", campaign_id).neq("status", "deleted").execute().count or 0
        )
        qualified_leads = (
            db_client.table("leads").select("id", count="exact")
            .eq("campaign_id", campaign_id).eq("is_lead", True)
            .neq("status", "deleted").execute().count or 0
        )

        return {
            "campaign_id": campaign_id,
            "campaign_status": campaign.get("status"),
            "total_leads": total_leads,
            "qualified_leads": qualified_leads,
            "job_status_counts": status_counts,
            "call_outcome_counts": outcome_counts,
            "goals_achieved": goals_achieved
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching stats for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch campaign stats")


# =============================================================================
# Day 9: Contact Management Endpoints
# =============================================================================

# TEMP — relaxed phone validation (remove ~2026-08; see memory
# "phone_validation_relaxed_temp"). Accounts in this set may add contacts with
# short/odd test numbers that the strict validator would reject. Scoped by login
# email so it only affects these accounts and never other tenants.
_RELAXED_PHONE_EMAILS = {"uzairdevelops@gmail.com", "allestateestimation@gmail.com"}


def _phone_validation_relaxed(user) -> bool:
    """True when the signed-in user's phone validation is temporarily relaxed."""
    email = getattr(user, "email", None)
    return bool(email and email.strip().lower() in _RELAXED_PHONE_EMAILS)


@router.post("/{campaign_id}/contacts", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def add_contact_to_campaign(
    campaign_id: str,
    contact: ContactCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_db_client)
):
    """
    Add a single contact (lead) to a campaign.
    
    Day 9 Endpoint: POST /campaigns/{id}/contacts
    
    Features:
    - Validates campaign exists
    - Normalizes phone number to E.164 format
    - Checks for duplicate phone within campaign
    - Creates lead record with pending status
    
    Returns:
        Created lead object
    """
    db_client = supabase
    try:
        # 1. Validate campaign exists and belongs to the current tenant
        campaign_query = db_client.table("campaigns").select("id, name, status, tenant_id").eq("id", campaign_id)
        if current_user.tenant_id:
            campaign_query = campaign_query.eq("tenant_id", current_user.tenant_id)
        campaign_response = campaign_query.execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        campaign = campaign_response.data[0]
        
        # 2. Normalize phone number — T2.5 uses the campaign's
        # default country (from script_config.campaign_slots or
        # top-level default_country_code) so non-US campaigns route
        # correctly. Falls back to US when not configured.
        default_country = "US"
        script_cfg = campaign.get("script_config") if isinstance(campaign, dict) else None
        if isinstance(script_cfg, dict):
            candidate = (
                script_cfg.get("default_country_code")
                or (script_cfg.get("campaign_slots") or {}).get("default_country_code")
            )
            if candidate:
                default_country = str(candidate).upper()

        # Phone validation is relaxed for specific accounts (TEMP) so they can
        # add short/odd test numbers; normal numbers still normalize to E.164.
        try:
            if _phone_validation_relaxed(current_user):
                normalized_phone = normalize_phone_number_lenient(contact.phone_number)
            else:
                normalized_phone = normalize_phone_number(
                    contact.phone_number, default_country=default_country,
                )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")
        
        # 3. Look up ANY existing row for this phone (including soft-deleted).
        #    - A live row is a real duplicate -> 409.
        #    - A soft-deleted row gets REVIVED in place (keeps its id, call
        #      history and is_lead/qualified_at) instead of inserting a brand-new
        #      row. Re-adding a previously-deleted contact used to orphan all of
        #      its prior calls and lead status behind a fresh lead_id.
        existing = db_client.table("leads").select(
            "id, status, is_lead"
        ).eq(
            "campaign_id", campaign_id
        ).eq(
            "phone_number", normalized_phone
        ).execute()

        live = [r for r in (existing.data or []) if r.get("status") != "deleted"]
        if live:
            raise HTTPException(
                status_code=409,
                detail=f"Phone number {normalized_phone} already exists in this campaign"
            )

        deleted_rows = [r for r in (existing.data or []) if r.get("status") == "deleted"]
        if deleted_rows:
            # Prefer reviving a row that was a qualified lead so we don't lose it.
            deleted_rows.sort(key=lambda r: (0 if r.get("is_lead") else 1))
            revive_id = deleted_rows[0]["id"]
            revived = db_client.table("leads").update({
                "status": "pending",
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
                "custom_fields": contact.custom_fields or {},
            }).eq("id", revive_id).execute()
            if revived.error or not revived.data:
                logger.error(f"Error reviving lead {revive_id} for campaign {campaign_id}: {getattr(revived, 'error', None)}")
                raise HTTPException(status_code=500, detail="Failed to add contact")
            logger.info(f"Contact revived in campaign {campaign_id}: {normalized_phone} (lead {revive_id})")
            return {
                "message": "Contact added successfully",
                "contact": revived.data[0]
            }

        # 4. Create lead record
        lead_id = str(uuid.uuid4())
        lead_data = {
            "id": lead_id,
            "tenant_id": campaign.get("tenant_id") or current_user.tenant_id,
            "campaign_id": campaign_id,
            "phone_number": normalized_phone,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "email": contact.email,
            "custom_fields": contact.custom_fields or {},
            "status": "pending",
            "last_call_result": "pending",
            "call_attempts": 0,
            "created_at": datetime.utcnow().isoformat()
        }

        response = db_client.table("leads").insert(lead_data).execute()

        if response.error:
            logger.error(f"Error creating lead for campaign {campaign_id}: {response.error}")
            raise HTTPException(status_code=500, detail=f"Failed to create contact: {response.error}")
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create contact")

        logger.info(f"Contact added to campaign {campaign_id}: {normalized_phone}")

        return {
            "message": "Contact added successfully",
            "contact": response.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding contact to campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add contact")


@router.patch("/{campaign_id}/contacts/{contact_id}", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def update_contact_in_campaign(
    campaign_id: str,
    contact_id: str,
    contact: ContactUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_db_client),
):
    """Edit a single contact (lead) — phone, name, or email. Only the provided
    fields change. Phone is normalized the same way as add-contact (relaxed for
    flagged accounts). Tenant-scoped.
    """
    db_client = supabase
    try:
        existing = (
            db_client.table("leads")
            .select("id,phone_number,first_name,last_name,email,campaign_id")
            .eq("id", contact_id)
            .eq("campaign_id", campaign_id)
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found in this campaign")

        update_payload: dict = {}

        if contact.phone_number is not None:
            try:
                if _phone_validation_relaxed(current_user):
                    normalized_phone = normalize_phone_number_lenient(contact.phone_number)
                else:
                    normalized_phone = normalize_phone_number(contact.phone_number)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")

            # Reject duplicate within the campaign (excluding this same lead).
            dupe = (
                db_client.table("leads").select("id")
                .eq("campaign_id", campaign_id)
                .eq("phone_number", normalized_phone)
                .neq("status", "deleted")
                .neq("id", contact_id)
                .execute()
            )
            if dupe.data:
                raise HTTPException(
                    status_code=409,
                    detail=f"Phone number {normalized_phone} already exists in this campaign",
                )
            update_payload["phone_number"] = normalized_phone

        if contact.first_name is not None:
            update_payload["first_name"] = contact.first_name
        if contact.last_name is not None:
            update_payload["last_name"] = contact.last_name
        if contact.email is not None:
            update_payload["email"] = contact.email

        if not update_payload:
            raise HTTPException(status_code=400, detail="No fields to update")

        resp = (
            db_client.table("leads")
            .update(update_payload)
            .eq("id", contact_id)
            .eq("tenant_id", current_user.tenant_id)
            .execute()
        )
        updated = resp.data[0] if resp.data else {**existing.data[0], **update_payload}
        logger.info("Contact %s updated in campaign %s", contact_id, campaign_id)
        return {"message": "Contact updated successfully", "contact": updated}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating contact {contact_id} in campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update contact")


@router.get("/{campaign_id}/contacts")
async def list_campaign_contacts(
    campaign_id: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (pending, called, completed, dnc)"),
    last_call_result: Optional[str] = Query(None, description="Filter by last call result"),
    list_id: Optional[str] = Query(None, description="Filter by contact list id, or 'ungrouped' for leads with no list"),
    search: Optional[str] = Query(None, description="Case-insensitive phone_number substring match"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    List all contacts (leads) for a campaign with pagination.

    Day 9 Endpoint: GET /campaigns/{id}/contacts

    ``get_current_user`` is required so the per-request RLS tenant
    context (``app.current_tenant_id``) is set on the connection;
    without it the campaign-existence SELECT returns zero rows and the
    endpoint 404s even though the campaign is owned by the calling
    user. Same fix as ``get_campaign`` and ``get_campaign_stats``.

    Features:
    - Paginated response
    - Filter by status (pending, called, completed, dnc)
    - Filter by last_call_result (pending, answered, no_answer, busy, failed, voicemail, goal_achieved)
    - Ordered by created_at descending

    Returns:
        Paginated list of contacts with their call status
    """
    try:
        # 1. Validate campaign exists. RLS scopes to the current tenant
        # automatically now that get_current_user is in the chain.
        campaign_response = db_client.table("campaigns").select("id, tenant_id").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        # Defense-in-depth tenant check.
        row_tenant = campaign_response.data[0].get("tenant_id")
        row_tenant_str = str(row_tenant) if row_tenant is not None else None
        if current_user.tenant_id and row_tenant_str not in (None, current_user.tenant_id):
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # 2. Build query
        query = db_client.table("leads").select(
            "*", count="exact"
        ).eq("campaign_id", campaign_id).neq("status", "deleted")
        
        # Apply filters
        if status:
            query = query.eq("status", status)
        if last_call_result:
            query = query.eq("last_call_result", last_call_result)
        # Contact-list filter: a real list id → exact match; the synthetic
        # 'ungrouped' sentinel → leads with no list_id (always-active bucket).
        if list_id:
            if list_id.lower() == "ungrouped":
                query = query.is_("list_id", None)
            else:
                query = query.eq("list_id", list_id)
        # Search matches a phone_number substring (case-insensitive). Kept to a
        # single indexed column so pagination + counts stay correct.
        if search:
            query = query.ilike("phone_number", f"%{search}%")

        # 3. Apply pagination
        offset = (page - 1) * page_size
        response = query.order(
            "created_at", desc=True
        ).range(
            offset, offset + page_size - 1
        ).execute()
        
        return {
            "items": response.data or [],
            "page": page,
            "page_size": page_size,
            "total": response.count or 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing contacts for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{campaign_id}/contacts/{contact_id}", dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))])
async def remove_contact_from_campaign(
    campaign_id: str,
    contact_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
):
    """
    Remove a contact from a campaign (soft delete).

    ``get_current_user`` is required so the per-request RLS tenant
    context is set; without it the contact existence SELECT returns
    zero rows and the endpoint 404s. Same fix as the sibling list /
    add contact endpoints.

    Sets status to 'deleted' rather than actually deleting,
    preserving history for analytics.
    """
    try:
        # Verify contact belongs to campaign
        existing = db_client.table("leads").select("id").eq(
            "id", contact_id
        ).eq(
            "campaign_id", campaign_id
        ).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found in this campaign")
        
        # Soft delete
        response = db_client.table("leads").update({
            "status": "deleted"
        }).eq("id", contact_id).execute()

        # Soft-delete leaves the leads row, so the FK cascade does NOT remove
        # this lead's dialer jobs — they'd keep dialing a number you just
        # removed. Cancel its active jobs so the number truly vanishes.
        cancelled = 0
        try:
            from app.domain.services.dialer.job_lifecycle import (
                cancel_active_jobs_for_lead,
                REASON_LEAD_REMOVED,
            )
            cancelled = cancel_active_jobs_for_lead(
                db_client, contact_id, reason=REASON_LEAD_REMOVED,
            )
        except Exception as exc:
            logger.warning("remove_contact: job cancel failed for %s: %s", contact_id, exc)

        logger.info(
            f"Contact {contact_id} removed from campaign {campaign_id} "
            f"(cancelled {cancelled} active job(s))"
        )

        return {"message": "Contact removed successfully", "cancelled_jobs": cancelled}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing contact {contact_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove contact")
