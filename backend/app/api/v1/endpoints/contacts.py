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
from supabase import Client

from app.api.v1.dependencies import get_supabase, get_current_user, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ImportError(BaseModel):
    """Single import error"""
    row: int
    error: str
    phone: Optional[str] = None


class BulkImportResponse(BaseModel):
    """Bulk import response"""
    total_rows: int
    imported: int
    failed: int
    duplicates_skipped: int = 0
    errors: List[ImportError]


def normalize_phone_number(phone: str) -> str:
    """
    Normalize phone number to E.164 format.
    
    Handles common formats and validates basic structure.
    For production, consider using the 'phonenumbers' library.
    
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
    
    if len(cleaned) < 7:
        raise ValueError("Phone number too short (minimum 7 digits)")
    
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
    supabase: Client = Depends(get_supabase)
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
    
    CSV Format Expected:
        phone_number,first_name,last_name,email
        +1234567890,John,Doe,john@example.com
        555-123-4567,Jane,Smith,jane@example.com
    
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
        # 1. Validate campaign exists
        campaign_response = supabase.table("campaigns").select("id, name").eq("id", campaign_id).execute()
        if not campaign_response.data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        campaign_name = campaign_response.data[0].get("name", "Unknown")
        
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
        
        # 4. Get existing phone numbers in campaign for duplicate detection
        existing_phones: Set[str] = set()
        if skip_duplicates:
            existing_response = supabase.table("leads").select("phone_number").eq(
                "campaign_id", campaign_id
            ).neq("status", "deleted").execute()
            
            existing_phones = {row["phone_number"] for row in (existing_response.data or [])}
        
        # 5. Process rows
        total_rows = 0
        imported = 0
        duplicates_skipped = 0
        errors: List[ImportError] = []
        leads_to_insert: List[dict] = []
        seen_phones_in_file: Set[str] = set()  # Track duplicates within file
        
        for row_num, row in enumerate(csv_reader, start=2):  # Row 1 is header
            total_rows += 1
            
            try:
                # Get phone number (case-insensitive column match)
                phone_raw = None
                for key in row:
                    if key.lower().strip() == 'phone_number':
                        phone_raw = row[key]
                        break
                
                if not phone_raw or not phone_raw.strip():
                    errors.append(ImportError(row=row_num, error="Missing phone_number", phone=None))
                    continue
                
                # Normalize phone
                try:
                    normalized_phone = normalize_phone_number(phone_raw.strip())
                except ValueError as e:
                    errors.append(ImportError(row=row_num, error=str(e), phone=phone_raw))
                    continue
                
                # Check for duplicate within this file
                if normalized_phone in seen_phones_in_file:
                    duplicates_skipped += 1
                    continue
                seen_phones_in_file.add(normalized_phone)
                
                # Check for duplicate within campaign (existing contacts)
                if normalized_phone in existing_phones:
                    duplicates_skipped += 1
                    continue
                
                # Extract other fields (case-insensitive)
                first_name = None
                last_name = None
                email = None
                custom_fields = {}
                
                for key, value in row.items():
                    key_lower = key.lower().strip()
                    value_clean = value.strip() if value else None
                    
                    if key_lower == 'first_name':
                        first_name = value_clean
                    elif key_lower == 'last_name':
                        last_name = value_clean
                    elif key_lower == 'email':
                        email = value_clean
                    elif key_lower not in ('phone_number',):
                        # Store additional columns as custom fields
                        if value_clean:
                            custom_fields[key] = value_clean
                
                # Prepare lead record
                lead_data = {
                    "id": str(uuid.uuid4()),
                    "campaign_id": campaign_id,
                    "phone_number": normalized_phone,
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "custom_fields": custom_fields if custom_fields else {},
                    "status": "pending",
                    "last_call_result": "pending",
                    "call_attempts": 0,
                    "created_at": datetime.utcnow().isoformat()
                }
                
                leads_to_insert.append(lead_data)
                existing_phones.add(normalized_phone)  # Track to prevent duplicates in later rows
                
            except Exception as e:
                errors.append(ImportError(row=row_num, error=str(e), phone=str(row.get('phone_number', ''))))
        
        # 6. Batch insert leads
        if leads_to_insert:
            # Supabase supports batch insert - insert all at once for performance
            # Split into chunks of 500 to avoid request size limits
            chunk_size = 500
            for i in range(0, len(leads_to_insert), chunk_size):
                chunk = leads_to_insert[i:i + chunk_size]
                try:
                    supabase.table("leads").insert(chunk).execute()
                    imported += len(chunk)
                except Exception as e:
                    logger.error(f"Batch insert failed for chunk {i}-{i+len(chunk)}: {e}")
                    # Mark all in this chunk as failed
                    for j, lead in enumerate(chunk):
                        errors.append(ImportError(
                            row=i + j + 2,  # Approximate row number
                            error=f"Database insert failed: {str(e)}",
                            phone=lead.get("phone_number")
                        ))
        
        logger.info(
            f"CSV upload completed for campaign '{campaign_name}': "
            f"{imported} imported, {duplicates_skipped} duplicates skipped, {len(errors)} errors"
        )
        
        return BulkImportResponse(
            total_rows=total_rows,
            imported=imported,
            failed=len(errors),
            duplicates_skipped=duplicates_skipped,
            errors=errors[:100]  # Limit errors returned to first 100
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
# Legacy Endpoint (Preserved for Backward Compatibility)
# =============================================================================

@router.post("/bulk", response_model=BulkImportResponse)
async def bulk_import_contacts(
    file: UploadFile = File(..., description="CSV file with contacts"),
    campaign_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
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
                    # Add as lead to campaign
                    supabase.table("leads").insert({
                        "id": str(uuid.uuid4()),
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
                    supabase.table("clients").insert({
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

