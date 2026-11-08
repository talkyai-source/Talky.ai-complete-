"""Contact-field definitions and captured lead details (goals.md §11 + §7).

    GET   /contacts/fields                      the canonical model + aliases
    POST  /contacts/import/preview              map headers, validate, find dupes
    GET   /campaigns/{id}/lead-fields           what this campaign captures
    PUT   /campaigns/{id}/lead-fields           define what it captures
    GET   /calls/{id}/lead-details              what was captured, with source
    PUT   /calls/{id}/lead-details/{field_key}  correct one value by hand

WHY THE IMPORT PREVIEW IS A SEPARATE CALL
------------------------------------------
§11 asks for column mapping, row-level validation failures and a merge/skip
decision on duplicates. All three are things a person needs to SEE before
committing 4,000 rows to a live dialler. So preview is its own endpoint that
writes nothing: upload, look at what we understood, fix the mapping, then
import. An importer that decides all that silently is how a campaign ends up
dialling a column of postcodes.
"""
from __future__ import annotations
from app.core.error_handlers import safe_exception_message
from app.api.v1.validation import ResourceUUID

import csv
import io
import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_user, get_db_client
from app.core.container import get_container
from app.core.security.rbac import Permission, require_permission
from app.domain.services.contact_fields import (
    CONTACT_FIELDS,
    csv_template_headers,
    dedupe_key,
    map_headers,
    validate_row,
)
from app.domain.services.lead_capture_service import (
    IDENTITY_FIELDS,
    InvalidCaptureError,
    LeadCaptureService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lead details"])

MAX_PREVIEW_ROWS = 5000


def _tenant(user) -> str:
    tid = getattr(user, "tenant_id", None)
    if not tid:
        raise HTTPException(status_code=400, detail="User is not associated with a tenant")
    return str(tid)


def _service() -> LeadCaptureService:
    c = get_container()
    if not c.is_initialized:
        raise HTTPException(status_code=503, detail="Backend not ready")
    return LeadCaptureService(c.db_pool)


# ── §11: the canonical model ────────────────────────────────────────────────

class FieldSpec(BaseModel):
    key: str
    label: str
    field_type: str
    aliases: list[str]
    agent_usable: bool
    max_len: int


@router.get(
    "/contacts/fields",
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def contact_field_spec() -> dict:
    """The single source of truth, so the form, the table and the import UI
    never hold their own copy of the field list and drift from it."""
    return {
        "fields": [
            FieldSpec(
                key=f.key, label=f.label, field_type=f.field_type,
                aliases=list(f.aliases), agent_usable=f.agent_usable,
                max_len=f.max_len,
            ).model_dump()
            for f in CONTACT_FIELDS
        ],
        "csv_template_headers": csv_template_headers(),
    }


class ImportPreview(BaseModel):
    headers: dict[str, Optional[str]]
    unmapped: list[str]
    total_rows: int
    valid_rows: int
    issues: list[dict]
    duplicates_in_file: list[dict]
    sample: list[dict]
    # D2-8: the country national-format numbers were read with (None = only
    # "+" numbers are accepted), and per previewed row the numbers as the
    # import stores them; phone_number is None when the row cannot be dialled.
    default_country: Optional[str] = None
    normalized_rows: list[dict] = Field(default_factory=list)


_PHONE_KEYS = ("phone_number", "mobile_number", "business_number")


@router.post(
    "/contacts/import/preview",
    response_model=ImportPreview,
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def preview_import(
    file: UploadFile = File(...),
    default_country: Annotated[Optional[str], Query(min_length=2, max_length=2)] = None,
    campaign_id: Annotated[Optional[str], Query(description="Read numbers with this campaign's country, as its import does")] = None,
    current_user=Depends(get_current_user),
    db_client=Depends(get_db_client),
) -> ImportPreview:
    """Parse a CSV and report what WOULD happen. Writes nothing.

    Numbers are judged by the import's own rule and country (D2-8): request,
    then campaign, then account; never a US guess.
    """
    from app.api.v1.endpoints._outbound_campaign import require_owned_outbound_campaign
    from app.api.v1.endpoints.contacts import _check_optional_numbers, _resolve_country
    from app.domain.services.phone_number_normalizer import normalize_phone_number, phone_number_error

    campaign = None
    if campaign_id:
        campaign = require_owned_outbound_campaign(
            db_client, campaign_id, tenant_id=current_user.tenant_id, extra_columns=("script_config",))
    country = _resolve_country(db_client, current_user.tenant_id, campaign, default_country)

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows still emits cp1252 constantly. Failing the upload
        # over an encoding the user cannot see is not helpful.
        text = raw.decode("cp1252", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    if not headers:
        raise HTTPException(status_code=400, detail="That file has no header row.")

    mapping = map_headers(headers)
    unmapped = [h for h, k in mapping.items() if k is None]
    # As on import, a Mobile column is the dial number when there is no Phone.
    dial_mapped = any(k in ("phone_number", "mobile_number") for k in mapping.values())

    issues: list[dict] = []
    normalized_rows: list[dict] = []
    seen: dict[str, int] = {}
    dupes: list[dict] = []
    sample: list[dict] = []
    total = valid = 0

    for row_num, row in enumerate(reader, start=2):
        if total >= MAX_PREVIEW_ROWS:
            break
        total += 1
        mapped: dict[str, str] = {}
        for header, value in row.items():
            key = mapping.get(header)
            if key and value and str(value).strip():
                mapped[key] = str(value).strip()

        # Phone fields are decided by the strict rule below, not the loose
        # digit count in validate_row.
        row_issues = [
            {"row": i.row, "field": i.field, "value": i.value, "reason": i.reason}
            for i in validate_row(mapped, row_num) if i.field not in _PHONE_KEYS
        ]
        numbers = {k: mapped[k] for k in _PHONE_KEYS if k in mapped}
        row_issues.extend(
            {"row": e.row, "field": e.field, "value": e.value, "reason": e.error}
            for e in _check_optional_numbers(numbers, row_num, country)
        )
        dial_field = "phone_number" if numbers.get("phone_number") else "mobile_number"
        dial_raw = numbers.get(dial_field)
        dial = None
        if dial_raw:
            try:
                dial = normalize_phone_number(dial_raw, default_country=country)
            except ValueError:
                row_issues.append({"row": row_num, "field": dial_field, "value": dial_raw[:40],
                                   "reason": phone_number_error(dial_raw, country)})
        elif dial_mapped:
            row_issues.append({"row": row_num, "field": "phone_number", "value": "",
                               "reason": "no phone number, so this row cannot be dialled"})
        normalized_rows.append({"row": row_num, "phone_number": dial, **{
            k: numbers[k] for k in ("mobile_number", "business_number")
            if k in numbers and k != dial_field}})
        issues.extend(row_issues)
        if dial and not row_issues:
            valid += 1

        dk = dedupe_key(mapped)
        if dk:
            if dk in seen:
                dupes.append({"row": row_num, "first_seen_row": seen[dk], "key": dk})
            else:
                seen[dk] = row_num

        if len(sample) < 5:
            sample.append(mapped)

    if not dial_mapped:
        # Not fatal here — preview is meant to TELL you, not refuse. The import
        # itself still requires a number.
        issues.insert(0, {
            "row": 0, "field": "phone_number", "value": "",
            "reason": "no column mapped to phone_number — nothing can be dialled",
        })

    return ImportPreview(
        headers=mapping, unmapped=unmapped, total_rows=total, valid_rows=valid,
        issues=issues[:200], duplicates_in_file=dupes[:200], sample=sample,
        default_country=country, normalized_rows=normalized_rows,
    )


# ── §7: campaign field definitions ──────────────────────────────────────────

class LeadFieldIn(BaseModel):
    field_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    field_type: str = "text"
    is_required: bool = False
    agent_visible: bool = True
    user_visible: bool = True
    options: Optional[list[str]] = None
    sort_order: int = 0


@router.get(
    "/campaigns/{campaign_id}/lead-fields",
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_lead_fields(campaign_id: ResourceUUID, current_user=Depends(get_current_user)):
    return {"fields": await _service().fields_for_campaign(_tenant(current_user), campaign_id)}


@router.put(
    "/campaigns/{campaign_id}/lead-fields",
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def set_lead_fields(
    campaign_id: ResourceUUID,
    fields: list[LeadFieldIn],
    current_user=Depends(get_current_user),
):
    """Replace the campaign's field definitions.

    Replace rather than merge: the UI edits the whole list, and a partial
    update would leave a field the user deleted still being asked for on live
    calls. Existing CAPTURED values are untouched — deleting a definition stops
    us collecting it, it does not erase what was already collected.
    """
    tenant_id = _tenant(current_user)
    container = get_container()
    if not container.is_initialized:
        raise HTTPException(status_code=503, detail="Backend not ready")

    from app.core.db_utils import acquire_with_tenant
    from app.domain.services.campaign_brief import normalize_required_lead_fields

    normalized_fields: list[tuple[LeadFieldIn, dict[str, str]]] = []
    try:
        for field in fields:
            normalized = normalize_required_lead_fields(
                [{"field_key": field.field_key, "label": field.label}]
            )[0]
            normalized_fields.append((field, normalized))
        required_refs = normalize_required_lead_fields(
            [
                normalized
                for field, normalized in normalized_fields
                if field.is_required and field.agent_visible
            ]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=safe_exception_message(exc, 'The request could not be completed.')) from exc
    async with acquire_with_tenant(container.db_pool, tenant_id) as conn:
        campaign = await conn.fetchrow(
            """
            SELECT system_prompt, script_config
              FROM campaigns
             WHERE id = $1::uuid
               AND tenant_id = $2::uuid
            """,
            campaign_id,
            tenant_id,
        )
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        from app.domain.services.campaign_prompt_service import (
            guidance_budget_error_message,
            guidance_budget_violation,
        )

        script_config = campaign["script_config"] or {}
        if isinstance(script_config, str):
            try:
                script_config = __import__("json").loads(script_config)
            except ValueError:
                script_config = {}
        brief = dict(script_config.get("campaign_brief") or {})
        brief["required_lead_fields"] = required_refs
        violation = guidance_budget_violation(
            script_config.get("additional_instructions")
            or campaign["system_prompt"]
            or "",
            brief,
        )
        if violation is not None:
            raise HTTPException(
                status_code=400,
                detail=guidance_budget_error_message(*violation),
            )

        await conn.execute(
            "DELETE FROM campaign_lead_fields WHERE campaign_id = $1::uuid",
            campaign_id,
        )
        for f, normalized in normalized_fields:
            await conn.execute(
                """
                INSERT INTO campaign_lead_fields
                    (tenant_id, campaign_id, field_key, label, field_type,
                     is_required, agent_visible, user_visible, options, sort_order)
                VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                """,
                tenant_id, campaign_id, normalized["field_key"], normalized["label"],
                f.field_type, f.is_required, f.agent_visible, f.user_visible,
                __import__("json").dumps(f.options) if f.options else None,
                f.sort_order,
            )
        # Keep the prompt-facing campaign brief in the same transaction as the
        # authoritative field policy.  The frontend must not maintain a second
        # list that can drift after somebody edits the contact-field panel.
        await conn.execute(
            """
            UPDATE campaigns
               SET script_config = jsonb_set(
                    COALESCE(script_config, '{}'::jsonb),
                    '{campaign_brief}',
                    COALESCE(script_config->'campaign_brief', '{}'::jsonb)
                    || jsonb_build_object('required_lead_fields', $3::jsonb),
                    true
               )
             WHERE id = $1::uuid
               AND tenant_id = $2::uuid
            """,
            campaign_id,
            tenant_id,
            __import__("json").dumps(required_refs),
        )
    logger.info(
        "campaign_lead_fields_updated campaign=%s count=%d required=%d",
        str(campaign_id)[:8], len(fields), sum(1 for f in fields if f.is_required),
    )
    return {"fields": await _service().fields_for_campaign(tenant_id, campaign_id)}


# ── §7: captured values ─────────────────────────────────────────────────────

@router.get(
    "/calls/{call_id}/lead-details",
    dependencies=[Depends(require_permission(Permission.CALLS_READ))],
)
async def get_lead_details(
    call_id: ResourceUUID, campaign_id: Optional[str] = None, current_user=Depends(get_current_user),
):
    """Captured values plus which required fields are still missing.

    Every value carries `source` and `confirmed` so the UI can show an
    inference differently from a stated fact — §7 is explicit that an inferred
    value is not a confirmed one.
    """
    tenant_id = _tenant(current_user)
    svc = _service()
    details = await svc.details_for_call(tenant_id, call_id)
    # Campaign ownership comes from this call, not optional browser context.
    # Retain the old query argument for existing clients without trusting it.
    missing = await svc.missing_required(tenant_id, call_id)
    return {"details": details, "missing_required": missing,
            "processing_status": await svc.processing_status(tenant_id, call_id=call_id),
            "transcript_save_state": await svc.transcript_save_state(tenant_id, call_id=call_id),
            "crm_deliveries": await svc.crm_deliveries(tenant_id, call_id=call_id)}


@router.get("/contacts/{lead_id}/lead-details", dependencies=[Depends(require_permission(Permission.CALLS_READ))])
async def get_contact_lead_details(lead_id: ResourceUUID, current_user=Depends(get_current_user)):
    tenant_id = _tenant(current_user)
    svc = _service()
    return {"details": await svc.details_for_lead(tenant_id, lead_id), "missing_required": [],
            "processing_status": await svc.processing_status(tenant_id, lead_id=lead_id),
            "transcript_save_state": await svc.transcript_save_state(tenant_id, lead_id=lead_id),
            "crm_deliveries": await svc.crm_deliveries(tenant_id, lead_id=lead_id)}


class ManualEdit(BaseModel):
    value: Any = None
    field_type: str = "text"
    confirmed: bool = True


@router.put(
    "/calls/{call_id}/lead-details/{field_key}",
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def correct_lead_detail(
    call_id: ResourceUUID, field_key: str, body: ManualEdit,
    current_user=Depends(get_current_user),
):
    """A human correcting a captured value.

    Always written as source=manual_edit, which outranks everything, so a human
    fix is never overwritten by a later inference — that is the whole point of
    the trust ordering.
    """
    try:
        withdrawn = body.value is None and (body.field_type in {"email", "phone"}
            or (field_key in IDENTITY_FIELDS and body.field_type == "text"))
        written = await _service().capture(
            tenant_id=_tenant(current_user), call_id=call_id,
            field_key=field_key, value=body.value,
            source="manual_edit", field_type=body.field_type,
            confirmed=False if withdrawn else body.confirmed,
            validation_status="cancelled" if withdrawn else None,
        )
    except InvalidCaptureError as exc:
        raise HTTPException(status_code=422, detail=safe_exception_message(exc, 'The request could not be completed.')) from exc
    if not written:
        # Should not happen: manual_edit is the top rank. If it does, something
        # is wrong with the ordering and silence would hide it.
        raise HTTPException(
            status_code=409,
            detail="The stored value could not be replaced. Reload and retry.",
        )
    return {"ok": True, "field_key": field_key}
