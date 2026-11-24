"""
Contacts Endpoints
Handles bulk contact/lead import via CSV

Day 9 Enhancements:
- POST /campaigns/{campaign_id}/contacts/upload - Campaign-scoped CSV upload
- Enhanced phone validation
- Duplicate detection within campaign
- Batch insertion for performance
- Detailed error reporting per row
"""
from app.core.error_handlers import safe_exception_message
from app.api.v1.validation import ResourceUUID
import csv
import io
import logging
import uuid
from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from fastapi.responses import Response
from pydantic import BaseModel
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.api.v1.endpoints._outbound_campaign import (
    outbound_campaign_conflict,
    require_owned_outbound_campaign,
)
from app.domain.services.campaign_direction_guard import (
    OutboundCampaignDirectionConflict,
    raise_for_outbound_direction_guard,
)
from app.core.security.rbac import Permission, require_permission
from app.domain.services.dialer.bulk_ingest import (
    IMPORT_FIELD_ERRORS_CAP,
    ROW_ERROR_TEXT,
    cap_errors,
    log_import_row_errors,
    phone_failure_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ImportError(BaseModel):
    """One thing that went wrong, tied to the row and the column it came from.

    goals.md §11 asks for row-level validation failures. "Invalid phone number"
    on its own is not actionable in a 40-column spreadsheet, so ``field`` names
    the canonical field the problem is about (``phone_number``, ``timezone``, …)
    and stays None for whole-row problems that belong to no single column.
    """
    row: Optional[int] = None
    error: str
    phone: Optional[str] = None
    field: Optional[str] = None
    value: Optional[str] = None
    # Stable snake_case identifier of the kind of problem (``phone_missing``,
    # ``phone_invalid``, ``email_invalid`` ...). ``error`` is always one plain
    # sentence; the website may map ``code`` to its own text.
    code: Optional[str] = None


class BulkImportResponse(BaseModel):
    """Bulk import response.

    ``list_id`` / ``list_name`` / ``list_contact_count`` describe the contact
    list this import created (or reused). They are optional so any older
    client that ignores them keeps working; they're None only if list creation
    was skipped/failed and the leads were imported as Ungrouped.
    """
    total_rows: int
    imported: int
    failed: int
    duplicates_skipped: int = 0
    # Contacts already in the campaign with no name that this file gave one to.
    names_added: int = 0
    errors: List[ImportError]
    # Cells we could not use on rows that DID import. Separate from ``errors``
    # so ``failed`` keeps meaning "contacts that did not land": a contact whose
    # timezone was junk is still a contact, and losing it over one bad cell
    # would be worse than the bad cell. Every entry names the row and the
    # field, and the value was not written.
    field_errors: List[ImportError] = []
    # ``errors`` is capped (IMPORT_ERRORS_CAP). ``errors_total`` is how many row
    # errors there were in all (it equals ``failed``) and ``errors_truncated``
    # is true when ``errors`` is shorter than that. Same pair for field_errors.
    errors_total: int = 0
    errors_truncated: bool = False
    field_errors_total: int = 0
    field_errors_truncated: bool = False
    list_id: Optional[str] = None
    list_name: Optional[str] = None
    list_contact_count: Optional[int] = None


class BulkPasteRequest(BaseModel):
    """Pasted-text bulk import payload — a blob of phone numbers, one per
    line or separated by commas/semicolons. Names are not parsed from a
    paste (use CSV for that); every token is treated as a phone number."""
    text: str
    default_country: Optional[str] = None
    # The customer's confirmation that they may call these contacts:
    # {basis, source, permission_date, statement_version}. Validated in the route.
    permission: Optional[Any] = None


def _names_from_values(values: dict) -> tuple[Optional[str], Optional[str]]:
    """first/last name of a mapped CSV row. A single name column (Name, Full
    name, Customer ...) is split by the product's one rule, the same one the
    manual Add-Contact form uses."""
    first_name = values.get("first_name")
    last_name = values.get("last_name")
    if not first_name and not last_name and values.get("full_name"):
        from app.api.v1.endpoints.campaigns import _split_contact_full_name
        return _split_contact_full_name(values["full_name"])
    return first_name, last_name


def _mapped_row(row: dict, header_mapping: dict) -> dict:
    """A CSV row keyed by canonical field (first non-empty cell wins)."""
    values: dict = {}
    for header, value in row.items():
        key = header_mapping.get(header) if header is not None else None
        if key and isinstance(value, str) and value.strip():
            values.setdefault(key, value.strip())
    return values


def _campaign_default_country(campaign) -> str:
    from app.domain.services.phone_number_normalizer import campaign_country
    return campaign_country(campaign)


def _normalize_for_user(phone: str, user, default_country: str = "US") -> str:
    """All accounts use the same valid-only dial destination contract."""
    from app.domain.services.phone_number_normalizer import normalize_phone_number
    return normalize_phone_number(phone, default_country=default_country)


def _phone_failure(raw, country):
    """(code, plain sentence) for a dial number the normaliser refused."""
    code = phone_failure_code(raw or "", country_known=bool(country))
    return code, ROW_ERROR_TEXT[code]


def _preflight_phones(records, default_country):
    from app.domain.services.phone_number_normalizer import normalize_phone_number
    errors = []
    for record in records:
        try:
            normalize_phone_number(record.phone_raw, default_country)
        except ValueError:
            code, message = _phone_failure(record.phone_raw, default_country)
            errors.append({"row": record.source_row, "field": "phone_number",
                           "code": code, "message": message})
    if errors:
        shown, total, truncated = cap_errors(errors)
        log_import_row_errors("Contact", None, [
            (e["row"], e["code"], "phone refused before import", None) for e in errors
        ])
        if truncated:
            shown.append({
                "row": None, "field": None, "code": "more_errors",
                "message": (
                    f"{total - len(shown)} more rows have phone numbers that are not valid "
                    "and are not listed. Fix the rows shown and upload again."
                ),
            })
        raise HTTPException(status_code=422, detail=shown)


def _check_optional_numbers(values: dict, row_num: int, country) -> list["ImportError"]:
    """CSV: mobile_number / business_number follow the dial number's rule (D2-9).

    A valid one is stored in E.164; a bad one is dropped from the row and
    reported, and the contact still imports (the policy for every bad cell).
    A mobile column that IS the dial number (no phone_number column) is left
    to the stricter dial-number check, which rejects the import.
    """
    from app.domain.services.phone_number_normalizer import normalize_phone_number

    errors = []
    for field in ("mobile_number", "business_number"):
        raw = (values.get(field) or "").strip()
        if not raw or (field == "mobile_number" and not values.get("phone_number")):
            continue
        try:
            values[field] = normalize_phone_number(raw, default_country=country)
        except ValueError:
            values.pop(field, None)
            label = "mobile" if field == "mobile_number" else "business"
            code, _ = _phone_failure(raw, country)
            if code == "phone_country_required":
                sentence = (
                    f"The {label} number has no country code, so it was left out. "
                    "Add the country code (for example +44) or choose a country for the import."
                )
            else:
                sentence = (
                    f"The {label} number is not valid for the country chosen, so it was left out. "
                    "Use the international format, for example +442071234567."
                )
                code = f"{field}_invalid"
            errors.append(ImportError(row=row_num, field=field, value=raw[:40],
                                      error=sentence, code=code))
    return errors


def _resolve_country(db_client, tenant_id, campaign=None, requested=None):
    from app.domain.services.phone_number_normalizer import resolve_default_country
    try:
        return resolve_default_country(db_client, tenant_id, campaign, requested)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=[{
            "row": 1, "field": "default_country", "code": "country_invalid",
            "message": "Choose a country from the list.",
        }]) from exc


# =============================================================================
# The import template (goals.md §11 "Update CSV import template")
# =============================================================================

_FIELD_ISSUE_TEXT = {
    "email": ("email_invalid", "The email address is not valid, so it was left out."),
    "timezone": (
        "timezone_invalid",
        "The time zone is not recognised, so it was left out. Use a name such as Europe/London.",
    ),
    "preferred_contact_method": (
        "contact_method_invalid",
        "The preferred contact method must be phone, email, sms or whatsapp, so it was left out.",
    ),
    "mobile_number": (
        "mobile_number_invalid",
        "The mobile number is not valid for the country chosen, so it was left out. "
        "Use the international format, for example +442071234567.",
    ),
    "business_number": (
        "business_number_invalid",
        "The business number is not valid for the country chosen, so it was left out. "
        "Use the international format, for example +442071234567.",
    ),
    "do_not_call": (
        "do_not_call_invalid",
        "The do-not-call answer must be yes or no, so it was left out.",
    ),
}


def _field_issue(issue, label: str) -> ImportError:
    """One rejected cell as a plain sentence with a code."""
    if str(issue.reason).startswith("longer than"):
        code = "value_too_long"
        sentence = f"The {label.lower()} is too long, so it was left out."
    else:
        code, sentence = _FIELD_ISSUE_TEXT.get(issue.field, (
            "value_invalid", f"The {label.lower()} is not valid, so it was left out."))
    return ImportError(row=issue.row, field=issue.field, value=issue.value,
                       error=sentence, code=code)


def _ingest_error_field(err) -> Optional[str]:
    """Which column a shared-ingest error belongs to.

    ``bulk_ingest`` is shared with the paste importer and carries no field, but
    everything it reports per row is about the dial number. The exceptions are
    the batch database failures, which belong to no column at all.
    """
    if getattr(err, "code", "") in ("save_failed", "update_failed"):
        return None
    return "phone_number"


@router.get("/import-template")
async def download_import_template(
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Download a CSV a customer can fill in and upload back.

    Generated from the canonical field registry, header row plus one example
    row, so it can never list a column the importer does not accept. A
    hand-written template drifts the moment a field is added, and the symptom
    is a customer filling in a column nothing reads.
    """
    from app.domain.services.contact_fields import TEMPLATE_FILENAME, csv_template_csv

    return Response(
        content=csv_template_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"',
            # The accepted columns change when the registry changes; a cached
            # copy of last month's template is exactly the drift we are
            # removing.
            "Cache-Control": "no-store",
        },
    )


# =============================================================================
# Day 9: Campaign-Scoped CSV Upload Endpoint
# =============================================================================

@router.post(
    "/campaigns/{campaign_id}/upload",
    response_model=BulkImportResponse,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def upload_campaign_contacts(
    campaign_id: ResourceUUID,
    file: UploadFile = File(..., description="CSV file with contacts"),
    skip_duplicates: bool = Query(True, description="Skip duplicate phone numbers within campaign"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    default_country: Annotated[Optional[str], Query(min_length=2, max_length=2)] = None,
    permission_basis: Annotated[Optional[str], Form()] = None,
    permission_source: Annotated[Optional[str], Form()] = None,
    permission_date: Annotated[Optional[str], Form()] = None,
    permission_statement_version: Annotated[Optional[str], Form()] = None,
):
    """
    Bulk import contacts from CSV to a specific campaign.
    
    Day 9 Endpoint: POST /campaigns/{campaign_id}/contacts/upload
    
    Features:
    - Campaign-scoped import (all contacts go to specified campaign)
    - Robust phone number validation and normalization
    - Duplicate detection within the campaign
    - Batch insertion for better performance
    - Detailed error reporting with row numbers
    
    CSV headings are mapped through the canonical contact-field aliases, so
    the sample sheet as well as common headings such as Mobile, Business Name,
    Calling Hours and Call Notes are accepted. Unknown columns are preserved
    in custom_fields and never break the import.

    Returns:
        ImportResult with counts and per-row errors
    """
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are supported"
        )

    # The customer's permission confirmation is checked before the file is read.
    from app.api.v1.endpoints.contact_lists import (
        permission_from_form, resolve_contact_permission,
    )
    permission = resolve_contact_permission(permission_from_form(
        permission_basis, permission_source, permission_date,
        permission_statement_version,
    ))

    try:
        # 1. Validate ownership and outbound direction before reading the file
        # or creating a contact list/lead.
        campaign = require_owned_outbound_campaign(
            db_client,
            campaign_id,
            tenant_id=current_user.tenant_id,
            extra_columns=("name", "script_config"),
        )
        campaign_name = campaign.get("name", "Unknown")
        campaign_tenant_id = campaign.get("tenant_id")
        # Same country the manual Add-Contact path uses, so a CSV-imported and
        # a hand-typed national-format number normalise identically.
        default_country = _resolve_country(db_client, current_user.tenant_id, campaign, default_country)

        # 2. Read and decode file
        content = await file.read()
        text_content = None
        
        # utf-8-sig first: Excel's "CSV UTF-8" writes a BOM, which plain utf-8
        # keeps as U+FEFF on the first header (it also decodes BOM-less UTF-8).
        for encoding in ['utf-8-sig', 'latin-1', 'cp1252']:
            try:
                text_content = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if text_content is None:
            raise HTTPException(
                status_code=400,
                detail="Unable to decode CSV file. Please use UTF-8 encoding."
            )
        
        # 3. Parse CSV
        csv_reader = csv.DictReader(io.StringIO(text_content))
        
        # Map real-world headings to the ONE canonical field definition used by
        # the preview endpoint and the agent-context builder. A Mobile column
        # can be the dial number when no separate Phone number is supplied.
        from app.domain.services.contact_fields import (
            BY_KEY, SEMICOLON_FILE_MESSAGE, coerce_bool,
            looks_semicolon_separated, map_headers, validate_row,
        )

        fieldnames = list(csv_reader.fieldnames or [])
        if looks_semicolon_separated(fieldnames):
            raise HTTPException(status_code=400, detail=SEMICOLON_FILE_MESSAGE)
        header_mapping = map_headers(fieldnames)
        if not any(
            mapped in {"phone_number", "mobile_number"}
            for mapped in header_mapping.values()
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "CSV must include a phone or mobile-number column. "
                    f"Found: {', '.join(fieldnames)}"
                ),
            )
        
        # 4. Parse CSV rows into LeadRecords (CSV-specific field mapping).
        #    Dedup / revive / chunk-insert is handled by the shared
        #    bulk-ingest core so CSV and pasted-text imports behave
        #    identically.
        from app.domain.services.dialer.bulk_ingest import (
            LeadRecord, ingest_lead_records,
        )
        records: List[LeadRecord] = []
        field_errors: List[ImportError] = []
        ragged_rows: List[ImportError] = []
        for row_num, row in enumerate(csv_reader, start=2):  # Row 1 is header
            if row.get(None):
                # More cells than headers (usually an unquoted comma). DictReader
                # files the overflow under the None key as a list; report the row
                # and carry on rather than aborting the whole import.
                ragged_rows.append(ImportError(
                    row=row_num, error=ROW_ERROR_TEXT["row_too_many_cells"],
                    code="row_too_many_cells",
                ))
                continue
            values: dict[str, str] = {}
            custom_fields: dict = {}
            for header, value in row.items():
                if header is None or not isinstance(value, str):
                    continue
                value_clean = value.strip() if value else ""
                if not value_clean:
                    continue
                mapped = header_mapping.get(header)
                if mapped:
                    # If a file repeats an equivalent heading, the first
                    # non-empty value wins rather than changing per row.
                    values.setdefault(mapped, value_clean)
                else:
                    custom_fields[header] = value_clean

            # §11: row-level validation on the path that actually writes.
            # The preview endpoint is optional and a user can upload straight
            # past it, so without this an invalid timezone, a malformed email
            # or a "maybe" in the do-not-call column reached the database with
            # nobody told. A rejected cell is dropped and reported; the CONTACT
            # still imports, because losing a person over one bad cell is worse
            # than the bad cell.
            for issue in validate_row(values, row_num):
                if issue.field == "phone_number":
                    # The dial number has a stricter authority downstream (the
                    # shared normalizer), which already reports the row. Do not
                    # count one bad number twice.
                    continue
                values.pop(issue.field, None)
                field_errors.append(_field_issue(issue, BY_KEY[issue.field].label))
            field_errors.extend(_check_optional_numbers(values, row_num, default_country))

            first_name, last_name = _names_from_values(values)

            phone_raw = values.get("phone_number") or values.get("mobile_number")
            company = values.get("company_name")
            records.append(LeadRecord(
                phone_raw=phone_raw or "",
                first_name=first_name,
                last_name=last_name,
                email=values.get("email"),
                company=company,
                mobile_number=values.get("mobile_number"),
                business_number=values.get("business_number"),
                company_name=company,
                job_title=values.get("job_title"),
                best_time_to_call=values.get("best_time_to_call"),
                timezone=values.get("timezone"),
                calling_notes=values.get("calling_notes"),
                preferred_contact_method=values.get("preferred_contact_method"),
                do_not_call=(
                    coerce_bool(values["do_not_call"])
                    if "do_not_call" in values else None
                ),
                custom_fields=custom_fields,
                source_row=row_num,
            ))

        _preflight_phones(records, default_country)

        # 5. Create (or reuse) the contact list for this upload — named after
        #    the uploaded file. Ordinary list failures fall back to Ungrouped;
        #    a direction-trigger rejection aborts with the stable 409 contract.
        from app.api.v1.endpoints.contact_lists import (
            create_contact_list, _live_count, list_display_name,
        )
        list_id = create_contact_list(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            name=file.filename,
            source="csv",
            permission=permission,
            confirmed_by=current_user.id,
        )

        # 6. Normalize, dedup, revive, chunk-insert via the shared core,
        #    tagging every inserted/revived lead with the list.
        result = ingest_lead_records(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            records=records,
            normalize=lambda p: _normalize_for_user(p, current_user, default_country),
            list_id=list_id,
        )

        logger.info(
            f"CSV upload completed for campaign '{campaign_name}' (list={list_id}): "
            f"{result.imported} imported ({result.revived} revived), "
            f"{result.duplicates_skipped} duplicates skipped, {len(result.errors)} errors"
        )

        list_count = None
        if list_id is not None:
            try:
                list_count = _live_count(
                    db_client,
                    campaign_id,
                    list_id,
                    tenant_id=campaign_tenant_id or current_user.tenant_id,
                )
            except Exception:  # noqa: BLE001
                list_count = None

        all_errors = ragged_rows + [
            ImportError(row=e.row, error=e.error, phone=e.phone,
                        field=_ingest_error_field(e), code=e.code)
            for e in result.errors
        ]
        shown, errors_total, errors_truncated = cap_errors(all_errors)
        shown_fields, fields_total, fields_truncated = cap_errors(
            field_errors, IMPORT_FIELD_ERRORS_CAP)
        log_import_row_errors("CSV", campaign_id, [
            (r.row, "row_too_many_cells", "row has extra cells", None) for r in ragged_rows
        ] + [(e.row, e.code, e.detail, e.phone) for e in result.errors])
        return BulkImportResponse(
            total_rows=result.total + len(ragged_rows),
            imported=result.imported,
            failed=len(result.errors) + len(ragged_rows),
            duplicates_skipped=result.duplicates_skipped,
            names_added=result.names_added,
            errors=shown,
            errors_total=errors_total,
            errors_truncated=errors_truncated,
            field_errors=shown_fields,
            field_errors_total=fields_total,
            field_errors_truncated=fields_truncated,
            list_id=list_id,
            list_name=(
                list_display_name(
                    db_client, list_id, campaign_tenant_id or current_user.tenant_id,
                    file.filename,
                ) if list_id is not None else None
            ),
            list_contact_count=list_count,
        )
    
    except OutboundCampaignDirectionConflict as exc:
        raise outbound_campaign_conflict(
            campaign_id,
            message=(
                "The campaign changed to inbound before the contact import completed. "
                "Use the inbound campaign lifecycle."
            ),
        ) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CSV upload failed for campaign {campaign_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to import contacts.')
        )


# =============================================================================
# Phase 3a: Paste-a-blob bulk import
# =============================================================================

@router.post(
    "/campaigns/{campaign_id}/paste",
    response_model=BulkImportResponse,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def paste_campaign_contacts(
    campaign_id: ResourceUUID,
    body: BulkPasteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Bulk import contacts by pasting a list of numbers.

    Accepts a free-form blob (one number per line, or comma/semicolon
    separated), extracts the numbers, then runs the same normalize →
    dedup → revive → chunk-insert pipeline as the CSV upload. Built for
    the "I have 100+ numbers to drop in" case without making a CSV.
    """
    from app.domain.services.dialer.bulk_ingest import (
        LeadRecord, ingest_lead_records, parse_pasted_numbers,
    )
    from app.api.v1.endpoints.contact_lists import resolve_contact_permission

    # The customer's permission confirmation is checked before anything is parsed.
    permission = resolve_contact_permission(body.permission)

    # 1. Validate ownership and outbound direction before list/lead writes.
    campaign = require_owned_outbound_campaign(
        db_client,
        campaign_id,
        tenant_id=current_user.tenant_id,
        extra_columns=("name", "script_config"),
    )
    campaign_tenant_id = campaign.get("tenant_id")
    default_country = _resolve_country(db_client, current_user.tenant_id, campaign, body.default_country)

    # 2. Parse the blob into raw tokens.
    tokens = parse_pasted_numbers(body.text)
    if not tokens:
        raise HTTPException(status_code=400, detail="No phone numbers found in the pasted text")

    records = [
        LeadRecord(phone_raw=tok, source_row=i)
        for i, tok in enumerate(tokens, start=1)
    ]

    _preflight_phones(records, default_country)

    # 3. Create (or reuse) the paste's contact list ("Pasted contacts <date>").
    from app.api.v1.endpoints.contact_lists import (
        create_contact_list, default_paste_list_name, _live_count, list_display_name,
    )
    list_name = default_paste_list_name()
    try:
        list_id = create_contact_list(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            name=list_name,
            source="paste",
            permission=permission,
            confirmed_by=current_user.id,
        )

        # 4. Shared ingest core, tagging leads with the list.
        result = ingest_lead_records(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            records=records,
            normalize=lambda p: _normalize_for_user(p, current_user, default_country),
            list_id=list_id,
        )
    except OutboundCampaignDirectionConflict as exc:
        raise outbound_campaign_conflict(
            campaign_id,
            message=(
                "The campaign changed to inbound before the contact import completed. "
                "Use the inbound campaign lifecycle."
            ),
        ) from exc

    logger.info(
        "Paste import for campaign %s (list=%s): %d imported (%d revived), %d duplicates, "
        "%d invalid",
        campaign_id, list_id, result.imported, result.revived,
        result.duplicates_skipped, result.invalid,
    )

    list_count = None
    if list_id is not None:
        try:
            list_count = _live_count(
                db_client,
                campaign_id,
                list_id,
                tenant_id=campaign_tenant_id or current_user.tenant_id,
            )
        except Exception:  # noqa: BLE001
            list_count = None

    shown, errors_total, errors_truncated = cap_errors([
        ImportError(row=e.row, error=e.error, phone=e.phone,
                    field=_ingest_error_field(e), code=e.code)
        for e in result.errors
    ])
    log_import_row_errors(
        "Paste", campaign_id, [(e.row, e.code, e.detail, e.phone) for e in result.errors])
    return BulkImportResponse(
        total_rows=result.total,
        imported=result.imported,
        failed=len(result.errors),
        duplicates_skipped=result.duplicates_skipped,
        errors=shown,
        errors_total=errors_total,
        errors_truncated=errors_truncated,
        list_id=list_id,
        list_name=(
            list_display_name(
                db_client, list_id, campaign_tenant_id or current_user.tenant_id, list_name,
            ) if list_id is not None else None
        ),
        list_contact_count=list_count,
    )


# =============================================================================
# Legacy Endpoint (Preserved for Backward Compatibility)
# =============================================================================

@router.post(
    "/bulk",
    response_model=BulkImportResponse,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def bulk_import_contacts(
    file: UploadFile = File(..., description="CSV file with contacts"),
    campaign_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    default_country: Annotated[Optional[str], Query(min_length=2, max_length=2)] = None,
    permission_basis: Annotated[Optional[str], Form()] = None,
    permission_source: Annotated[Optional[str], Form()] = None,
    permission_date: Annotated[Optional[str], Form()] = None,
    permission_statement_version: Annotated[Optional[str], Form()] = None,
):
    """
    Bulk import contacts from CSV file.
    
    LEGACY ENDPOINT - Prefer using POST /campaigns/{id}/contacts/upload for campaign imports.
    
    Used by: Upload Contacts page (/dashboard/upload).
    
    CSV format expected:
        phone_number,first_name,last_name,email
        +1234567890,John,Doe,john@example.com
    
    If campaign_id is provided, contacts are added as leads to that campaign.
    Otherwise, they're added to the clients table.
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are supported"
        )

    # Leads for a campaign need the customer's permission confirmation; the
    # no-campaign path writes the clients table (never dialled) and does not.
    permission = None
    if campaign_id:
        from app.api.v1.endpoints.contact_lists import (
            permission_from_form, resolve_contact_permission,
        )
        permission = resolve_contact_permission(permission_from_form(
            permission_basis, permission_source, permission_date,
            permission_statement_version,
        ))

    try:
        # The legacy endpoint used to trust any campaign UUID and only discover
        # it after parsing/writing rows. Resolve the same outbound boundary as
        # every other campaign-contact mutation before reading the upload.
        campaign = None
        if campaign_id:
            campaign = require_owned_outbound_campaign(
                db_client,
                campaign_id,
                tenant_id=current_user.tenant_id,
                extra_columns=("script_config",),
            )
        default_country = _resolve_country(db_client, current_user.tenant_id, campaign, default_country)

        # Read file content
        content = await file.read()
        
        # Try different encodings
        for encoding in ['utf-8-sig', 'latin-1']:
            try:
                text_content = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(
                status_code=400,
                detail="Unable to decode CSV file. Please use UTF-8 encoding."
            )
        
        # Parse CSV
        csv_reader = csv.DictReader(io.StringIO(text_content))
        
        from app.domain.services.dialer.bulk_ingest import LeadRecord
        csv_rows = list(csv_reader)
        from app.domain.services.contact_fields import (
            SEMICOLON_FILE_MESSAGE, looks_semicolon_separated, map_headers,
        )
        fieldnames = list(csv_reader.fieldnames or [])
        if looks_semicolon_separated(fieldnames):
            raise HTTPException(status_code=400, detail=SEMICOLON_FILE_MESSAGE)
        # The same heading recognition as the campaign upload, so Name, Phone,
        # Mobile ... mean the same thing on both paths.
        header_mapping = map_headers(fieldnames)
        mapped_rows = [_mapped_row(row, header_mapping) for row in csv_rows]
        _preflight_phones([
            LeadRecord(
                phone_raw=(v.get("phone_number") or v.get("mobile_number") or "").strip(),
                source_row=i,
            )
            for i, v in enumerate(mapped_rows, start=2)
        ], default_country)
        # A removed contact's do-not-call mark comes back with its number. Read
        # once, before anything is written; a failed read ends the request.
        removed_marked: set[str] = set()
        if campaign_id:
            from app.domain.services.dialer.removed_dnc_marks import removed_marked_phones
            removed_marked = removed_marked_phones(
                db_client, campaign_id=campaign_id, tenant_id=current_user.tenant_id,
            )
        list_id = None
        if permission is not None:
            from app.api.v1.endpoints.contact_lists import create_contact_list
            list_id = create_contact_list(
                db_client,
                campaign_id=campaign_id,
                tenant_id=campaign.get("tenant_id") or current_user.tenant_id,
                name=file.filename,
                source="csv",
                permission=permission,
                confirmed_by=current_user.id,
            )
        total_rows = 0
        imported = 0
        errors = []
        log_entries = []
        
        for row_num, row in enumerate(csv_rows, start=2):  # Start at 2 (header is row 1)
            total_rows += 1
            values = mapped_rows[row_num - 2]
            
            try:
                # Validate required field
                phone = (values.get("phone_number") or values.get("mobile_number") or "").strip()
                if not phone:
                    errors.append(ImportError(
                        row=row_num, error=ROW_ERROR_TEXT["phone_missing"],
                        field="phone_number", code="phone_missing"))
                    log_entries.append((row_num, "phone_missing", "no phone number", None))
                    continue
                
                # Use the single domain normalizer shared by manual add, paste,
                # CSV and the dialer. The old strip/prefix parser accepted
                # impossible numbers and mangled national formats.
                try:
                    phone = _normalize_for_user(phone, current_user, default_country)
                except ValueError as exc:
                    code, sentence = _phone_failure(phone, default_country)
                    errors.append(
                        ImportError(
                            row=row_num,
                            error=sentence,
                            field="phone_number",
                            code=code,
                        )
                    )
                    log_entries.append((row_num, code, str(exc), phone))
                    continue
                
                # Prepare data
                first_name, last_name = _names_from_values(values)
                email = values.get("email")
                company = values.get("company_name")
                
                if campaign_id:
                    # Add as lead to campaign (with tenant_id)
                    lead_row = {
                        "id": str(uuid.uuid4()),
                        "tenant_id": current_user.tenant_id,
                        "campaign_id": campaign_id,
                        "phone_number": phone,
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": email,
                        "status": "pending",
                        "last_call_result": "pending"
                    }
                    if phone in removed_marked:
                        lead_row["do_not_call"] = True
                    if list_id is not None:
                        lead_row["list_id"] = list_id
                    write_response = db_client.table("leads").insert(lead_row).execute()
                else:
                    # Add as client
                    name = f"{first_name or ''} {last_name or ''}".strip() or "Unknown"
                    write_response = db_client.table("clients").insert({
                        "tenant_id": current_user.tenant_id,
                        "name": name,
                        "company": company,
                        "phone": phone,
                        "email": email,
                        "tags": []
                    }).execute()

                if getattr(write_response, "error", None):
                    if campaign_id:
                        raise_for_outbound_direction_guard(
                            write_response.error,
                            campaign_id,
                        )
                    raise RuntimeError("Database write failed")
                if not getattr(write_response, "data", None):
                    raise RuntimeError("Database write affected no rows")
                
                imported += 1
            
            except OutboundCampaignDirectionConflict:
                raise
            except Exception as e:
                errors.append(ImportError(
                    row=row_num, error=ROW_ERROR_TEXT["save_failed"], code="save_failed"))
                log_entries.append((row_num, "save_failed", str(e), phone))
        
        log_import_row_errors("Legacy bulk", campaign_id, log_entries)
        shown, errors_total, errors_truncated = cap_errors(errors)
        return BulkImportResponse(
            total_rows=total_rows,
            imported=imported,
            failed=len(errors),
            duplicates_skipped=0,
            errors=shown,
            errors_total=errors_total,
            errors_truncated=errors_truncated,
        )
    
    except OutboundCampaignDirectionConflict as exc:
        raise outbound_campaign_conflict(
            str(campaign_id),
            message=(
                "The campaign changed to inbound before the contact import completed. "
                "Use the inbound campaign lifecycle."
            ),
        ) from exc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_message(e, 'Failed to import contacts.')
        )
