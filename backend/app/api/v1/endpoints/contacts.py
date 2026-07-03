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
import re
import logging
import uuid
from datetime import datetime
from typing import List, Optional, Set

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from pydantic import BaseModel
from app.core.postgres_adapter import Client

from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.utils.tenant_filter import apply_tenant_filter, verify_tenant_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ImportError(BaseModel):
    """Single import error"""
    row: Optional[int] = None
    error: str
    phone: Optional[str] = None


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
    list_id: Optional[str] = None
    list_name: Optional[str] = None
    list_contact_count: Optional[int] = None


class BulkPasteRequest(BaseModel):
    """Pasted-text bulk import payload — a blob of phone numbers, one per
    line or separated by commas/semicolons. Names are not parsed from a
    paste (use CSV for that); every token is treated as a phone number."""
    text: str


def _normalize_for_user(phone: str, user) -> str:
    """Normalize a phone number the SAME way the Add-Contact endpoint does, so
    CSV import and manual add never disagree.

    Uses the canonical domain normalizer (libphonenumber-backed, rejects 6-digit
    junk), and the lenient passthrough only for accounts whose phone validation
    is temporarily relaxed — exactly mirroring add_contact_to_campaign. Before
    this, CSV import had its OWN looser rules (accepted 6-digit numbers for
    everyone), so a contact imported via CSV could be un-dialable by the
    campaign path that re-validated with the stricter normalizer.
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
    return normalize_phone_number_lenient(phone) if relaxed else _domain_normalize(phone)


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
    if not phone:
        raise ValueError("Phone number is empty")
    
    # Remove all non-digit characters except leading +
    has_plus = phone.strip().startswith('+')
    cleaned = re.sub(r'[^\d]', '', phone)
    
    if not cleaned:
        raise ValueError("Phone number contains no digits")

    # Allow short SIP extensions (3–6 digits) to pass through as-is
    if len(cleaned) <= 6:
        if len(cleaned) < 3:
            raise ValueError("Phone number too short (minimum 3 digits for SIP extensions)")
        return cleaned  # Return raw SIP extension — no E.164 normalization

    if len(cleaned) > 15:
        raise ValueError("Phone number too long (maximum 15 digits)")

    # If already has + and country code, use as-is
    if has_plus:
        return f"+{cleaned}"

    # If 10 digits (US/Canada without country code), add +1
    if len(cleaned) == 10:
        return f"+1{cleaned}"

    # If 11 digits starting with 1 (US/Canada with country code), add +
    if len(cleaned) == 11 and cleaned.startswith('1'):
        return f"+{cleaned}"

    # Otherwise, return with + prefix
    return f"+{cleaned}"


# =============================================================================
# Day 9: Campaign-Scoped CSV Upload Endpoint
# =============================================================================

@router.post("/campaigns/{campaign_id}/upload", response_model=BulkImportResponse)
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
    
    CSV Format Expected (company is optional; any extra column is preserved
    in custom_fields and never breaks the import):
        phone_number,first_name,last_name,email,company
        +1234567890,John,Doe,john@example.com,Acme Roofing
        555-123-4567,Jane,Smith,jane@example.com,

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
        # 1. Validate campaign exists AND belongs to user's tenant
        campaign_query = db_client.table("campaigns").select("id, name, tenant_id").eq("id", campaign_id)
        campaign_query = apply_tenant_filter(campaign_query, current_user.tenant_id)
        campaign_response = campaign_query.execute()
        
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign_name = campaign_response.data[0].get("name", "Unknown")
        campaign_tenant_id = campaign_response.data[0].get("tenant_id")
        
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
        
        # Validate headers
        required_headers = {'phone_number'}
        if csv_reader.fieldnames:
            headers = {h.lower().strip() for h in csv_reader.fieldnames}
            if not required_headers.issubset(headers):
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV must have 'phone_number' column. Found: {', '.join(csv_reader.fieldnames)}"
                )
        
        # 4. Parse CSV rows into LeadRecords (CSV-specific field mapping).
        #    Dedup / revive / chunk-insert is handled by the shared
        #    bulk-ingest core so CSV and pasted-text imports behave
        #    identically.
        from app.domain.services.dialer.bulk_ingest import (
            LeadRecord, ingest_lead_records,
        )
        records: List[LeadRecord] = []
        for row_num, row in enumerate(csv_reader, start=2):  # Row 1 is header
            phone_raw = None
            first_name = last_name = email = company = None
            custom_fields: dict = {}
            for key, value in row.items():
                key_lower = (key or "").lower().strip()
                value_clean = value.strip() if value else None
                if key_lower == 'phone_number':
                    phone_raw = value_clean
                elif key_lower == 'first_name':
                    first_name = value_clean
                elif key_lower == 'last_name':
                    last_name = value_clean
                elif key_lower == 'email':
                    email = value_clean
                elif key_lower == 'company':
                    # Recognized 5th column — stored canonically as
                    # custom_fields.company by the ingest core and later
                    # injected into the agent's "who you're calling" prompt.
                    company = value_clean
                elif value_clean:
                    # Any OTHER column is preserved verbatim in custom_fields,
                    # so an extra/unknown column never breaks the import.
                    custom_fields[key] = value_clean
            records.append(LeadRecord(
                phone_raw=phone_raw or "",
                first_name=first_name,
                last_name=last_name,
                email=email,
                company=company,
                custom_fields=custom_fields,
                source_row=row_num,
            ))

        # 5. Create (or reuse) the contact list for this upload — named after
        #    the uploaded file. Best-effort: if it fails, list_id stays None and
        #    the leads import as Ungrouped rather than the whole upload failing.
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
            normalize=lambda p: _normalize_for_user(p, current_user),
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
                list_count = _live_count(db_client, campaign_id, list_id)
            except Exception:  # noqa: BLE001
                list_count = None

        return BulkImportResponse(
            total_rows=result.total,
            imported=result.imported,
            failed=len(result.errors),
            duplicates_skipped=result.duplicates_skipped,
            errors=[
                ImportError(row=e.row, error=e.error, phone=e.phone)
                for e in result.errors[:100]
            ],
            list_id=list_id,
            list_name=file.filename if list_id is not None else None,
            list_contact_count=list_count,
        )
    
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

@router.post("/campaigns/{campaign_id}/paste", response_model=BulkImportResponse)
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

    # 1. Validate campaign exists AND belongs to the caller's tenant.
    campaign_query = db_client.table("campaigns").select("id, name, tenant_id").eq("id", campaign_id)
    campaign_query = apply_tenant_filter(campaign_query, current_user.tenant_id)
    campaign_response = campaign_query.execute()
    if not campaign_response.data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign_tenant_id = campaign_response.data[0].get("tenant_id")

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
        normalize=lambda p: _normalize_for_user(p, current_user),
        list_id=list_id,
    )

    logger.info(
        "Paste import for campaign %s (list=%s): %d imported (%d revived), %d duplicates, "
        "%d invalid",
        campaign_id, list_id, result.imported, result.revived,
        result.duplicates_skipped, result.invalid,
    )

    list_count = None
    if list_id is not None:
        try:
            list_count = _live_count(db_client, campaign_id, list_id)
        except Exception:  # noqa: BLE001
            list_count = None

    return BulkImportResponse(
        total_rows=result.total,
        imported=result.imported,
        failed=len(result.errors),
        duplicates_skipped=result.duplicates_skipped,
        errors=[
            ImportError(row=e.row, error=e.error, phone=e.phone)
            for e in result.errors[:100]
        ],
        list_id=list_id,
        list_name=list_name if list_id is not None else None,
        list_contact_count=list_count,
    )


# =============================================================================
# Legacy Endpoint (Preserved for Backward Compatibility)
# =============================================================================

@router.post("/bulk", response_model=BulkImportResponse)
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
                
                # Clean phone number
                phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
                if not phone.startswith('+'):
                    phone = '+' + phone
                
                # Basic phone validation
                if len(phone) < 8 or not phone[1:].isdigit():
                    errors.append(ImportError(row=row_num, error="Invalid phone number"))
                    continue
                
                # Prepare data
                first_name = row.get('first_name', '').strip() or None
                last_name = row.get('last_name', '').strip() or None
                email = row.get('email', '').strip() or None
                company = row.get('company', '').strip() or None
                
                if campaign_id:
                    # Add as lead to campaign (with tenant_id)
                    db_client.table("leads").insert({
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
                    db_client.table("clients").insert({
                        "tenant_id": current_user.tenant_id,
                        "name": name,
                        "company": company,
                        "phone": phone,
                        "email": email,
                        "tags": []
                    }).execute()
                
                imported += 1
            
            except Exception as e:
                errors.append(ImportError(row=row_num, error=str(e)))
        
        return BulkImportResponse(
            total_rows=total_rows,
            imported=imported,
            failed=len(errors),
            duplicates_skipped=0,
            errors=errors[:50]  # Limit errors returned
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import contacts: {str(e)}"
        )

