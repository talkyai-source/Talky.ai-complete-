# Day 30: CRM & Drive Integration

> **Date**: January 14, 2026  
> **Focus**: Close the loop with customer systems - CRM sync and Drive backup  
> **Status**: Implementation Complete ✅

---

## Overview

Today we integrated CRM synchronization and Google Drive file storage into the post-call pipeline. Call data now automatically syncs to HubSpot CRM and recordings/transcripts are backed up to Google Drive with shareable links.

### Key Design Decision: Hybrid Approach

Instead of requiring users to connect all integrations upfront, we implemented a **hybrid sync approach**:

- ✅ **Always save locally first** - Recording and transcript saved regardless of integration status
- ✅ **Sync if connected** - If HubSpot/Drive are connected, data syncs automatically
- ✅ **Warn if not connected** - User-friendly message with actionable next steps
- ✅ **Zero latency impact** - All sync runs as background task after call ends

### Key Features

- ✅ **CRM Contact Management** - Auto-create or find existing contacts by email
- ✅ **Call Activity Logging** - Log calls with summary, duration, and outcome to HubSpot timeline
- ✅ **Drive Backup** - Upload recordings as `.wav` and transcripts as `.md`
- ✅ **Folder Hierarchy** - `Talky.ai Calls/{tenant_name}/{YYYY-MM-DD}/`
- ✅ **Viewer-Safe Transcripts** - Markdown formatting with escaped symbols for clean rendering
- ✅ **Drive Links in CRM** - Attach recording/transcript links as notes in HubSpot
- ✅ **Token Refresh** - Automatic OAuth token refresh for long-running tenants

---

## Architecture

### Directory Structure

```
backend/app/
├── services/
│   ├── crm_sync_service.py          # CRM orchestration service (NEW)
│   └── drive_sync_service.py        # Drive backup service (NEW)
│
├── infrastructure/connectors/
│   ├── base.py                       # +LOG_CALL, CREATE_NOTE capabilities (MODIFIED)
│   └── crm/
│       ├── base.py                   # +log_call(), create_note() abstract (MODIFIED)
│       └── hubspot.py                # +log_call(), create_note() impl (MODIFIED)
│
├── api/v1/endpoints/
│   └── websockets.py                 # +Drive/CRM sync in _save_call_data (MODIFIED)
│
└── database/migrations/
    └── add_crm_drive_sync.sql        # New columns for tracking (NEW)

tests/unit/
├── test_crm_sync_service.py          # 17 unit tests (NEW)
└── test_drive_sync_service.py        # 17 unit tests (NEW)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     CRM & DRIVE SYNC PIPELINE FLOW                          │
└─────────────────────────────────────────────────────────────────────────────┘

DURING CALL (unchanged, zero latency):
┌─────────────────────────────────────────────────────────────────────────────┐
│  Audio → DeepgramFlux → GroqLLM → Cartesia TTS → Audio                      │
│                      (normal voice pipeline)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ call ends, WebSocket closes
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      _save_call_data() Background Task                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. Save recording to Supabase Storage (Day 10)                             │
│  2. Save transcript to database (Day 10)                                    │
│  3. Update call record with status/duration (Day 16)                        │
│  4. PostCallAnalyzer.analyze_call() (Day 29)                                │
│  5. ┌────────────────────────────────────────┐                              │
│     │  DriveSyncService.sync_call_files() D30│                              │
│     │  ─────────────────────────────────────  │                              │
│     │  • Create folder hierarchy             │                              │
│     │  • Upload recording as .wav            │                              │
│     │  • Upload transcript as .md            │                              │
│     │  • Store file IDs and links            │                              │
│     └───────────────────┬────────────────────┘                              │
│  6. ┌────────────────────────────────────────┐                              │
│     │  CRMSyncService.sync_call() Day 30     │                              │
│     │  ─────────────────────────────────────  │                              │
│     │  • Find or create contact              │                              │
│     │  • Log call activity                   │                              │
│     │  • Attach Drive links as note          │                              │
│     └────────────────────────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────────┘
                           │
         ┌─────────────────┴─────────────────┐
         ▼                                   ▼
┌───────────────────┐              ┌───────────────────┐
│ CONNECTED         │              │ NOT CONNECTED     │
│ Integration works │              │ Skip + warn       │
└─────────┬─────────┘              └─────────┬─────────┘
          ▼                                  ▼
┌───────────────────┐              ┌───────────────────┐
│ Data synced to    │              │ Data saved        │
│ HubSpot + Drive   │              │ locally only      │
│ File IDs stored   │              │ Warning logged    │
└───────────────────┘              └───────────────────┘
```

---

## Usage Examples

### Example 1: Full Flow - All Integrations Connected

```python
# This happens automatically in _save_call_data() after call ends

# Call ends with:
# - Recording: 45 seconds of audio
# - Transcript: "User: I'd like to schedule a demo for tomorrow..."
# - Lead email: john@example.com

# DriveSyncService does:
# 1. Creates folders: Talky.ai Calls/Acme Corp/2026-01-14/
# 2. Uploads: call-uuid-123.wav
# 3. Uploads: call-uuid-123_transcript.md
# 4. Stores links in database

# CRMSyncService does:
# 1. Searches HubSpot for john@example.com
# 2. Finds existing contact (or creates new)
# 3. Logs call activity with 45s duration, "COMPLETED" status
# 4. Creates note with Drive links:
#    "📞 Call Summary
#     Duration: 0m 45s
#     🎙️ [Recording](https://drive.google.com/...)
#     📝 [Transcript](https://drive.google.com/...)"

# Result:
crm_result = CRMSyncResult(
    success=True,
    crm_contact_id="12345678901",
    crm_call_id="98765432101",
    crm_note_id="11223344556"
)
```

### Example 2: Missing CRM Connector

```python
# Tenant has Google Drive connected but NOT HubSpot

# DriveSyncService: ✓ Works normally, uploads files

# CRMSyncService: Returns warning
crm_result = CRMSyncResult(
    success=False,
    skipped=True,
    skipped_reason="no_crm_connected",
    warning_message="""⚠️ CRM not connected. Call data was saved locally but not synced 
    to your CRM. Connect HubSpot in Settings > Integrations to enable:
    • Automatic lead creation/updates
    • Call logging with summaries
    • Meeting attachment to contacts"""
)

# User sees this recommendation next time they log in
```

### Example 3: Programmatic Usage

```python
from app.services.crm_sync_service import get_crm_sync_service
from app.services.drive_sync_service import get_drive_sync_service

# Initialize services
crm_service = get_crm_sync_service(supabase)
drive_service = get_drive_sync_service(supabase)

# Upload files to Drive
drive_result = await drive_service.sync_call_files(
    tenant_id="tenant-uuid-123",
    call_id="call-uuid-456",
    recording_bytes=audio_bytes,
    transcript_text="User: Hello...",
    lead_name="John Doe",
    call_timestamp=datetime.utcnow()
)

print(f"Recording: {drive_result.recording_link}")
print(f"Transcript: {drive_result.transcript_link}")

# Sync to CRM with Drive links
crm_result = await crm_service.sync_call(
    tenant_id="tenant-uuid-123",
    call_id="call-uuid-456",
    lead_data={"lead_id": "lead-uuid-789"},
    call_summary="Customer interested in product demo",
    duration_seconds=120,
    outcome="completed",
    drive_recording_link=drive_result.recording_link,
    drive_transcript_link=drive_result.transcript_link
)

print(f"Contact: {crm_result.crm_contact_id}")
print(f"Call logged: {crm_result.crm_call_id}")
```

---

## API Reference

### CRMSyncService Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `sync_call()` | Main sync: find/create contact, log call, create note | `CRMSyncResult` |
| `_get_crm_connector()` | Get active CRM connector for tenant | `HubSpotConnector` or `None` |
| `_get_lead()` | Fetch lead data from database | `Dict` or `None` |
| `_update_lead_crm_id()` | Store CRM contact ID on lead record | `None` |
| `_update_call_crm_ids()` | Store call/note IDs on call record | `None` |
| `_map_outcome()` | Map internal outcome to HubSpot status | `str` |
| `_build_note_body()` | Create markdown note with Drive links | `str` |

### DriveSyncService Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `sync_call_files()` | Upload recording and transcript to Drive | `DriveSyncResult` |
| `_get_drive_connector()` | Get active Drive connector for tenant | `GoogleDriveConnector` or `None` |
| `_ensure_folder_hierarchy()` | Create `Talky.ai Calls/{tenant}/{date}/` folders | `str` (folder ID) |
| `_get_tenant()` | Fetch tenant name for folder naming | `Dict` or `None` |
| `_update_recording_drive_info()` | Store file ID/link in recordings table | `None` |
| `_update_transcript_drive_info()` | Store file ID/link in transcripts table | `None` |
| `_sanitize_folder_name()` | Remove invalid chars for Drive folder names | `str` |
| `_format_transcript_for_viewer()` | Format transcript as viewer-safe markdown | `str` |

### Domain Models

| Model | Description |
|-------|-------------|
| `CRMSyncResult` | Result with success, contact_id, call_id, note_id, warning |
| `DriveSyncResult` | Result with success, file_ids, links, folder_id, warning |
| `CRMNotConnectedWarning` | User-friendly warning messages for missing CRM |
| `DriveNotConnectedWarning` | User-friendly warning messages for missing Drive |

---

## HubSpot API Integration

### Endpoints Used

| Endpoint | Purpose | Method |
|----------|---------|--------|
| `/crm/v3/objects/contacts` | Create new contact | POST |
| `/crm/v3/objects/contacts/search` | Find contact by email | POST |
| `/crm/v3/objects/calls` | Log call activity | POST |
| `/crm/v3/objects/notes` | Create note with links | POST |

### Call Logging Payload

```python
# POST to /crm/v3/objects/calls
{
    "properties": {
        "hs_timestamp": "1705248000000",      # Unix ms
        "hs_call_body": "Customer interested in demo...",
        "hs_call_duration": "45000",           # Duration in ms
        "hs_call_direction": "OUTBOUND",
        "hs_call_status": "COMPLETED",
        "hs_call_title": "Call - 2026-01-14 12:00"
    },
    "associations": [
        {
            "to": {"id": "12345678901"},       # Contact ID
            "types": [
                {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 194   # Call to Contact
                }
            ]
        }
    ]
}
```

### Note Creation Payload

```python
# POST to /crm/v3/objects/notes
{
    "properties": {
        "hs_timestamp": "1705248000000",
        "hs_note_body": """📞 **Call Summary**
Duration: 0m 45s

🎙️ [Recording](https://drive.google.com/file/d/...)
📝 [Transcript](https://drive.google.com/file/d/...)

_Call ID: call-uuid..._"""
    },
    "associations": [
        {
            "to": {"id": "12345678901"},
            "types": [
                {
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 202   # Note to Contact
                }
            ]
        }
    ]
}
```

---

## Google Drive Folder Structure

### Hierarchy

```
My Drive/
└── Talky.ai Calls/                    ← Root folder (created once)
    ├── Acme Corporation/              ← Tenant folder (sanitized name)
    │   ├── 2026-01-14/                ← Date folder
    │   │   ├── call-uuid-123.wav      ← Recording
    │   │   └── call-uuid-123_transcript.md  ← Transcript
    │   └── 2026-01-15/
    │       └── ...
    └── Another Company/
        └── ...
```

### Transcript Markdown Format

```markdown
# Call Transcript

**Call ID:** `call-uuid-123`
**Date:** 2026-01-14 12:00 UTC
**Lead:** John Doe

---

## Conversation

> **User:** Hello, I'm interested in your product.

**Assistant:** Great! I'd be happy to help. What specific features are you looking for?

> **User:** I need something for team collaboration.

**Assistant:** We have excellent collaboration tools. Would you like to schedule a demo?

---

*Generated by Talky.ai on 2026-01-14*
```

---

## Database Schema

### New Columns on `leads` Table

```sql
ALTER TABLE leads ADD COLUMN crm_contact_id TEXT;
```

| Column | Type | Description |
|--------|------|-------------|
| `crm_contact_id` | TEXT | External CRM contact ID (e.g., HubSpot ID) |

### New Columns on `calls` Table

```sql
ALTER TABLE calls
ADD COLUMN crm_call_id TEXT,
ADD COLUMN crm_note_id TEXT,
ADD COLUMN crm_synced_at TIMESTAMPTZ;
```

| Column | Type | Description |
|--------|------|-------------|
| `crm_call_id` | TEXT | HubSpot call engagement ID |
| `crm_note_id` | TEXT | HubSpot note ID (with Drive links) |
| `crm_synced_at` | TIMESTAMPTZ | When call was synced to CRM |

### New Columns on `recordings` Table

```sql
ALTER TABLE recordings
ADD COLUMN drive_file_id TEXT,
ADD COLUMN drive_web_link TEXT;
```

| Column | Type | Description |
|--------|------|-------------|
| `drive_file_id` | TEXT | Google Drive file ID |
| `drive_web_link` | TEXT | Shareable Google Drive link |

### New Columns on `transcripts` Table

```sql
ALTER TABLE transcripts
ADD COLUMN drive_file_id TEXT,
ADD COLUMN drive_web_link TEXT;
```

### New Column on `tenant_settings` Table

```sql
ALTER TABLE tenant_settings
ADD COLUMN drive_root_folder_id TEXT;
```

| Column | Type | Description |
|--------|------|-------------|
| `drive_root_folder_id` | TEXT | Cached root folder ID for faster uploads |

### Run Migration

```bash
psql $DATABASE_URL -f backend/database/migrations/add_crm_drive_sync.sql
```

---

## Files Created/Modified

### New Files

| File | Lines | Description |
|------|-------|-------------|
| `app/services/crm_sync_service.py` | 330 | CRM orchestration with hybrid approach |
| `app/services/drive_sync_service.py` | 400 | Drive backup with folder management |
| `database/migrations/add_crm_drive_sync.sql` | 95 | Schema changes for tracking |
| `tests/unit/test_crm_sync_service.py` | 200 | 17 unit tests |
| `tests/unit/test_drive_sync_service.py` | 210 | 17 unit tests |
| `docs/day_thirty_crm_drive_integration.md` | - | This documentation |

### Modified Files

| File | Changes |
|------|---------|
| `app/infrastructure/connectors/base.py` | +2 capabilities: `LOG_CALL`, `CREATE_NOTE` |
| `app/infrastructure/connectors/crm/base.py` | +50 lines: abstract `log_call()`, `create_note()` |
| `app/infrastructure/connectors/crm/hubspot.py` | +140 lines: implementations |
| `app/api/v1/endpoints/websockets.py` | +86 lines: pipeline integration |

---

## Test Results ✅

```
tests/unit/test_crm_sync_service.py::TestCRMSyncResult::test_success_result PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncResult::test_skipped_result PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncResult::test_error_result PASSED
tests/unit/test_crm_sync_service.py::TestCRMNotConnectedWarning::test_missing_crm_warning_has_actionable_message PASSED
tests/unit/test_crm_sync_service.py::TestCRMNotConnectedWarning::test_token_expired_warning PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceInit::test_init_requires_supabase PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceInit::test_singleton_pattern PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceMethods::test_map_outcome_completed PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceMethods::test_map_outcome_no_answer PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceMethods::test_map_outcome_unknown_defaults_to_completed PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceMethods::test_build_note_body_with_links PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncServiceMethods::test_build_note_body_with_meeting PASSED
tests/unit/test_crm_sync_service.py::TestCRMSyncNoConnector::test_sync_without_connector_returns_warning PASSED
tests/unit/test_crm_sync_service.py::TestImportVerification::test_crm_sync_service_importable PASSED
tests/unit/test_crm_sync_service.py::TestImportVerification::test_crm_sync_result_importable PASSED
tests/unit/test_crm_sync_service.py::TestImportVerification::test_singleton_getter_importable PASSED

tests/unit/test_drive_sync_service.py::TestDriveSyncResult::test_success_result PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncResult::test_skipped_result PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncResult::test_no_files_result PASSED
tests/unit/test_drive_sync_service.py::TestDriveNotConnectedWarning::test_missing_drive_warning_has_actionable_message PASSED
tests/unit/test_drive_sync_service.py::TestDriveNotConnectedWarning::test_token_expired_warning PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceInit::test_init_requires_supabase PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceInit::test_root_folder_name PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceInit::test_singleton_pattern PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceMethods::test_sanitize_folder_name_removes_invalid_chars PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceMethods::test_sanitize_folder_name_limits_length PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceMethods::test_format_transcript_has_header PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceMethods::test_format_transcript_escapes_markdown PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncServiceMethods::test_format_transcript_speaker_labels PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncNoConnector::test_sync_without_connector_returns_warning PASSED
tests/unit/test_drive_sync_service.py::TestDriveSyncNoFiles::test_sync_no_files_returns_success_skipped PASSED
tests/unit/test_drive_sync_service.py::TestImportVerification::test_drive_sync_service_importable PASSED
tests/unit/test_drive_sync_service.py::TestImportVerification::test_drive_sync_result_importable PASSED
tests/unit/test_drive_sync_service.py::TestImportVerification::test_singleton_getter_importable PASSED

================================= 34 passed in 1.30s =================================
```

### Syntax Verification

```bash
cd backend

# All files pass Python syntax check
python -m py_compile app/services/crm_sync_service.py       # ✓
python -m py_compile app/services/drive_sync_service.py     # ✓
python -m py_compile app/infrastructure/connectors/crm/hubspot.py  # ✓
python -m py_compile app/infrastructure/connectors/crm/base.py     # ✓
python -m py_compile app/api/v1/endpoints/websockets.py     # ✓
echo "Syntax OK"
```

---

## Security Considerations

| Concern | Implementation |
|---------|----------------|
| **OAuth Token Security** | Tokens encrypted with `CONNECTOR_ENCRYPTION_KEY` |
| **Token Refresh** | Automatic refresh when tokens expire |
| **Tenant Isolation** | All queries include tenant_id |
| **Non-Critical Path** | Sync failures are logged but don't affect call data saving |
| **Folder Privacy** | Each tenant gets isolated folder structure |
| **File Naming** | Uses UUID-based names to prevent collisions |
| **Markdown Escaping** | Transcript content escaped to prevent XSS in viewers |

---

## Integration Points

### 1. websockets.py - _save_call_data()

```python
# Location: app/api/v1/endpoints/websockets.py :: _save_call_data()
# Added after: Day 29 PostCallAnalyzer

# Day 30: Drive Sync - Upload recording and transcript
try:
    from app.services.drive_sync_service import get_drive_sync_service
    
    drive_service = get_drive_sync_service(supabase)
    drive_result = await drive_service.sync_call_files(
        tenant_id=tenant_id,
        call_id=call_id,
        recording_bytes=recording_bytes,
        transcript_text=full_transcript,
        lead_name=lead_name,
        call_timestamp=datetime.utcnow()
    )
    
    if drive_result.success:
        drive_recording_link = drive_result.recording_link
        drive_transcript_link = drive_result.transcript_link
except Exception as e:
    logger.warning(f"Drive sync failed for {call_id}: {e}")

# Day 30: CRM Sync - Log call and create notes with Drive links
try:
    from app.services.crm_sync_service import get_crm_sync_service
    
    crm_service = get_crm_sync_service(supabase)
    crm_result = await crm_service.sync_call(
        tenant_id=tenant_id,
        call_id=call_id,
        lead_data={"lead_id": session.lead_id},
        call_summary=full_transcript[:500] if full_transcript else "",
        duration_seconds=duration_seconds,
        outcome="completed",
        drive_recording_link=drive_recording_link,
        drive_transcript_link=drive_transcript_link
    )
except Exception as e:
    logger.warning(f"CRM sync failed for {call_id}: {e}")
```

### 2. HubSpotConnector - New Methods

```python
# app/infrastructure/connectors/crm/hubspot.py

async def log_call(
    self,
    contact_id: str,
    call_body: str,
    duration_seconds: int,
    outcome: str = "COMPLETED",
    call_direction: str = "OUTBOUND",
    timestamp: datetime = None
) -> str:
    """Log a call activity in HubSpot."""
    # Uses POST /crm/v3/objects/calls
    ...

async def create_note(
    self,
    contact_id: str,
    note_body: str,
    timestamp: datetime = None
) -> str:
    """Create a note attached to a contact."""
    # Uses POST /crm/v3/objects/notes
    ...
```

---

## Setup Instructions

### 1. Run Database Migration

```bash
psql $DATABASE_URL -f backend/database/migrations/add_crm_drive_sync.sql
```

### 2. Configure HubSpot

1. Go to **Settings > Integrations** in your dashboard
2. Click "Connect" next to **HubSpot**
3. Authorize the OAuth flow in the popup
4. CRM sync will activate automatically for all future calls

### 3. Configure Google Drive

1. Go to **Settings > Integrations** in your dashboard
2. Click "Connect" next to **Google Drive**
3. Authorize the OAuth flow in the popup
4. Drive backup will activate automatically for all future calls

### 4. Verify Integration

Make a test call and check:
- HubSpot contact timeline shows call activity
- Google Drive has `Talky.ai Calls/{tenant}/{date}/` folder with files
- Database has `crm_call_id` and `drive_file_id` populated

---

## Next Steps

- [ ] Run database migration on Supabase
- [ ] Connect HubSpot and Google Drive for test tenant
- [ ] Test with live calls
- [ ] Add REST API endpoint for viewing sync status
- [ ] Frontend UI to display Drive links on call details
- [ ] Add Salesforce connector (using same abstract methods)
- [ ] Add OneDrive connector alternative

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **CRM Service** | `CRMSyncService` in app/services/ |
| **Drive Service** | `DriveSyncService` in app/services/ |
| **HubSpot Methods** | `log_call()`, `create_note()` in HubSpotConnector |
| **Folder Structure** | `Talky.ai Calls/{tenant_name}/{YYYY-MM-DD}/` |
| **Transcript Format** | Viewer-safe `.md` with escaped markdown symbols |
| **Database Columns** | 9 new columns across 4 tables |
| **Integration Point** | `_save_call_data()` in websockets.py |
| **Latency Impact** | Zero - all background tasks |
| **Fallback Strategy** | Hybrid approach - sync if connected, warn if not |
| **Tests** | 34 unit tests passing |
