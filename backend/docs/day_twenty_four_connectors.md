# Day 24: Unified Connector System

> **Date**: January 6, 2026  
> **Focus**: OAuth integrations for Calendar, Email, CRM, and Drive providers  
> **Status**: Implementation Complete

---

## Overview

Today we implemented a unified connector system that enables Talky.ai to integrate with external services through OAuth 2.0. This system follows the factory pattern established by our telephony providers and provides a secure, extensible framework for connecting Google Calendar, Gmail, HubSpot CRM, and Google Drive.

### Key Features

- ✅ **Factory Pattern** - `ConnectorFactory` for consistent provider instantiation
- ✅ **OAuth 2.0 with PKCE** - Secure authorization using Proof Key for Code Exchange
- ✅ **Token Encryption** - Fernet (AES-128-CBC + HMAC) with key rotation support
- ✅ **Multi-Tenant Isolation** - Strict tenant binding on all operations
- ✅ **Provider Implementations** - Google Calendar, Gmail, HubSpot, Google Drive
- ✅ **REST API** - Complete CRUD operations for connector management
- ✅ **Comprehensive Tests** - Unit and integration test coverage

---

## Architecture

### Directory Structure

```
backend/app/infrastructure/connectors/
├── __init__.py              # Package exports
├── base.py                  # BaseConnector ABC + ConnectorFactory
├── encryption.py            # TokenEncryptionService (Fernet)
├── oauth.py                 # OAuthStateManager (PKCE + Redis)
│
├── calendar/
│   ├── __init__.py
│   ├── base.py              # CalendarProvider ABC
│   └── google_calendar.py   # Google Calendar implementation
│
├── email/
│   ├── __init__.py
│   ├── base.py              # EmailProvider ABC
│   └── gmail.py             # Gmail implementation
│
├── crm/
│   ├── __init__.py
│   ├── base.py              # CRMProvider ABC
│   └── hubspot.py           # HubSpot implementation
│
└── drive/
    ├── __init__.py
    ├── base.py              # DriveProvider ABC
    └── google_drive.py      # Google Drive implementation
```

### Flow Diagram

```
┌──────────┐     ┌─────────────────┐     ┌────────────────┐     ┌──────────┐
│ Frontend │────▶│ POST /authorize │────▶│ Provider OAuth │────▶│ Callback │
│          │     │ Generate state  │     │ Consent Screen │     │ /callback│
└──────────┘     │ + PKCE verifier │     └────────────────┘     └────┬─────┘
                 └─────────────────┘                                  │
                                                                       ▼
┌──────────┐     ┌─────────────────┐     ┌────────────────┐     ┌──────────┐
│ Success  │◀────│ Store encrypted │◀────│ Exchange code  │◀────│ Validate │
│ Redirect │     │ tokens in DB    │     │ for tokens     │     │ state    │
└──────────┘     └─────────────────┘     └────────────────┘     └──────────┘
```

---

## Security Implementation

### Token Encryption

| Component | Technology | Purpose |
|-----------|------------|---------|
| Algorithm | Fernet (AES-128-CBC + HMAC-SHA256) | Authenticated encryption |
| Key Storage | `CONNECTOR_ENCRYPTION_KEY` env var | Never in code/DB |
| Key Rotation | MultiFernet | Decrypt old, encrypt new |
| Location | `connector_accounts.access_token_encrypted` | At-rest protection |

```python
# Example usage
from app.infrastructure.connectors.encryption import get_encryption_service

encryption = get_encryption_service()
encrypted = encryption.encrypt("access_token_value")
decrypted = encryption.decrypt(encrypted)
```

### OAuth Security (PKCE)

| Measure | Implementation |
|---------|----------------|
| State Parameter | Random UUID stored in Redis (5 min TTL) |
| Code Verifier | 64 bytes URL-safe random string |
| Code Challenge | SHA256(verifier), base64url encoded |
| Tenant Binding | Validated on callback |

```python
# OAuth state flow
manager = get_oauth_state_manager()

# Create (on authorize)
state_data = await manager.create_state(
    tenant_id=tenant_id,
    user_id=user_id,
    provider="google_calendar",
    redirect_uri=redirect_uri
)
# Returns: {state, code_verifier, code_challenge}

# Validate (on callback)
validated = await manager.validate_state(state, expected_tenant_id)
# Returns stored data including code_verifier
```

---

## API Endpoints

### Base URL: `/api/v1/connectors`

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/providers` | List available providers | No |
| GET | `/` | List tenant's connectors | Yes |
| GET | `/{id}` | Get connector details | Yes |
| POST | `/authorize` | Start OAuth flow | Yes |
| GET | `/callback` | OAuth callback (redirect) | No |
| DELETE | `/{id}` | Disconnect connector | Yes |
| POST | `/{id}/refresh` | Force token refresh | Yes |

### Request/Response Examples

**POST /authorize**
```json
// Request
{
  "type": "calendar",
  "provider": "google_calendar",
  "name": "My Work Calendar"
}

// Response
{
  "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "state": "abc123-uuid"
}
```

**GET /providers**
```json
[
  {
    "provider": "google_calendar",
    "type": "calendar",
    "name": "Google Calendar",
    "description": "Connect your Google Calendar to book meetings",
    "requires_oauth": true
  },
  {
    "provider": "gmail",
    "type": "email",
    "name": "Gmail",
    "description": "Send emails using your Gmail account",
    "requires_oauth": true
  }
]
```

---

## Provider Implementations

### Google Calendar

| Capability | Method | Description |
|------------|--------|-------------|
| Create Event | `create_event()` | With Google Meet support |
| Update Event | `update_event()` | Modify any field |
| Delete Event | `delete_event()` | Cancel meeting |
| List Events | `list_events()` | Time range query |
| Get Availability | `get_availability()` | Find free slots |

**Scopes**: `calendar`, `calendar.events`

### Gmail

| Capability | Method | Description |
|------------|--------|-------------|
| Send Email | `send_email()` | Plain text + HTML |
| Get Email | `get_email()` | Single message |
| List Emails | `list_emails()` | With search/filter |

**Scopes**: `gmail.send`, `gmail.readonly`

### HubSpot CRM

| Capability | Method | Description |
|------------|--------|-------------|
| Create Contact | `create_contact()` | Add to CRM |
| Update Contact | `update_contact()` | Modify properties |
| Get Contact | `get_contact()` | By ID |
| List Contacts | `list_contacts()` | With search |
| Find by Email | `find_contact_by_email()` | Email lookup |
| Create Deal | `create_deal()` | New opportunity |

**Scopes**: `crm.objects.contacts.read/write`, `crm.objects.deals.read/write`

### Google Drive

| Capability | Method | Description |
|------------|--------|-------------|
| Upload File | `upload_file()` | Multipart upload |
| Download File | `download_file()` | Get content |
| List Files | `list_files()` | Folder contents |
| Create Folder | `create_folder()` | New folder |
| Delete File | `delete_file()` | Remove item |

**Scopes**: `drive.file`, `drive.readonly`

---

## Environment Variables

### Required

```bash
# Token Encryption (generate with TokenEncryptionService.generate_key())
CONNECTOR_ENCRYPTION_KEY=your-32-byte-fernet-key

# Google OAuth (Calendar, Gmail, Drive)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# HubSpot OAuth
HUBSPOT_CLIENT_ID=your-hubspot-client-id
HUBSPOT_CLIENT_SECRET=your-hubspot-client-secret

# Callback Configuration
API_BASE_URL=https://api.talky.ai  # For OAuth redirect
FRONTEND_URL=http://localhost:3000  # Success/error redirect
```

### Optional (Key Rotation)

```bash
# Comma-separated old keys for rotation
CONNECTOR_ENCRYPTION_KEYS_OLD=old-key-1,old-key-2
```

---

## Test Coverage

### Unit Tests

| File | Tests | Coverage |
|------|-------|----------|
| `test_connector_encryption.py` | 9 tests | Encryption, rotation, error handling |
| `test_connector_factory.py` | 10 tests | Factory, providers, validation |
| `test_oauth_state.py` | 9 tests | PKCE, state management, tenant binding |

### Integration Tests

| File | Tests | Coverage |
|------|-------|----------|
| `test_connectors_api.py` | 10 tests | API endpoints, auth, security |

### Run Tests

```bash
# All connector tests
pytest tests/unit/test_connector_*.py tests/unit/test_oauth_state.py -v

# Integration tests
pytest tests/integration/test_connectors_api.py -v
```

---

## Files Created/Modified

### New Files (16)

| Path | Description |
|------|-------------|
| `infrastructure/connectors/__init__.py` | Package exports |
| `infrastructure/connectors/base.py` | Base classes + factory |
| `infrastructure/connectors/encryption.py` | Token encryption |
| `infrastructure/connectors/oauth.py` | OAuth state manager |
| `infrastructure/connectors/calendar/__init__.py` | Calendar package |
| `infrastructure/connectors/calendar/base.py` | Calendar ABC |
| `infrastructure/connectors/calendar/google_calendar.py` | Google Calendar |
| `infrastructure/connectors/email/__init__.py` | Email package |
| `infrastructure/connectors/email/base.py` | Email ABC |
| `infrastructure/connectors/email/gmail.py` | Gmail |
| `infrastructure/connectors/crm/__init__.py` | CRM package |
| `infrastructure/connectors/crm/base.py` | CRM ABC |
| `infrastructure/connectors/crm/hubspot.py` | HubSpot |
| `infrastructure/connectors/drive/__init__.py` | Drive package |
| `infrastructure/connectors/drive/base.py` | Drive ABC |
| `infrastructure/connectors/drive/google_drive.py` | Google Drive |
| `api/v1/endpoints/connectors.py` | REST API |

### Modified Files (1)

| Path | Change |
|------|--------|
| `api/v1/routes.py` | Added connectors router |

### Test Files (4)

| Path | Description |
|------|-------------|
| `tests/unit/test_connector_encryption.py` | Encryption tests |
| `tests/unit/test_connector_factory.py` | Factory tests |
| `tests/unit/test_oauth_state.py` | OAuth tests |
| `tests/integration/test_connectors_api.py` | API tests |

---

## Clarifications Needed (Post-Testing)

> [!IMPORTANT]
> The following items need to be clarified after testing in a live environment:

### 1. OAuth Redirect Domain
- **Question**: What is the production callback URL?
- **Current**: Uses `API_BASE_URL` env var or request base URL
- **Example**: `https://api.talky.ai/api/v1/connectors/callback`

### 2. Token Refresh Strategy
- **Question**: Proactive refresh (15 min before expiry) or on-demand?
- **Current**: On-demand via `/connectors/{id}/refresh` endpoint
- **Recommendation**: Add background job for proactive refresh

### 3. Google Cloud Project
- **Question**: Which Google Cloud project for OAuth credentials?
- **Required APIs**: Calendar API, Gmail API, Drive API
- **Consent Screen**: Needs verification for production

### 4. HubSpot App Type
- **Question**: Public or private HubSpot app?
- **Current**: Assumes public app with OAuth
- **Alternative**: Private app with API key (no OAuth)

### 5. Encryption Key Management
- **Question**: How will the Fernet key be managed in production?
- **Options**: 
  - AWS Secrets Manager
  - HashiCorp Vault
  - Kubernetes Secrets (encrypted at rest)
- **Backup**: Key loss = tokens unrecoverable

### 6. Error Handling for Revoked Tokens
- **Question**: How to handle when user revokes OAuth access?
- **Current**: Token refresh fails, status stays "active"
- **Recommendation**: Webhook or periodic validation

---

## Next Steps

### Phase 1: Testing
- [ ] Set up Google Cloud OAuth credentials
- [ ] Set up HubSpot developer app
- [ ] Generate encryption key
- [ ] Run unit tests
- [ ] Test OAuth flow end-to-end

### Phase 2: Assistant Integration
- [ ] Add `send_email_via_connector` tool to assistant
- [ ] Add `book_meeting_via_connector` tool
- [ ] Add `create_crm_contact` tool
- [ ] Update assistant tools.py

### Phase 3: Frontend
- [ ] Build integrations settings page
- [ ] Add connector status indicators
- [ ] Handle OAuth redirect flow

### Phase 4: Production
- [ ] Set up production OAuth apps
- [ ] Configure key management
- [ ] Add monitoring/alerting
- [ ] Implement token refresh job

---

## Dependencies

### Python Packages (Already Present)

```
httpx          # HTTP client for API calls
cryptography   # Fernet encryption
redis          # OAuth state storage
pydantic       # Data models
```

### External Services

| Service | Purpose | Setup Required |
|---------|---------|----------------|
| Redis | OAuth state storage | Already configured |
| Google Cloud | Calendar, Gmail, Drive APIs | OAuth credentials needed |
| HubSpot | CRM API | Developer app needed |

---

## Usage Example

### Frontend Integration Flow

```javascript
// 1. Start OAuth flow
const response = await fetch('/api/v1/connectors/authorize', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    type: 'calendar',
    provider: 'google_calendar',
    name: 'Work Calendar'
  })
});

const { authorization_url } = await response.json();

// 2. Redirect to OAuth provider
window.location.href = authorization_url;

// 3. User returns to /settings/integrations?success=true&provider=google_calendar
```

### Assistant Tool Usage

```python
# Inside assistant tools.py
async def book_meeting_via_connector(
    tenant_id: str,
    title: str,
    start_time: datetime,
    duration_minutes: int,
    attendees: List[str],
    supabase: Client
) -> Dict[str, Any]:
    """Book a meeting using connected calendar."""
    
    # Get active calendar connector
    conn = await get_active_connector(tenant_id, "calendar", supabase)
    if not conn:
        return {"success": False, "error": "No calendar connected"}
    
    # Get decrypted token
    token = await get_decrypted_token(conn["id"], supabase)
    
    # Create connector and book meeting
    connector = ConnectorFactory.create(
        provider=conn["provider"],
        tenant_id=tenant_id,
        connector_id=conn["id"]
    )
    await connector.set_access_token(token)
    
    event = await connector.create_event(
        title=title,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=duration_minutes),
        attendees=attendees,
        add_video_conference=True
    )
    
    return {
        "success": True,
        "event_id": event.id,
        "video_link": event.video_link
    }
```

---

## Summary

Day 24 delivered a complete, secure, and extensible connector system for Talky.ai. The implementation follows established patterns (factory, ABC), applies industry-standard security (OAuth 2.0 + PKCE, Fernet encryption), and provides comprehensive API coverage for managing external integrations.

The system is ready for testing and will enable the Assistant Agent to perform real actions like booking meetings, sending emails, and syncing with CRM systems.

---

## Update: January 8, 2026 - Frontend Authentication Protection & Build Fixes

### Issue 1: Unauthenticated Dashboard Access
Unauthenticated users could view partial dashboard content before being redirected.

### Issue 2: Next.js 16 Pre-rendering Errors
Pages using `useSearchParams` hook were failing to pre-render due to missing Suspense boundaries.

### Fixes Applied

#### 1. Created `ProtectedRoute` Component
**File:** `frontend/src/components/auth/protected-route.tsx` (NEW)
- Checks authentication status using `useAuth()` hook
- Shows loading spinner while checking auth
- Redirects to `/auth/login` if user is not authenticated
- Only renders children when user is confirmed authenticated

#### 2. Updated `DashboardLayout`
**File:** `frontend/src/components/layout/dashboard-layout.tsx` (MODIFIED)
- Wrapped with `<ProtectedRoute>` component
- All pages using `DashboardLayout` are now automatically protected

#### 3. Fixed Pre-rendering Errors with Suspense Boundaries
The following files were updated to wrap `useSearchParams` usage in Suspense:

| File | Issue | Fix |
|------|-------|-----|
| `app/auth/callback/page.tsx` | Pre-render failure | Added Suspense with LoadingFallback |
| `app/billing/success/page.tsx` | Pre-render failure | Added Suspense with LoadingFallback |
| `app/integrations/page.tsx` | Pre-render failure | Added Suspense with LoadingFallback |
| `app/settings/integrations/page.tsx` | Pre-render failure | Added Suspense with LoadingFallback |

#### 4. Fixed TypeScript Error
**File:** `components/ui/health-stat-card.tsx` (MODIFIED)
- Fixed framer-motion `Variants` type error by using `as const` for transition type

### Behavior After Fix
- **Authenticated users:** Normal dashboard access
- **Unauthenticated users:** Immediately redirected to login page
- **Loading state:** Shows spinner with "Loading..." message
- **Build:** Successful with all static pages pre-rendered
- The `FloatingAssistant` component already returns `null` for unauthenticated users

### Build Output Verification
```
✓ Compiled successfully in 11.3s
✓ Finished TypeScript in 18.1s
✓ Generating static pages (19/19)
```

### Files Modified Summary
| File | Change Type |
|------|-------------|
| `frontend/src/components/auth/protected-route.tsx` | NEW |
| `frontend/src/components/layout/dashboard-layout.tsx` | MODIFIED |
| `frontend/src/app/auth/callback/page.tsx` | MODIFIED |
| `frontend/src/app/billing/success/page.tsx` | MODIFIED |
| `frontend/src/app/integrations/page.tsx` | MODIFIED |
| `frontend/src/app/settings/integrations/page.tsx` | MODIFIED |
| `frontend/src/components/ui/health-stat-card.tsx` | MODIFIED |


