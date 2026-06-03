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
from app.domain.services.phone_number_normalizer import normalize_phone_number
from app.domain.services.event_emitter import emit_event
from app.api.v1.dependencies import (
    get_db_client,
    get_db_read_client,
    get_current_user,
    CurrentUser,
)
from app.api.v1.schemas.campaigns import (
    CampaignCreateRequest,
    CampaignPromptPreviewRequest,
    CampaignPromptPreviewResponse,
    CampaignStartRequest,
    CampaignUpdateRequest,
    ContactCreate,
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


@router.post("/", dependencies=[Depends(rate_limit_dependency)])
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

        from app.api.v1.endpoints.ai_options import (
            _english_google_voices,
            _fetch_tenant_config,
            _get_deepgram_voices_for_current_key,
            get_elevenlabs_voices_for_current_key,
        )
        from app.domain.models.ai_config import AIProviderConfig

        selected_voice_id = campaign_data.voice_id.strip()

        async with db_client.pool.acquire() as conn:
            ai_config = await _fetch_tenant_config(conn, current_user.tenant_id)
        if ai_config is None:
            ai_config = AIProviderConfig()

        if ai_config.tts_provider == "google":
            valid_voice_ids = {voice.id for voice in _english_google_voices()}
        elif ai_config.tts_provider == "deepgram":
            valid_voice_ids = {
                voice.id for voice in await _get_deepgram_voices_for_current_key()
            }
        else:
            valid_voice_ids = {
                voice.id for voice in await get_elevenlabs_voices_for_current_key()
            }

        if selected_voice_id not in valid_voice_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Voice '{selected_voice_id}' is not available for the current "
                    f"global TTS provider '{ai_config.tts_provider}'. Update AI Options "
                    "or choose a matching campaign voice."
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

        insert_payload = {
            "tenant_id": current_user.tenant_id,
            "name": campaign_data.name.strip(),
            "description": campaign_data.description.strip() if campaign_data.description else None,
            "system_prompt": campaign_data.system_prompt.strip(),
            "voice_id": selected_voice_id,
            "goal": campaign_data.goal.strip() if campaign_data.goal else None,
            "script_config": script_config,
        }

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


@router.put("/{campaign_id}", dependencies=[Depends(rate_limit_dependency)])
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

        script_config = _build_validated_script_config(
            persona_type=campaign_data.persona_type,
            company_name=campaign_data.company_name,
            agent_names=campaign_data.agent_names,
            campaign_slots=campaign_data.campaign_slots,
            additional_instructions=campaign_data.system_prompt,
            knowledge_driven=campaign_data.knowledge_driven,
        )

        update_payload = {
            "name": campaign_data.name.strip(),
            "description": campaign_data.description.strip() if campaign_data.description else None,
            "system_prompt": campaign_data.system_prompt.strip(),
            "voice_id": campaign_data.voice_id.strip(),
            "goal": campaign_data.goal.strip() if campaign_data.goal else None,
            "script_config": script_config,
        }

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


@router.post("/{campaign_id}/start", dependencies=[Depends(rate_limit_dependency)])
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
    except Exception as e:
        if idempotency_key:
            await release_idempotency_lock(request)
        logger.error(f"Unexpected error starting campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{campaign_id}/pause")
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


@router.post("/{campaign_id}/stop")
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
        
        # Get call outcomes
        calls_response = db_client.table("calls").select("outcome, goal_achieved").eq("campaign_id", campaign_id).execute()
        
        outcome_counts = {}
        goals_achieved = 0
        for call in calls_response.data or []:
            outcome = call.get("outcome", "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            if call.get("goal_achieved"):
                goals_achieved += 1
        
        return {
            "campaign_id": campaign_id,
            "campaign_status": campaign.get("status"),
            "total_leads": campaign.get("total_leads", 0),
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

@router.post("/{campaign_id}/contacts")
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

        try:
            normalized_phone = normalize_phone_number(
                contact.phone_number, default_country=default_country,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")
        
        # 3. Check for duplicate within campaign
        existing = db_client.table("leads").select("id").eq(
            "campaign_id", campaign_id
        ).eq(
            "phone_number", normalized_phone
        ).neq(
            "status", "deleted"
        ).execute()
        
        if existing.data:
            raise HTTPException(
                status_code=409, 
                detail=f"Phone number {normalized_phone} already exists in this campaign"
            )
        
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


@router.get("/{campaign_id}/contacts")
async def list_campaign_contacts(
    campaign_id: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (pending, called, completed, dnc)"),
    last_call_result: Optional[str] = Query(None, description="Filter by last call result"),
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


@router.delete("/{campaign_id}/contacts/{contact_id}")
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
        
        logger.info(f"Contact {contact_id} removed from campaign {campaign_id}")
        
        return {"message": "Contact removed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing contact {contact_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove contact")
