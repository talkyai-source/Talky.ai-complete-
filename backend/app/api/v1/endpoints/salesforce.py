"""
Salesforce connector endpoints — settings, callback webhooks, import, probe.

The OAuth connect/disconnect flow lives in ``connectors.py`` (card key
``salesforce``). This module adds what makes the connection useful:

Tenant-authenticated
    GET  /connectors/salesforce/settings          current connection + sync settings
    PUT  /connectors/salesforce/settings          callback campaign, log/create/inbound toggles
    GET  /connectors/salesforce/webhook-token     reveal the webhook token + URLs
    POST /connectors/salesforce/webhook-token     rotate the token
    POST /connectors/salesforce/test              prove the token/instance work
    POST /connectors/salesforce/import            seed a campaign from Salesforce Leads/Contacts

Public (token in the URL — the only mechanism Salesforce Outbound Messages
support; JSON callers may also send ``X-Talky-Token``)
    POST /connectors/salesforce/callback-requests/{tenant_id}/{token}
         JSON from a Flow HTTP callout / Apex / Zapier: "have the agent call
         this person". Creates or revives the lead in the configured outbound
         campaign and enqueues it immediately via the same list-dial flow the
         dashboard's "Call this list" button uses.
    POST /connectors/salesforce/outbound-message/{tenant_id}/{token}
         SOAP Outbound Message (no-code: Flow / Workflow Rule action). Same
         effect; answers with the ``<Ack>`` Salesforce expects.

Direction boundary: every campaign write goes through
``require_owned_outbound_campaign`` — an inbound campaign can never become a
callback target (409 ``inbound_campaign_managed_separately``).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints._outbound_campaign import require_owned_outbound_campaign
from app.core.postgres_adapter import Client
from app.core.security.api_security import rate_limit_dependency
from app.core.security.rbac import Permission, require_permission
from app.infrastructure.connectors.base import ConnectorProviderError
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector
from app.infrastructure.connectors.encryption import get_encryption_service
from app.services.connector_resolver import (
    ConnectorLookupError,
    ConnectorNotConnectedError,
    resolve_active_connector,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors/salesforce", tags=["Connectors"])

CALLBACK_LIST_NAME = "Salesforce callbacks"
_MAX_XML_BYTES = 256 * 1024
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
# Statuses in which a callback request must NOT auto-start dialing.
_NO_AUTOSTART_STATUSES = {"paused", "completed", "archived", "deleted"}


# =============================================================================
# Models
# =============================================================================

class SalesforceSettings(BaseModel):
    callback_campaign_id: Optional[str] = None
    log_calls: bool = True
    create_leads: bool = True
    sync_inbound: bool = True
    callback_priority: int = Field(8, ge=1, le=10)


class SalesforceSettingsUpdate(BaseModel):
    callback_campaign_id: Optional[str] = None
    clear_callback_campaign: bool = False
    log_calls: Optional[bool] = None
    create_leads: Optional[bool] = None
    sync_inbound: Optional[bool] = None
    callback_priority: Optional[int] = Field(None, ge=1, le=10)


class WebhookInfo(BaseModel):
    callback_url: Optional[str] = None
    outbound_message_url: Optional[str] = None
    token_set: bool = False
    token_masked: Optional[str] = None
    token: Optional[str] = None  # only on reveal / rotate


class SalesforceSettingsResponse(BaseModel):
    server_configured: bool
    connected: bool
    status: str
    connector_id: Optional[str] = None
    org_id: Optional[str] = None
    username: Optional[str] = None
    instance_url: Optional[str] = None
    api_version: str
    login_url: str
    settings: SalesforceSettings
    webhook: WebhookInfo
    last_synced_call_at: Optional[str] = None


class CallbackRequest(BaseModel):
    """What Salesforce (Flow / Apex / Zapier) sends to request an agent call."""
    phone: str = Field(..., min_length=3, max_length=40)
    record_id: Optional[str] = Field(None, max_length=18, description="Salesforce Lead/Contact Id")
    object_type: Optional[str] = Field(None, max_length=40)
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=2000)
    campaign_id: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=10)


class CallbackResponse(BaseModel):
    accepted: bool
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    list_id: Optional[str] = None
    duplicate: bool = False
    queued: bool = False
    jobs_enqueued: int = 0
    message: str


class ImportRequest(BaseModel):
    campaign_id: str
    object_type: str = Field("Lead", pattern="^(?:[Ll]ead|[Cc]ontact)$")
    where: Optional[str] = Field(None, max_length=500, description="Optional SOQL WHERE fragment")
    limit: int = Field(500, ge=1, le=2000)
    list_name: Optional[str] = Field(None, max_length=100)


class ImportResponse(BaseModel):
    campaign_id: str
    list_id: Optional[str]
    fetched: int
    imported: int
    revived: int
    duplicates_skipped: int
    invalid: int
    errors: List[Dict[str, Any]] = []


class ProbeResponse(BaseModel):
    ok: bool
    org_id: Optional[str] = None
    username: Optional[str] = None
    instance_url: Optional[str] = None
    api_version: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Helpers
# =============================================================================

def _coerce_config(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _settings_from_config(config: Dict[str, Any]) -> SalesforceSettings:
    try:
        return SalesforceSettings(
            callback_campaign_id=config.get("callback_campaign_id") or None,
            log_calls=config.get("log_calls", True) is not False,
            create_leads=config.get("create_leads", True) is not False,
            sync_inbound=config.get("sync_inbound", True) is not False,
            callback_priority=int(config.get("callback_priority") or 8),
        )
    except Exception:  # noqa: BLE001 — corrupt config must not 500 the page
        return SalesforceSettings()


def _salesforce_rows(db_client: Client, tenant_id: str) -> List[dict]:
    resp = (
        db_client.table("connectors")
        .select("id, status, config, created_at")
        .eq("tenant_id", tenant_id)
        .eq("type", "crm")
        .eq("provider", "salesforce")
        .order("created_at", desc=True)
        .execute()
    )
    if getattr(resp, "error", None):
        raise HTTPException(status_code=503, detail="Salesforce connector lookup is temporarily unavailable")
    return list(resp.data or [])


def _active_row(rows: List[dict]) -> Optional[dict]:
    for row in rows:
        if row.get("status") == "active":
            return row
    return None


def _require_active_row(db_client: Client, tenant_id: str) -> dict:
    row = _active_row(_salesforce_rows(db_client, tenant_id))
    if row is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "salesforce_not_connected",
                "message": "Connect Salesforce on the Connectors page first.",
            },
        )
    return row


def _update_config(db_client: Client, connector_id: str, tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = (
        db_client.table("connectors")
        .select("config")
        .eq("id", connector_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if getattr(current, "error", None) or not current.data:
        raise HTTPException(status_code=404, detail="Salesforce connector not found")
    merged = _coerce_config(current.data[0].get("config"))
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    resp = (
        db_client.table("connectors")
        .update({"config": merged, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", connector_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if getattr(resp, "error", None) or not resp.data:
        raise HTTPException(status_code=503, detail="Salesforce settings could not be saved")
    return merged


def _api_base(request: Request) -> str:
    return (os.getenv("API_BASE_URL") or str(request.base_url)).rstrip("/")


def _webhook_urls(request: Request, tenant_id: str, token: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not token:
        return None, None
    base = _api_base(request)
    return (
        f"{base}/api/v1/connectors/salesforce/callback-requests/{tenant_id}/{token}",
        f"{base}/api/v1/connectors/salesforce/outbound-message/{tenant_id}/{token}",
    )


def _mask(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return f"{token[:4]}…{token[-4:]}" if len(token) > 10 else "••••"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reveal_token(config: Dict[str, Any]) -> Optional[str]:
    encrypted = config.get("webhook_token_encrypted")
    if not encrypted:
        return None
    try:
        return get_encryption_service().decrypt(encrypted) or None
    except Exception:  # noqa: BLE001 — rotated key; the user rotates the token
        return None


def _webhook_info(request: Request, tenant_id: str, config: Dict[str, Any], *, reveal: bool) -> WebhookInfo:
    token = _reveal_token(config)
    token_set = bool(config.get("webhook_token_hash"))
    cb, om = _webhook_urls(request, tenant_id, token if reveal else None)
    return WebhookInfo(
        callback_url=cb if reveal else (f"{_api_base(request)}/api/v1/connectors/salesforce/callback-requests/{tenant_id}/{_mask(token)}" if token else None),
        outbound_message_url=om if reveal else (f"{_api_base(request)}/api/v1/connectors/salesforce/outbound-message/{tenant_id}/{_mask(token)}" if token else None),
        token_set=token_set,
        token_masked=_mask(token),
        token=token if reveal else None,
    )


def _ui_status(row: Optional[dict]) -> str:
    if row is None:
        return "disconnected"
    status = row.get("status")
    if status == "active":
        return "connected"
    if status == "expired":
        return "expired"
    if status in ("error", "revoked", "failed"):
        return "error"
    return "disconnected"


async def _last_synced_call_at(db_client: Client, tenant_id: str) -> Optional[str]:
    try:
        resp = (
            db_client.table("calls")
            .select("crm_synced_at")
            .eq("tenant_id", tenant_id)
            .order("crm_synced_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        value = rows[0].get("crm_synced_at") if rows else None
        return value if isinstance(value, str) else (value.isoformat() if value else None)
    except Exception:  # noqa: BLE001 — cosmetic
        return None


def _settings_response(
    request: Request, tenant_id: str, rows: List[dict], *, reveal: bool = False, last_synced: Optional[str] = None,
) -> SalesforceSettingsResponse:
    row = _active_row(rows) or (rows[0] if rows else None)
    config = _coerce_config(row.get("config")) if row else {}
    return SalesforceSettingsResponse(
        server_configured=SalesforceConnector.is_configured(),
        connected=bool(row and row.get("status") == "active"),
        status=_ui_status(row),
        connector_id=str(row["id"]) if row else None,
        org_id=config.get("org_id"),
        username=config.get("username"),
        instance_url=config.get("instance_url"),
        api_version=SalesforceConnector.api_version(),
        login_url=SalesforceConnector.login_url(),
        settings=_settings_from_config(config),
        webhook=_webhook_info(request, tenant_id, config, reveal=reveal) if row else WebhookInfo(),
        last_synced_call_at=last_synced,
    )


# =============================================================================
# Settings
# =============================================================================

@router.get(
    "/settings",
    response_model=SalesforceSettingsResponse,
    dependencies=[Depends(require_permission(Permission.CONNECTORS_READ))],
)
async def get_salesforce_settings(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    rows = _salesforce_rows(db_client, current_user.tenant_id)
    last = await _last_synced_call_at(db_client, current_user.tenant_id) if _active_row(rows) else None
    return _settings_response(request, current_user.tenant_id, rows, last_synced=last)


@router.put(
    "/settings",
    response_model=SalesforceSettingsResponse,
    dependencies=[Depends(require_permission(Permission.CONNECTORS_UPDATE))],
)
async def update_salesforce_settings(
    body: SalesforceSettingsUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    row = _require_active_row(db_client, current_user.tenant_id)
    patch: Dict[str, Any] = {}
    if body.clear_callback_campaign:
        patch["callback_campaign_id"] = None
    elif body.callback_campaign_id:
        # Fails 404 for foreign/missing ids and 409 for inbound campaigns —
        # an inbound campaign can never be a callback target.
        campaign = require_owned_outbound_campaign(
            db_client, body.callback_campaign_id, tenant_id=current_user.tenant_id,
        )
        patch["callback_campaign_id"] = str(campaign["id"])
    if body.log_calls is not None:
        patch["log_calls"] = bool(body.log_calls)
    if body.create_leads is not None:
        patch["create_leads"] = bool(body.create_leads)
    if body.sync_inbound is not None:
        patch["sync_inbound"] = bool(body.sync_inbound)
    if body.callback_priority is not None:
        patch["callback_priority"] = int(body.callback_priority)
    if patch:
        _update_config(db_client, str(row["id"]), current_user.tenant_id, patch)
    rows = _salesforce_rows(db_client, current_user.tenant_id)
    return _settings_response(request, current_user.tenant_id, rows)


@router.get(
    "/webhook-token",
    response_model=SalesforceSettingsResponse,
    dependencies=[Depends(require_permission(Permission.CONNECTORS_UPDATE))],
)
async def reveal_webhook_token(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Reveal the webhook token (needs connectors:update — it can create
    leads and start calls in this tenant)."""
    _require_active_row(db_client, current_user.tenant_id)
    rows = _salesforce_rows(db_client, current_user.tenant_id)
    return _settings_response(request, current_user.tenant_id, rows, reveal=True)


@router.post(
    "/webhook-token",
    response_model=SalesforceSettingsResponse,
    dependencies=[Depends(require_permission(Permission.CONNECTORS_UPDATE))],
)
async def rotate_webhook_token(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Mint a new webhook token. The old one stops working immediately —
    update the Salesforce Flow / Outbound Message endpoint URL."""
    row = _require_active_row(db_client, current_user.tenant_id)
    token = secrets.token_urlsafe(32)
    _update_config(
        db_client, str(row["id"]), current_user.tenant_id,
        {
            "webhook_token_hash": _hash_token(token),
            "webhook_token_encrypted": get_encryption_service().encrypt(token),
            "webhook_token_rotated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    rows = _salesforce_rows(db_client, current_user.tenant_id)
    return _settings_response(request, current_user.tenant_id, rows, reveal=True)


@router.post(
    "/test",
    response_model=ProbeResponse,
    dependencies=[Depends(require_permission(Permission.CONNECTORS_UPDATE))],
)
async def test_salesforce_connection(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Prove the stored token and instance URL work (identity + one query)."""
    try:
        connector, _cid, _prov = await resolve_active_connector(
            db_client, current_user.tenant_id, "crm", provider="salesforce",
        )
        probe = await connector.probe()
        return ProbeResponse(**probe)
    except ConnectorNotConnectedError as exc:
        return ProbeResponse(ok=False, error=exc.message)
    except ConnectorLookupError as exc:
        return ProbeResponse(ok=False, error=exc.message)
    except ConnectorProviderError as exc:
        return ProbeResponse(ok=False, error=f"{exc.category}: {exc}")


# =============================================================================
# Import (pull Leads/Contacts into a campaign)
# =============================================================================

@router.post(
    "/import",
    response_model=ImportResponse,
    dependencies=[
        Depends(require_permission(Permission.CONNECTORS_UPDATE)),
        Depends(require_permission(Permission.CAMPAIGNS_UPDATE)),
    ],
)
async def import_salesforce_people(
    body: ImportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Seed an outbound campaign with Salesforce Leads or Contacts that have a
    phone number. Every imported lead remembers its Salesforce Id so the
    post-call sync logs the Task against the right record."""
    from app.api.v1.endpoints.contact_lists import create_contact_list
    from app.domain.services.dialer.bulk_ingest import LeadRecord, ingest_lead_records

    campaign = require_owned_outbound_campaign(
        db_client, body.campaign_id, tenant_id=current_user.tenant_id, extra_columns=("script_config",),
    )
    try:
        connector, _cid, _prov = await resolve_active_connector(
            db_client, current_user.tenant_id, "crm", provider="salesforce",
        )
        people = await connector.list_people(body.object_type, where=body.where, limit=body.limit)
    except ConnectorNotConnectedError as exc:
        raise HTTPException(status_code=409, detail={"error": "salesforce_not_connected", "message": exc.message})
    except ConnectorLookupError as exc:
        raise HTTPException(status_code=503, detail=exc.message)
    except ConnectorProviderError as exc:
        raise HTTPException(status_code=502, detail={"error": f"salesforce_{exc.category}", "message": str(exc)})

    object_label = "Contact" if body.object_type.lower() == "contact" else "Lead"
    list_name = (body.list_name or "").strip() or f"Salesforce {object_label}s {datetime.now(timezone.utc).date().isoformat()}"
    list_id = create_contact_list(
        db_client, campaign_id=str(campaign["id"]), tenant_id=current_user.tenant_id,
        name=list_name, source="manual",
    )

    records: List[LeadRecord] = []
    for idx, person in enumerate(people, start=1):
        phone = person.get("phone") or person.get("mobile_phone")
        if not phone:
            continue
        sf_id = str(person.get("id") or "")
        records.append(LeadRecord(
            phone_raw=str(phone),
            first_name=person.get("first_name"),
            last_name=person.get("last_name"),
            email=person.get("email"),
            company=person.get("company"),
            job_title=person.get("title"),
            mobile_number=person.get("mobile_phone") if person.get("mobile_phone") != phone else None,
            custom_fields={
                "salesforce_id": sf_id,
                "salesforce_object": object_label,
                "crm_ids": {"salesforce": sf_id},
                "source": "salesforce_import",
            },
            source_row=idx,
        ))

    default_country = _campaign_default_country(campaign)
    from app.domain.services.phone_number_normalizer import normalize_phone_number

    result = ingest_lead_records(
        db_client,
        campaign_id=str(campaign["id"]),
        tenant_id=current_user.tenant_id,
        records=records,
        normalize=lambda p: normalize_phone_number(p, default_country=default_country),
        list_id=list_id,
    )
    # Stamp the Salesforce Id onto crm_contact_id for the rows that landed.
    _stamp_crm_ids(db_client, str(campaign["id"]), current_user.tenant_id, records)

    return ImportResponse(
        campaign_id=str(campaign["id"]),
        list_id=list_id,
        fetched=len(people),
        imported=result.imported,
        revived=result.revived,
        duplicates_skipped=result.duplicates_skipped,
        invalid=result.invalid,
        errors=[{"row": e.row, "error": e.error, "phone": e.phone} for e in result.errors[:50]],
    )


def _campaign_default_country(campaign: Optional[dict]) -> str:
    try:
        from app.api.v1.endpoints.campaigns import campaign_default_country
        return campaign_default_country(campaign)
    except Exception:  # noqa: BLE001
        return "US"


def _stamp_crm_ids(db_client: Client, campaign_id: str, tenant_id: str, records) -> None:
    """Best-effort: copy custom_fields.salesforce_id into leads.crm_contact_id
    so legacy readers and the sync service agree."""
    for rec in records:
        sf_id = (rec.custom_fields or {}).get("salesforce_id")
        if not sf_id:
            continue
        try:
            from app.domain.services.phone_number_normalizer import normalize_phone_number_lenient
            phone = normalize_phone_number_lenient(rec.phone_raw)
        except Exception:  # noqa: BLE001
            continue
        try:
            db_client.table("leads").update({"crm_contact_id": sf_id}).eq("campaign_id", campaign_id).eq(
                "tenant_id", tenant_id
            ).eq("phone_number", phone).execute()
        except Exception:  # noqa: BLE001 — cosmetic
            continue


# =============================================================================
# Public webhooks
# =============================================================================

def _verify_webhook(db_client: Client, tenant_id: str, token: str) -> tuple[dict, Dict[str, Any]]:
    """Return ``(connector_row, config)`` or raise 401. Sets the tenant
    context first — this is an unauthenticated route, so nothing upstream
    did, and every RLS-scoped query would otherwise see zero rows."""
    from app.core.security.tenant_isolation import set_current_tenant_id

    if not _UUID_RE.match(tenant_id or "") or not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    set_current_tenant_id(tenant_id)
    row = _active_row(_salesforce_rows(db_client, tenant_id))
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    config = _coerce_config(row.get("config"))
    stored = str(config.get("webhook_token_hash") or "")
    if not stored or not hmac.compare_digest(stored, _hash_token(token)):
        logger.warning("salesforce webhook rejected tenant=%s: bad token", tenant_id[:8])
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    return row, config


class _CallbackConflict(Exception):
    """A permanent, caller-visible reason the callback cannot be dialed."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


async def _enqueue_callback(
    db_client: Client,
    *,
    tenant_id: str,
    config: Dict[str, Any],
    payload: CallbackRequest,
) -> CallbackResponse:
    from app.api.v1.endpoints.contact_lists import create_contact_list
    from app.domain.services.dialer.bulk_ingest import LeadRecord, ingest_lead_records
    from app.domain.services.phone_number_normalizer import normalize_phone_number

    settings = _settings_from_config(config)
    campaign_id = payload.campaign_id or settings.callback_campaign_id
    if not campaign_id:
        raise _CallbackConflict(409, {
            "error": "callback_campaign_not_configured",
            "message": "Choose a callback campaign in Connectors > Salesforce before requesting callbacks.",
        })
    try:
        campaign = require_owned_outbound_campaign(
            db_client, campaign_id, tenant_id=tenant_id, extra_columns=("script_config", "status", "name"),
        )
    except HTTPException as exc:
        raise _CallbackConflict(exc.status_code, exc.detail)

    try:
        phone = normalize_phone_number(payload.phone, default_country=_campaign_default_country(campaign))
    except ValueError as exc:
        raise _CallbackConflict(422, {"error": "invalid_phone", "message": str(exc)})

    list_id = create_contact_list(
        db_client, campaign_id=str(campaign["id"]), tenant_id=tenant_id,
        name=CALLBACK_LIST_NAME, source="manual",
    )
    sf_id = (payload.record_id or "").strip() or None
    custom_fields: Dict[str, Any] = {"source": "salesforce_callback"}
    if sf_id:
        custom_fields.update({
            "salesforce_id": sf_id,
            "salesforce_object": payload.object_type or None,
            "crm_ids": {"salesforce": sf_id},
        })
    if payload.company:
        custom_fields["company"] = payload.company
    priority = payload.priority or settings.callback_priority

    existing = (
        db_client.table("leads")
        .select("id, status, do_not_call, custom_fields")
        .eq("campaign_id", str(campaign["id"]))
        .eq("tenant_id", tenant_id)
        .eq("phone_number", phone)
        .neq("status", "deleted")
        .limit(1)
        .execute()
    )
    if getattr(existing, "error", None):
        raise RuntimeError("lead lookup failed")

    duplicate = False
    lead_id: Optional[str] = None
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing.data:
        lead = existing.data[0]
        if lead.get("do_not_call"):
            raise _CallbackConflict(409, {
                "error": "do_not_call",
                "message": "This contact is flagged do-not-call in Talky.ai; the callback was not queued.",
            })
        duplicate = True
        lead_id = str(lead["id"])
        merged_custom = _coerce_config(lead.get("custom_fields"))
        merged_custom.update(custom_fields)
        update: Dict[str, Any] = {
            "custom_fields": merged_custom,
            "list_id": list_id,
            "priority": priority,
            "updated_at": now_iso,
        }
        if sf_id:
            update["crm_contact_id"] = sf_id
        if payload.notes:
            update["calling_notes"] = payload.notes[:2000]
        # A callback request means "call them (again)": a lead that already
        # finished its attempts goes back to pending so the list-dial picks it up.
        if str(lead.get("status") or "") not in ("pending", "calling"):
            update["status"] = "pending"
        resp = db_client.table("leads").update(update).eq("id", lead_id).eq("tenant_id", tenant_id).execute()
        if getattr(resp, "error", None):
            raise RuntimeError("lead update failed")
    else:
        record = LeadRecord(
            phone_raw=phone,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            company=payload.company,
            calling_notes=payload.notes[:2000] if payload.notes else None,
            custom_fields=custom_fields,
            source_row=1,
        )
        result = ingest_lead_records(
            db_client, campaign_id=str(campaign["id"]), tenant_id=tenant_id,
            records=[record], normalize=lambda p: p, list_id=list_id,
        )
        if result.invalid:
            raise _CallbackConflict(422, {"error": "invalid_phone", "message": result.errors[0].error if result.errors else "invalid phone"})
        created = (
            db_client.table("leads")
            .select("id")
            .eq("campaign_id", str(campaign["id"]))
            .eq("tenant_id", tenant_id)
            .eq("phone_number", phone)
            .neq("status", "deleted")
            .limit(1)
            .execute()
        )
        if created.data:
            lead_id = str(created.data[0]["id"])
            stamp: Dict[str, Any] = {"priority": priority}
            if sf_id:
                stamp["crm_contact_id"] = sf_id
            db_client.table("leads").update(stamp).eq("id", lead_id).eq("tenant_id", tenant_id).execute()

    status = str(campaign.get("status") or "").lower()
    if status in _NO_AUTOSTART_STATUSES:
        return CallbackResponse(
            accepted=True, lead_id=lead_id, campaign_id=str(campaign["id"]), list_id=list_id,
            duplicate=duplicate, queued=False, jobs_enqueued=0,
            message=f"Lead saved; campaign is {status}, so dialing was not started.",
        )

    from app.api.v1.endpoints.campaigns import _get_campaign_service
    from app.domain.services.campaign_service import CampaignError

    try:
        service = _get_campaign_service(db_client)
        start = await service.start_campaign(
            campaign_id=str(campaign["id"]), tenant_id=tenant_id, list_id=list_id, allow_running=True,
        )
        return CallbackResponse(
            accepted=True, lead_id=lead_id, campaign_id=str(campaign["id"]), list_id=list_id,
            duplicate=duplicate, queued=True, jobs_enqueued=int(start.jobs_enqueued or 0),
            message="Callback queued for the Talky.ai agent." if start.jobs_enqueued else
                    "Lead saved; it already has an active dial job or is outside the calling window.",
        )
    except CampaignError as exc:
        logger.warning("salesforce callback: enqueue failed campaign=%s: %s", campaign_id, exc)
        return CallbackResponse(
            accepted=True, lead_id=lead_id, campaign_id=str(campaign["id"]), list_id=list_id,
            duplicate=duplicate, queued=False, jobs_enqueued=0,
            message=f"Lead saved but dialing could not be started: {exc}",
        )


@router.post(
    "/callback-requests/{tenant_id}/{token}",
    response_model=CallbackResponse,
    dependencies=[Depends(rate_limit_dependency)],
)
async def salesforce_callback_request(
    tenant_id: str,
    token: str,
    payload: CallbackRequest,
    db_client: Client = Depends(get_db_client),
    x_talky_token: Optional[str] = Header(None, alias="X-Talky-Token"),
):
    """Salesforce (Flow HTTP callout, Apex, Zapier) asks the agent to call
    someone. Token in the path; ``X-Talky-Token`` is accepted as well."""
    _row, config = _verify_webhook(db_client, tenant_id, x_talky_token or token)
    try:
        return await _enqueue_callback(db_client, tenant_id=tenant_id, config=config, payload=payload)
    except _CallbackConflict as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# ----- SOAP Outbound Message -----------------------------------------------

_ACK_OK = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    '<soapenv:Body><notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">'
    "<Ack>true</Ack></notificationsResponse></soapenv:Body></soapenv:Envelope>"
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_outbound_message(raw: bytes) -> tuple[Optional[str], List[Dict[str, str]]]:
    """Return ``(organization_id, [sobject field dicts])`` from a Salesforce
    Outbound Message envelope. Namespace-agnostic. Refuses DTDs."""
    if len(raw) > _MAX_XML_BYTES:
        raise ValueError("payload too large")
    head = raw[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValueError("DTD not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML: {exc}") from exc
    org_id: Optional[str] = None
    records: List[Dict[str, str]] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "OrganizationId" and el.text and org_id is None:
            org_id = el.text.strip()
        elif name == "sObject":
            fields: Dict[str, str] = {}
            for child in el:
                if child.text is not None and child.text.strip():
                    fields[_local(child.tag)] = child.text.strip()
            sobject_type = el.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}type") or el.attrib.get("type")
            if sobject_type:
                fields.setdefault("__type", sobject_type.split(":")[-1])
            if fields:
                records.append(fields)
    return org_id, records


def _callback_from_sobject(fields: Dict[str, str]) -> Optional[CallbackRequest]:
    phone = fields.get("Phone") or fields.get("MobilePhone") or fields.get("Talky_Phone__c")
    if not phone:
        return None
    first = fields.get("FirstName")
    last = fields.get("LastName")
    if not (first or last) and fields.get("Name"):
        parts = fields["Name"].split(" ", 1)
        first, last = parts[0], (parts[1] if len(parts) > 1 else None)
    return CallbackRequest(
        phone=phone[:40],
        record_id=(fields.get("Id") or "")[:18] or None,
        object_type=fields.get("__type"),
        first_name=(first or None) and first[:100],
        last_name=(last or None) and last[:100],
        email=(fields.get("Email") or None) and fields["Email"][:255],
        company=(fields.get("Company") or None) and fields["Company"][:255],
        notes=(fields.get("Talky_Notes__c") or fields.get("Description") or None) and (fields.get("Talky_Notes__c") or fields.get("Description"))[:2000],
        campaign_id=fields.get("Talky_Campaign_Id__c") or None,
    )


@router.post(
    "/outbound-message/{tenant_id}/{token}",
    dependencies=[Depends(rate_limit_dependency)],
)
async def salesforce_outbound_message(
    tenant_id: str,
    token: str,
    request: Request,
    db_client: Client = Depends(get_db_client),
):
    """No-code path: a Salesforce Outbound Message (Flow / Workflow action)
    pointed at this URL. Each notified Lead/Contact becomes a callback."""
    _row, config = _verify_webhook(db_client, tenant_id, token)
    raw = await request.body()
    try:
        org_id, records = parse_outbound_message(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    expected_org = str(config.get("org_id") or "")
    if expected_org and org_id and expected_org[:15] != org_id[:15]:
        logger.warning("salesforce outbound message org mismatch tenant=%s", tenant_id[:8])
        raise HTTPException(status_code=403, detail="Outbound message is from a different Salesforce org")

    transient_failure = False
    for fields in records:
        payload = _callback_from_sobject(fields)
        if payload is None:
            continue
        try:
            outcome = await _enqueue_callback(db_client, tenant_id=tenant_id, config=config, payload=payload)
            logger.info(
                "salesforce outbound message tenant=%s record=%s queued=%s jobs=%d",
                tenant_id[:8], payload.record_id, outcome.queued, outcome.jobs_enqueued,
            )
        except _CallbackConflict as exc:
            # Permanent for this record (DNC, inbound campaign, bad phone):
            # ack so Salesforce does not retry for 24h.
            logger.info("salesforce outbound message tenant=%s record=%s rejected: %s", tenant_id[:8], payload.record_id, exc.detail)
        except Exception as exc:  # noqa: BLE001 — transient: let Salesforce retry
            logger.warning("salesforce outbound message tenant=%s record=%s failed: %r", tenant_id[:8], payload.record_id, exc)
            transient_failure = True

    if transient_failure:
        raise HTTPException(status_code=503, detail="Temporary failure; Salesforce will retry")
    return Response(content=_ACK_OK, media_type="text/xml")

