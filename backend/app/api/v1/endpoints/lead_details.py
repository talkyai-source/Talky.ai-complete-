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

import csv
import io
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_user
from app.core.container import get_container
from app.core.security.rbac import Permission, require_permission
from app.domain.services.contact_fields import (
    BY_KEY,
    CONTACT_FIELDS,
    csv_template_headers,
    dedupe_key,
    map_headers,
    validate_row,
)
from app.domain.services.lead_capture_service import (
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


@router.post(
    "/contacts/import/preview",
    response_model=ImportPreview,
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def preview_import(file: UploadFile = File(...)) -> ImportPreview:
    """Parse a CSV and report what WOULD happen. Writes nothing."""
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

    issues: list[dict] = []
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

        row_issues = validate_row(mapped, row_num)
        if row_issues:
            issues.extend(
                {"row": i.row, "field": i.field, "value": i.value, "reason": i.reason}
                for i in row_issues
            )
        else:
            valid += 1

        dk = dedupe_key(mapped)
        if dk:
            if dk in seen:
                dupes.append({"row": row_num, "first_seen_row": seen[dk], "key": dk})
            else:
                seen[dk] = row_num

        if len(sample) < 5:
            sample.append(mapped)

    if not any(mapping.get(h) == "phone_number" for h in headers):
        # Not fatal here — preview is meant to TELL you, not refuse. The import
        # itself still requires a number.
        issues.insert(0, {
            "row": 0, "field": "phone_number", "value": "",
            "reason": "no column mapped to phone_number — nothing can be dialled",
        })

    return ImportPreview(
        headers=mapping, unmapped=unmapped, total_rows=total, valid_rows=valid,
        issues=issues[:200], duplicates_in_file=dupes[:200], sample=sample,
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
async def get_lead_fields(campaign_id: str, current_user=Depends(get_current_user)):
    return {"fields": await _service().fields_for_campaign(_tenant(current_user), campaign_id)}


@router.put(
    "/campaigns/{campaign_id}/lead-fields",
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def set_lead_fields(
    campaign_id: str,
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

    async with acquire_with_tenant(container.db_pool, tenant_id) as conn:
        await conn.execute(
            "DELETE FROM campaign_lead_fields WHERE campaign_id = $1::uuid",
            campaign_id,
        )
        for f in fields:
            await conn.execute(
                """
                INSERT INTO campaign_lead_fields
                    (tenant_id, campaign_id, field_key, label, field_type,
                     is_required, agent_visible, user_visible, options, sort_order)
                VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                """,
                tenant_id, campaign_id, f.field_key.strip(), f.label.strip(),
                f.field_type, f.is_required, f.agent_visible, f.user_visible,
                __import__("json").dumps(f.options) if f.options else None,
                f.sort_order,
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
    call_id: str, campaign_id: Optional[str] = None, current_user=Depends(get_current_user),
):
    """Captured values plus which required fields are still missing.

    Every value carries `source` and `confirmed` so the UI can show an
    inference differently from a stated fact — §7 is explicit that an inferred
    value is not a confirmed one.
    """
    tenant_id = _tenant(current_user)
    svc = _service()
    details = await svc.details_for_call(tenant_id, call_id)
    missing: list[str] = []
    if campaign_id:
        missing = await svc.missing_required(tenant_id, call_id, campaign_id)
    return {"details": details, "missing_required": missing}


class ManualEdit(BaseModel):
    value: Any = None
    field_type: str = "text"
    confirmed: bool = True


@router.put(
    "/calls/{call_id}/lead-details/{field_key}",
    dependencies=[Depends(require_permission(Permission.CALLS_CREATE))],
)
async def correct_lead_detail(
    call_id: str, field_key: str, body: ManualEdit,
    current_user=Depends(get_current_user),
):
    """A human correcting a captured value.

    Always written as source=manual_edit, which outranks everything, so a human
    fix is never overwritten by a later inference — that is the whole point of
    the trust ordering.
    """
    try:
        written = await _service().capture(
            tenant_id=_tenant(current_user), call_id=call_id,
            field_key=field_key, value=body.value,
            source="manual_edit", field_type=body.field_type,
            confirmed=body.confirmed,
        )
    except InvalidCaptureError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not written:
        # Should not happen: manual_edit is the top rank. If it does, something
        # is wrong with the ordering and silence would hide it.
        raise HTTPException(
            status_code=409,
            detail="The stored value could not be replaced. Reload and retry.",
        )
    return {"ok": True, "field_key": field_key}
