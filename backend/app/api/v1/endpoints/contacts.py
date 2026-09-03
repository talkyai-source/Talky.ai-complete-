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
import csv
import io
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
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
    errors: List[ImportError]
    # Cells we could not use on rows that DID import. Separate from ``errors``
    # so ``failed`` keeps meaning "contacts that did not land": a contact whose
    # timezone was junk is still a contact, and losing it over one bad cell
    # would be worse than the bad cell. Every entry names the row and the
    # field, and the value was not written.
    field_errors: List[ImportError] = []
    list_id: Optional[str] = None
    list_name: Optional[str] = None
    list_contact_count: Optional[int] = None


class BulkPasteRequest(BaseModel):
    """Pasted-text bulk import payload — a blob of phone numbers, one per
    line or separated by commas/semicolons. Names are not parsed from a
    paste (use CSV for that); every token is treated as a phone number."""
    text: str


def _campaign_default_country(campaign) -> str:
    """The campaign's ISO country, resolved exactly as campaigns.py does.

    Imported lazily (and defensively) because contacts.py <-> campaigns.py is
    already a circular pair; a failure here must degrade to "US", never break
    an import.
    """
    try:
        from app.api.v1.endpoints.campaigns import campaign_default_country
        return campaign_default_country(campaign)
    except Exception:  # noqa: BLE001 — country resolution must never break import
        return "US"


def _normalize_for_user(phone: str, user, default_country: str = "US") -> str:
    """Normalize a phone number the SAME way the Add-Contact endpoint does, so
    CSV import and manual add never disagree.

    Uses the canonical domain normalizer (libphonenumber-backed, rejects 6-digit
    junk), and the lenient passthrough only for accounts whose phone validation
    is temporarily relaxed — exactly mirroring add_contact_to_campaign. Before
    this, CSV import had its OWN looser rules (accepted 6-digit numbers for
    everyone), so a contact imported via CSV could be un-dialable by the
    campaign path that re-validated with the stricter normalizer.

    ``default_country`` is the campaign's country (see
    :func:`app.api.v1.endpoints.campaigns.campaign_default_country`) and is the
    second half of that same "never disagree" promise: the manual-add path has
    always passed it, so a UK campaign's "07700 900123" became ``+447700900123``
    there while the CSV path — which dropped the parameter and silently used US
    — stored the un-dialable ``+07700900123`` for the very same number.
    """
    from app.domain.services.phone_number_normalizer import (
        normalize_phone_number as _domain_normalize,
        normalize_phone_number_lenient,
    )
    try:
        from app.api.v1.endpoints.campaigns import _phone_validation_relaxed
        relaxed = _phone_validation_relaxed(user)
    except Exception:  # noqa: BLE001 — never let the relaxed check break import
        relaxed = False
    if relaxed:
        return normalize_phone_number_lenient(phone)
    return _domain_normalize(phone, default_country=default_country or "US")


def normalize_phone_number(phone: str) -> str:
    """
    DEPRECATED legacy normalizer — kept only so nothing breaks if an old import
    path still references it. New code must use _normalize_for_user so CSV import
    and Add-Contact share one definition (see Fix #9). Do not add callers.

    Normalize phone number to E.164 format.

    Args:
        phone: Raw phone number string

    Returns:
        Normalized phone in E.164 format (+1234567890)
        
    Raises:
        ValueError: If phone is invalid
    """
    from app.domain.services.phone_number_normalizer import (
        normalize_phone_number_legacy,
    )

    return normalize_phone_number_legacy(phone)


# =============================================================================
# The import template (goals.md §11 "Update CSV import template")
# =============================================================================

def _ingest_error_field(err) -> Optional[str]:
    """Which column a shared-ingest error belongs to.

    ``bulk_ingest`` is shared with the paste importer and carries no field, but
    everything it reports per row is about the dial number. The exceptions are
    the batch database failures, which belong to no column at all.
    """
    text = (getattr(err, "error", "") or "").lower()
    if text.startswith("database insert failed") or text.startswith("revive failed"):
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
    campaign_id: str,
    file: UploadFile = File(..., description="CSV file with contacts"),
    skip_duplicates: bool = Query(True, description="Skip duplicate phone numbers within campaign"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client)
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
        default_country = _campaign_default_country(campaign)

        # 2. Read and decode file
        content = await file.read()
        text_content = None
        
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
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
            BY_KEY, coerce_bool, map_headers, validate_row,
        )

        fieldnames = list(csv_reader.fieldnames or [])
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
        for row_num, row in enumerate(csv_reader, start=2):  # Row 1 is header
            values: dict[str, str] = {}
            custom_fields: dict = {}
            for header, value in row.items():
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
                label = BY_KEY[issue.field].label
                field_errors.append(ImportError(
                    row=issue.row, field=issue.field, value=issue.value,
                    error=f"{label}: {issue.reason}",
                ))

            first_name = values.get("first_name")
            last_name = values.get("last_name")
            if not first_name and not last_name and values.get("full_name"):
                name_parts = values["full_name"].split()
                first_name = name_parts[0] if name_parts else None
                last_name = " ".join(name_parts[1:]) or None

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

        # 5. Create (or reuse) the contact list for this upload — named after
        #    the uploaded file. Ordinary list failures fall back to Ungrouped;
        #    a direction-trigger rejection aborts with the stable 409 contract.
        from app.api.v1.endpoints.contact_lists import create_contact_list, _live_count
        list_id = create_contact_list(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            name=file.filename,
            source="csv",
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

        return BulkImportResponse(
            total_rows=result.total,
            imported=result.imported,
            failed=len(result.errors),
            duplicates_skipped=result.duplicates_skipped,
            errors=[
                ImportError(row=e.row, error=e.error, phone=e.phone,
                            field=_ingest_error_field(e))
                for e in result.errors[:100]
            ],
            field_errors=field_errors[:200],
            list_id=list_id,
            list_name=file.filename if list_id is not None else None,
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
            detail=f"Failed to import contacts: {str(e)}"
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
    campaign_id: str,
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

    # 1. Validate ownership and outbound direction before list/lead writes.
    campaign = require_owned_outbound_campaign(
        db_client,
        campaign_id,
        tenant_id=current_user.tenant_id,
        extra_columns=("name", "script_config"),
    )
    campaign_tenant_id = campaign.get("tenant_id")
    default_country = _campaign_default_country(campaign)

    # 2. Parse the blob into raw tokens.
    tokens = parse_pasted_numbers(body.text)
    if not tokens:
        raise HTTPException(status_code=400, detail="No phone numbers found in the pasted text")

    records = [
        LeadRecord(phone_raw=tok, source_row=i)
        for i, tok in enumerate(tokens, start=1)
    ]

    # 3. Create (or reuse) the paste's contact list ("Pasted contacts <date>").
    from app.api.v1.endpoints.contact_lists import (
        create_contact_list, default_paste_list_name, _live_count,
    )
    list_name = default_paste_list_name()
    try:
        list_id = create_contact_list(
            db_client,
            campaign_id=campaign_id,
            tenant_id=campaign_tenant_id or current_user.tenant_id,
            name=list_name,
            source="paste",
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

    return BulkImportResponse(
        total_rows=result.total,
        imported=result.imported,
        failed=len(result.errors),
        duplicates_skipped=result.duplicates_skipped,
        errors=[
            ImportError(row=e.row, error=e.error, phone=e.phone,
                        field=_ingest_error_field(e))
            for e in result.errors[:100]
        ],
        list_id=list_id,
        list_name=list_name if list_id is not None else None,
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
    db_client: Client = Depends(get_db_client)
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
        default_country = _campaign_default_country(campaign or {})

        # Read file content
        content = await file.read()
        
        # Try different encodings
        for encoding in ['utf-8', 'utf-8-sig', 'latin-1']:
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
        
        total_rows = 0
        imported = 0
        errors = []
        
        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (header is row 1)
            total_rows += 1
            
            try:
                # Validate required field
                phone = row.get('phone_number', '').strip()
                if not phone:
                    errors.append(ImportError(row=row_num, error="Missing phone_number"))
                    continue
                
                # Use the single domain normalizer shared by manual add, paste,
                # CSV and the dialer. The old strip/prefix parser accepted
                # impossible numbers and mangled national formats.
                try:
                    phone = _normalize_for_user(phone, current_user, default_country)
                except ValueError as exc:
                    errors.append(
                        ImportError(
                            row=row_num,
                            error=f"Invalid phone number: {exc}",
                            field="phone_number",
                        )
                    )
                    continue
                
                # Prepare data
                first_name = row.get('first_name', '').strip() or None
                last_name = row.get('last_name', '').strip() or None
                email = row.get('email', '').strip() or None
                company = row.get('company', '').strip() or None
                
                if campaign_id:
                    # Add as lead to campaign (with tenant_id)
                    write_response = db_client.table("leads").insert({
                        "id": str(uuid.uuid4()),
                        "tenant_id": current_user.tenant_id,
                        "campaign_id": campaign_id,
                        "phone_number": phone,
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": email,
                        "status": "pending",
                        "last_call_result": "pending"
                    }).execute()
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
                errors.append(ImportError(row=row_num, error=str(e)))
        
        return BulkImportResponse(
            total_rows=total_rows,
            imported=imported,
            failed=len(errors),
            duplicates_skipped=0,
            errors=errors[:50]  # Limit errors returned
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
            detail=f"Failed to import contacts: {str(e)}"
        )
