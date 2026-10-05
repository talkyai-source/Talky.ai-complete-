"""
Admin Actions Endpoints
Assistant action log: list, detail, retry, cancel
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Response, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Literal, Optional
import asyncio
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4
from app.core.postgres_adapter import Client
from app.core.db_utils import acquire_with_tenant
from app.core.security.rbac import UserRole, normalize_role
from app.services.voice_callback_service import callback_job_id
from app.services.action_execution import public_action_receipt
from app.services.saved_acknowledgement import gmail_acknowledgement
from app.core.security.principal import PrincipalUnavailable, load_session_principal
from app.services.connector_resolver import resolve_active_connector, verify_reviewed_authorization
from app.infrastructure.connectors.base import ConnectorProviderError

from app.api.v1.dependencies import get_db_client, require_admin, require_platform_admin, CurrentUser
from ._serialization import AdminResponseModel

router = APIRouter()


# These historical audit types have no generic replay consumer. A new pending
# row is not queue admission, and an uncertain effect must never be replayed.
RETRYABLE_ACTION_TYPES = frozenset()


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _admin_tenant(user):
    """Broad Admin roles do not grant platform-wide receipt access."""
    if normalize_role(getattr(user, "role", None)) == UserRole.PLATFORM_ADMIN:
        return None
    tenant = getattr(user, "tenant_id", None)
    if not tenant:
        raise HTTPException(status_code=403, detail="Tenant context required for action receipts")
    try:
        return str(UUID(str(tenant)))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Invalid tenant context") from exc


_EMAIL_PROOF_FIELDS = ("identity_version", "tenant_id", "connector_id", "provider", "account_row_id", "external_account_id")


def _gmail_inspection_bundle(row):
    return _saved_inspection_bundle(row, action_types=("send_email",), providers=("gmail",),
                                    reference_key="message_id", reference_pattern=r"[A-Za-z0-9_-]{1,256}")


def _calendar_inspection_bundle(row):
    return _saved_inspection_bundle(row, action_types=("book_meeting", "update_meeting", "cancel_meeting"),
                                    providers=("google_calendar", "outlook_calendar"),
                                    reference_key="external_event_id", reference_pattern=r"(?!\.{1,2}$)[!-~]{1,512}")


def _gmail_recovery_candidate(row):
    return gmail_acknowledgement(row, _gmail_inspection_bundle(row))


async def _recovery_owned(conn, row):
    if row.get("triggered_by") != "voice":
        return True
    # Verify only the already-recorded call relationship. This is not child
    # discovery, current campaign approval, or proof of the email's content.
    return await conn.fetchval("""
        SELECT EXISTS(SELECT 1 FROM calls c JOIN campaigns p
          ON p.id=c.campaign_id AND p.tenant_id=c.tenant_id
          WHERE c.id=$1::uuid AND c.tenant_id=$2::uuid AND c.campaign_id=$3::uuid
            AND ($4::uuid IS NULL OR c.lead_id=$4::uuid))
    """, str(row["call_id"]), str(row["tenant_id"]), str(row["campaign_id"]),
        str(row["lead_id"]) if row.get("lead_id") else None)


async def _recorded_recovery(conn, actor_id, action_id, body):
    previous = await conn.fetchrow(
        "SELECT * FROM assistant_action_resolutions WHERE actor_id=$1::uuid AND request_id=$2::uuid",
        actor_id, str(body.request_id),
    )
    if previous and (str(previous["action_id"]) != action_id
            or previous["source_digest"] != body.expected_source_digest or previous["reason"] != body.reason):
        raise HTTPException(status_code=409, detail="Recovery request ID belongs to different review details")
    return previous


def _recovery_summary(row):
    return {key: str(row[key]) for key in (
        "id", "action_id", "actor_id", "actor_role", "request_id", "source_digest", "reason",
        "original_status", "recovered_status", "provider_status", "recorded_at")}


def _saved_recovery_id(value):
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


class RecoverAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_source_digest: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    reason: str = Field(min_length=1, max_length=500, strict=True)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Provide a bounded review reason without control characters")
        return value


def _saved_inspection_bundle(row, *, action_types, providers, reference_key, reference_pattern):
    """One co-persisted proof/reference, never a flattened or inferred receipt."""
    if row.get("type") not in action_types:
        return None
    output = _object(row.get("output_data"))
    nested = output.get("provider_result")
    if "provider_result" in output and not isinstance(nested, dict):
        return None
    sources = [output] + ([nested] if isinstance(nested, dict) else [])
    if any(source.get("action_id") is not None and str(source["action_id"]) != str(row.get("id")) for source in sources):
        return None
    evidence = [source for source in sources if any(key in source for key in (*_EMAIL_PROOF_FIELDS, reference_key))]
    if not evidence:
        return None
    bundles = []
    for source in evidence:
        # A partial contradictory wrapper is not silently ignored or stitched
        # to a nested result. Bulk receipts require their own supported contract.
        if "receipts" in source or source.get("identity_version") != "authorization_row_v1" or source.get("provider") not in providers:
            return None
        try:
            proof = {key: source[key] for key in _EMAIL_PROOF_FIELDS if key != "external_account_id"}
            for key in ("tenant_id", "connector_id", "account_row_id"):
                if not isinstance(proof[key], str) or str(UUID(proof[key])) != proof[key]:
                    return None
            if proof["tenant_id"] != str(row.get("tenant_id")):
                return None
        except (KeyError, TypeError, ValueError):
            return None
        external = source.get("external_account_id")
        if external is not None and (not isinstance(external, str) or not external.strip() or len(external) > 512):
            return None
        if external is not None:
            proof["external_account_id"] = external
        message_id = source.get(reference_key)
        if not isinstance(message_id, str) or re.fullmatch(reference_pattern, message_id) is None:
            return None
        if row.get("connector_id") is not None and str(row["connector_id"]) != proof["connector_id"]:
            return None
        bundles.append((proof, message_id))
    if any(bundle != bundles[0] for bundle in bundles[1:]):
        return None
    proof, message_id = bundles[0]
    intent = _object(row.get("input_data"))
    # Canonical inner EmailService records bind this proof before dispatch.
    # Outer proposal parameters never supply missing provider-result proof.
    reviewed_proofs = []
    if "reviewed_connector" in intent:
        reviewed_proofs.append(intent["reviewed_connector"])
    parameters = intent.get("parameters")
    if isinstance(parameters, dict) and "_reviewed_connector" in parameters:
        reviewed_proofs.append(parameters["_reviewed_connector"])
    for reviewed in reviewed_proofs:
        if not isinstance(reviewed, dict) or any(reviewed.get(key) != proof.get(key) for key in _EMAIL_PROOF_FIELDS):
            return None
    bulk_keys = ("receipts", "message_ids") + (("event_ids", "external_event_ids") if reference_key == "external_event_id" else ())
    if any(any(key in source for key in bulk_keys) for source in sources):
        return None
    return proof, message_id


class EmailInspection(AdminResponseModel):
    action_id: str
    outcome: Literal["observed_message", "not_observed", "unavailable"]
    reason: Literal["exact_message_observed_only", "absence_is_inconclusive", "saved_proof_unavailable",
                    "original_authorization_unavailable", "provider_read_unavailable"]
    observed_at: str
    observed_message_id: Optional[str] = None


class CalendarInspection(AdminResponseModel):
    action_id: str
    outcome: Literal["observed_event", "not_observed", "unavailable"]
    reason: Literal["exact_event_observed_only", "absence_is_inconclusive", "saved_proof_unavailable",
                    "original_authorization_unavailable", "provider_read_unavailable"]
    observed_at: str
    observed_event_id: Optional[str] = None


def _is_callback(row):
    return row.get("type") == "schedule_callback" and row.get("triggered_by") == "voice"


def _cancellation_owned(row):
    """Only an existing claim/outbox can prove that cancellation stops work."""
    if row.get("status") == "pending":
        # Legacy email/SMS pending rows are audits, not execution claims: a
        # provider request may already be in progress while they remain pending.
        return bool(row.get("idempotency_key") and _object(row.get("input_data")).get("request_hash"))
    return (row.get("status") == "scheduled" and _is_callback(row)
            and not _object(row.get("output_data")).get("dialer_job_id"))


async def _callback_reserved(conn, row):
    if not _is_callback(row):
        return False
    return bool(await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM dialer_jobs WHERE tenant_id=$1::uuid AND id=$2::uuid)",
        str(row["tenant_id"]), callback_job_id(row["id"]),
    ))


# =============================================================================
# Response Models
# =============================================================================

class ActionItem(AdminResponseModel):
    """Action list item for table display"""
    id: str
    tenant_id: str
    tenant_name: str
    type: str
    status: str
    outcome_status: Optional[str] = None
    triggered_by: Optional[str] = None
    lead_name: Optional[str] = None
    lead_phone: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None


class ActionListResponse(BaseModel):
    """Paginated action list response"""
    items: List[ActionItem]
    total: int
    page: int
    page_size: int


class ActionDetail(AdminResponseModel):
    """Full action detail with payload"""
    id: str
    tenant_id: str
    tenant_name: str
    type: str
    status: str
    outcome_status: Optional[str] = None
    triggered_by: Optional[str] = None
    
    # Related entities
    conversation_id: Optional[str] = None
    call_id: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = None
    lead_phone: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    connector_id: Optional[str] = None
    connector_name: Optional[str] = None
    
    # Payload
    input_data: Optional[dict] = None
    output_data: Optional[dict] = None
    saved_receipt: Optional[dict] = None
    email_inspection_available: bool = False
    calendar_inspection_available: bool = False
    acknowledgement_recovery: Optional[dict] = None
    acknowledgement_recovery_record: Optional[dict] = None
    error: Optional[str] = None
    
    # Audit
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    
    # Timing
    scheduled_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: str
    
    # Flags
    is_retryable: bool = False
    is_cancellable: bool = False


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/actions", response_model=ActionListResponse)
async def get_admin_actions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    type: Optional[str] = Query(None, description="Filter by action type"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
    from_date: Optional[str] = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, alias="to", description="End date (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search by lead phone"),
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    List all assistant actions with pagination and filters.
    Platform admins may view all tenants; tenant and partner admins stay scoped.
    """
    try:
        scope = _admin_tenant(admin_user)
        if scope and tenant_id and str(tenant_id) != scope:
            raise HTTPException(status_code=403, detail="Cannot access another tenant's action receipts")
        offset = (page - 1) * page_size
        
        # Build query with joins
        query = db_client.table("assistant_actions").select(
            "*, tenants!inner(business_name), leads(first_name, last_name, phone_number)",
            count="exact"
        ).order("created_at", desc=True)
        
        # Apply filters
        if status:
            query = query.eq("status", status)
        if type:
            query = query.eq("type", type)
        if scope or tenant_id:
            query = query.eq("tenant_id", scope or tenant_id)
        if from_date:
            query = query.gte("created_at", f"{from_date}T00:00:00")
        if to_date:
            query = query.lte("created_at", f"{to_date}T23:59:59")
        if search:
            # Search by lead phone - need to filter on lead relation
            query = query.ilike("leads.phone_number", f"%{search}%")
        
        # Pagination
        query = query.range(offset, offset + page_size - 1)
        
        response = query.execute()
        
        items = []
        for action in response.data or []:
            tenant = action.get("tenants", {})
            lead = action.get("leads") or {}
            
            lead_name = None
            if lead.get("first_name") or lead.get("last_name"):
                lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            
            items.append(ActionItem(
                id=action["id"],
                tenant_id=action["tenant_id"],
                tenant_name=tenant.get("business_name", "Unknown"),
                type=action["type"],
                status=action["status"],
                outcome_status=action.get("outcome_status"),
                triggered_by=action.get("triggered_by"),
                lead_name=lead_name,
                lead_phone=lead.get("phone_number"),
                error=action.get("error"),
                created_at=action["created_at"],
                started_at=action.get("started_at"),
                completed_at=action.get("completed_at"),
                duration_ms=action.get("duration_ms")
            ))
        
        return ActionListResponse(
            items=items,
            total=response.count or 0,
            page=page,
            page_size=page_size
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch actions: {str(e)}"
        )


@router.get("/actions/{action_id}", response_model=ActionDetail)
async def get_admin_action_detail(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get full action detail including input/output payloads.
    """
    try:
        scope = _admin_tenant(admin_user)
        # Fetch action with all relations
        query = db_client.table("assistant_actions").select(
            "*, tenants!inner(business_name), leads(first_name, last_name, phone_number), "
            "campaigns(name), connectors(name)"
        ).eq("id", action_id)
        if scope:
            query = query.eq("tenant_id", scope)
        response = query.single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        action = response.data
        tenant = action.get("tenants", {})
        lead = action.get("leads") or {}
        campaign = action.get("campaigns") or {}
        connector = action.get("connectors") or {}
        
        lead_name = None
        if lead.get("first_name") or lead.get("last_name"):
            lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
        
        # Determine if action is retryable/cancellable
        is_retryable = (
            action["status"] == "failed" and 
            action["type"] in RETRYABLE_ACTION_TYPES
        )
        is_cancellable = _cancellation_owned(action)
        if is_cancellable and _is_callback(action):
            async with acquire_with_tenant(db_client.pool, str(action["tenant_id"])) as conn:
                is_cancellable = not await _callback_reserved(conn, action)

        candidate, recovered = None, None
        if normalize_role(admin_user.role) == UserRole.PLATFORM_ADMIN:
            candidate = _gmail_recovery_candidate(action)
            recovery_id = _saved_recovery_id(_object(action.get("output_data")).get("receipt_recovery_id"))
            if recovery_id or (candidate and action.get("triggered_by") == "voice"):
                async with acquire_with_tenant(db_client.pool, None, user_id=str(admin_user.id)) as conn:
                    if candidate and not await _recovery_owned(conn, action):
                        candidate = None
                    if recovery_id:
                        history = await conn.fetchrow(
                            "SELECT * FROM assistant_action_resolutions WHERE id=$1::uuid "
                            "AND action_id=$2::uuid AND tenant_id=$3::uuid",
                            recovery_id, str(action["id"]), str(action["tenant_id"]),
                        )
                        if history:
                            recovered = _recovery_summary(history)
        
        return ActionDetail(
            id=action["id"],
            tenant_id=action["tenant_id"],
            tenant_name=tenant.get("business_name", "Unknown"),
            type=action["type"],
            status=action["status"],
            outcome_status=action.get("outcome_status"),
            triggered_by=action.get("triggered_by"),
            conversation_id=action.get("conversation_id"),
            call_id=action.get("call_id"),
            lead_id=action.get("lead_id"),
            lead_name=lead_name,
            lead_phone=lead.get("phone_number"),
            campaign_id=action.get("campaign_id"),
            campaign_name=campaign.get("name"),
            connector_id=action.get("connector_id"),
            connector_name=connector.get("name"),
            input_data=action.get("input_data"),
            output_data=action.get("output_data"),
            saved_receipt=public_action_receipt(action),
            email_inspection_available=(normalize_role(admin_user.role) == UserRole.PLATFORM_ADMIN
                                        and _gmail_inspection_bundle(action) is not None),
            calendar_inspection_available=(normalize_role(admin_user.role) == UserRole.PLATFORM_ADMIN
                                           and _calendar_inspection_bundle(action) is not None),
            acknowledgement_recovery=({key: candidate[key] for key in ("source_digest", "provider_status")}
                                      if candidate else None),
            acknowledgement_recovery_record=recovered,
            error=action.get("error"),
            ip_address=str(action["ip_address"]) if action.get("ip_address") else None,
            user_agent=action.get("user_agent"),
            request_id=action.get("request_id"),
            idempotency_key=action.get("idempotency_key"),
            scheduled_at=action.get("scheduled_at"),
            started_at=action.get("started_at"),
            completed_at=action.get("completed_at"),
            duration_ms=action.get("duration_ms"),
            created_at=action["created_at"],
            is_retryable=is_retryable,
            is_cancellable=is_cancellable
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch action detail: {str(e)}"
        )


@router.get("/actions/{action_id}/email-inspection", response_model=EmailInspection)
async def inspect_admin_email_action(
    action_id: UUID,
    response: Response,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Observe the saved Gmail message in its original authorization; no effects."""
    response.headers["Cache-Control"] = "no-store"
    selected_id = str(action_id)

    def observation(outcome, reason, message_id=None):
        return EmailInspection(action_id=selected_id, outcome=outcome, reason=reason,
                               observed_at=datetime.now(timezone.utc).isoformat(), observed_message_id=message_id)

    try:
        saved = db_client.table("assistant_actions").select(
            "id,tenant_id,type,connector_id,input_data,output_data").eq("id", selected_id).single().execute()
        if getattr(saved, "error", None):
            raise RuntimeError("Saved action lookup unavailable")
        if not saved.data:
            raise HTTPException(status_code=404, detail="Action not found")
        if str(saved.data.get("id")) != selected_id:
            raise RuntimeError("Saved action identity mismatch")
        bundle = _gmail_inspection_bundle(saved.data)
        if bundle is None:
            return observation("unavailable", "saved_proof_unavailable")
        proof, message_id = bundle
    except HTTPException:
        raise
    except Exception:
        return observation("unavailable", "saved_proof_unavailable")

    try:
        async with asyncio.timeout(10):
            connector, connector_id, provider = await resolve_active_connector(
                db_client, proof["tenant_id"], "email", read_only=True,
                provider=proof["provider"], connector_id=proof["connector_id"], account_id=proof["account_row_id"],
                external_account_id=proof.get("external_account_id"),
            )
            verify_reviewed_authorization(connector, connector_id, provider, proof)
    except Exception:
        return observation("unavailable", "original_authorization_unavailable")

    try:
        async with asyncio.timeout(10):
            message = await connector.get_email(message_id)
        if getattr(message, "id", None) != message_id:
            return observation("unavailable", "provider_read_unavailable")
        return observation("observed_message", "exact_message_observed_only", message_id)
    except ConnectorProviderError as exc:
        if exc.provider == "gmail" and exc.operation == "get_email" and exc.status_code == 404:
            return observation("not_observed", "absence_is_inconclusive")
        return observation("unavailable", "provider_read_unavailable")
    except Exception:
        return observation("unavailable", "provider_read_unavailable")


@router.get("/actions/{action_id}/calendar-inspection", response_model=CalendarInspection)
async def inspect_admin_calendar_action(
    action_id: UUID,
    response: Response,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Observe the saved event in its original authorization without effects."""
    response.headers["Cache-Control"] = "no-store"
    selected_id = str(action_id)

    def observation(outcome, reason, event_id=None):
        return CalendarInspection(action_id=selected_id, outcome=outcome, reason=reason,
                                  observed_at=datetime.now(timezone.utc).isoformat(), observed_event_id=event_id)

    try:
        saved = db_client.table("assistant_actions").select(
            "id,tenant_id,type,connector_id,input_data,output_data").eq("id", selected_id).single().execute()
        if getattr(saved, "error", None):
            raise RuntimeError("Saved action lookup unavailable")
        if not saved.data:
            raise HTTPException(status_code=404, detail="Action not found")
        if str(saved.data.get("id")) != selected_id:
            raise RuntimeError("Saved action identity mismatch")
        bundle = _calendar_inspection_bundle(saved.data)
        if bundle is None:
            return observation("unavailable", "saved_proof_unavailable")
        proof, event_id = bundle
    except HTTPException:
        raise
    except Exception:
        return observation("unavailable", "saved_proof_unavailable")

    try:
        async with asyncio.timeout(10):
            connector, connector_id, provider = await resolve_active_connector(
                db_client, proof["tenant_id"], "calendar", read_only=True,
                provider=proof["provider"], connector_id=proof["connector_id"], account_id=proof["account_row_id"],
                external_account_id=proof.get("external_account_id"),
            )
            verify_reviewed_authorization(connector, connector_id, provider, proof)
    except Exception:
        return observation("unavailable", "original_authorization_unavailable")

    try:
        async with asyncio.timeout(10):
            observed_id = await connector.get_event_reference(event_id)
        if observed_id != event_id:
            return observation("unavailable", "provider_read_unavailable")
        return observation("observed_event", "exact_event_observed_only", event_id)
    except ConnectorProviderError as exc:
        if exc.provider == provider and exc.operation == "get_event_reference" and exc.status_code in (404, 410):
            return observation("not_observed", "absence_is_inconclusive")
        return observation("unavailable", "provider_read_unavailable")
    except Exception:
        return observation("unavailable", "provider_read_unavailable")


@router.post("/actions/{action_id}/recover-acknowledgement")
async def recover_saved_acknowledgement(
    action_id: str,
    body: RecoverAcknowledgement,
    request: Request,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Recover an explicit historical Gmail acknowledgement without execution."""
    try:
        action_id = str(UUID(action_id))
        actor_id = str(UUID(str(admin_user.id)))
        session_id = getattr(request.state, "authenticated_session_id", None)
        if getattr(request.state, "authenticated_user_id", None) != actor_id or not session_id:
            raise HTTPException(status_code=401, detail="Current authenticated session required")
        async with asyncio.timeout(5):
            async with acquire_with_tenant(db_client.pool, None, user_id=actor_id,
                                           request_id=str(body.request_id)) as conn:
                principal = await load_session_principal(conn, {
                    "sub": actor_id, "sid": session_id, "tenant_id": admin_user.tenant_id,
                })
                if principal["role"] != "platform_admin":
                    raise HTTPException(status_code=403, detail="Current platform admin authority required")
                previous = await _recorded_recovery(conn, actor_id, action_id, body)
                if previous:
                    return _recovery_summary(previous)
                row = await conn.fetchrow("SELECT * FROM assistant_actions WHERE id=$1::uuid FOR UPDATE", action_id)
                if row is None:
                    raise HTTPException(status_code=404, detail="Action not found")
                # Row-lock waits must not extend a revoked browser session.
                principal = await load_session_principal(conn, {
                    "sub": actor_id, "sid": session_id, "tenant_id": admin_user.tenant_id,
                })
                if principal["role"] != "platform_admin":
                    raise HTTPException(status_code=403, detail="Current platform admin authority required")
                # A simultaneous identical request may have committed while we
                # waited for the action lock. It must replay the same event.
                previous = await _recorded_recovery(conn, actor_id, action_id, body)
                if previous:
                    return _recovery_summary(previous)
                candidate = _gmail_recovery_candidate(dict(row))
                if (candidate is None or candidate["source_digest"] != body.expected_source_digest
                        or not await _recovery_owned(conn, row)):
                    raise HTTPException(status_code=409, detail="Saved acknowledgement is unavailable or changed; reload the receipt")
                recovery_id = str(uuid4())
                event = await conn.fetchrow("""
                    INSERT INTO assistant_action_resolutions
                        (id,tenant_id,action_id,actor_id,actor_role,request_id,source_digest,reason,
                         original_status,original_output,original_timestamps,recovered_status,provider_status)
                    VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,'platform_admin',$5::uuid,$6,$7,
                            'unknown',$8::jsonb,$9::jsonb,'completed',$10)
                    ON CONFLICT (actor_id,request_id) DO NOTHING RETURNING *
                """, recovery_id, str(row["tenant_id"]), action_id, actor_id, str(body.request_id),
                    body.expected_source_digest, body.reason, json.dumps(candidate["original_output"]),
                    json.dumps(candidate["original_timestamps"]), candidate["provider_status"])
                if event is None:
                    raise HTTPException(status_code=409, detail="Recovery request already recorded; reload the receipt")
                restored = {**candidate["result"], "receipt_recovery_id": recovery_id}
                changed = await conn.fetchval("""
                    UPDATE assistant_actions SET status='completed',output_data=$3::jsonb
                    WHERE id=$1::uuid AND tenant_id=$2::uuid AND status='unknown' RETURNING id
                """, action_id, str(row["tenant_id"]), json.dumps(restored))
                if changed is None:
                    raise RuntimeError("Saved acknowledgement update was not committed")
                return _recovery_summary(event)
    except HTTPException:
        raise
    except PrincipalUnavailable as exc:
        raise HTTPException(status_code=401, detail="Current account or session is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid recovery identity") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Recovery is unconfirmed. Reload the saved receipt before another request.") from exc


@router.post("/actions/{action_id}/retry")
async def retry_action(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """Retain the receipt; no generic worker supports replaying audit rows."""
    try:
        scope = _admin_tenant(admin_user)
        # Fetch original action
        query = db_client.table("assistant_actions").select("*").eq("id", action_id)
        if scope:
            query = query.eq("tenant_id", scope)
        response = query.single().execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Action not found")
        
        original = response.data
        
        uncertain = original["status"] in {"pending", "scheduled", "running", "unknown"}
        raise HTTPException(status_code=501, detail={
            "code": "action_retry_unavailable", "action_id": str(original["id"]),
            "status": original["status"], "retryable": False,
            "message": "No action was queued. This receipt has no supported automatic replay route.",
            "next_step": ("Keep this action held. Review the original account and provider receipt; do not resend an uncertain effect."
                          if uncertain else
                          "Review the original receipt first. Only after non-execution is established may an authorized user create a fresh preview and confirm it through the existing assistant."),
        })
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retry action: {str(e)}"
        )


@router.post("/actions/{action_id}/cancel")
async def cancel_action(
    action_id: str,
    admin_user: CurrentUser = Depends(require_admin),
    db_client: Client = Depends(get_db_client)
):
    """Cancel only before the owned executor/outbox has reserved dispatch."""
    try:
        scope = _admin_tenant(admin_user)
        action_id = str(UUID(action_id))
        # Only platform admins use the existing bypass; all other accepted
        # Admin roles stay tenant scoped. This lock is shared with claim and
        # callback preparation; updates also pin the row's authoritative tenant.
        async with asyncio.timeout(5):
            async with acquire_with_tenant(db_client.pool, scope) as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM assistant_actions WHERE id=$1::uuid "
                    "AND ($2::uuid IS NULL OR tenant_id=$2::uuid) FOR UPDATE", action_id, scope,
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="Action not found")
                if not _cancellation_owned(row) or await _callback_reserved(conn, row):
                    raise HTTPException(status_code=409, detail={
                        "code": "action_not_cancellable", "action_id": action_id,
                        "status": row["status"], "retryable": False,
                        "message": "Cancellation is not confirmed: execution is already reserved or this audit row does not own a cancellable action. Review the saved receipt; do not resend.",
                    })
                result = await conn.fetchval(
                    """UPDATE assistant_actions SET status='cancelled',outcome_status='cancelled_by_admin',
                        completed_at=NOW(),output_data=COALESCE(output_data,'{}'::jsonb) || $4::jsonb
                        WHERE id=$1::uuid AND tenant_id=$2::uuid AND status=$3 RETURNING id""",
                    action_id, str(row["tenant_id"]), row["status"], json.dumps({
                        "success": False, "status": "cancelled", "confirmation_allowed": False,
                        "message": "Cancelled before dispatch was reserved.",
                        "cancelled_by_user_id": str(admin_user.id),
                    }),
                )
                if result is None:
                    raise RuntimeError("Cancellation was not persisted")
        return {"detail": "Cancelled before dispatch was reserved", "action_id": action_id,
                "previous_status": row["status"], "new_status": "cancelled"}
    
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid action ID") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Cancellation could not be confirmed. Reload the saved receipt before another action.") from exc
