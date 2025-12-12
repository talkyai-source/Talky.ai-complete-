# Talky.ai Backend Documentation - Part 2
# API Endpoints Reference

## Table of Contents
1. [API Architecture](#api-architecture)
2. [Authentication Endpoints](#authentication-endpoints)
3. [Campaign Endpoints](#campaign-endpoints)
4. [Contact Management Endpoints](#contact-management-endpoints)
5. [Webhook Endpoints](#webhook-endpoints)
6. [Dashboard & Analytics](#dashboard--analytics)
7. [Other Endpoints](#other-endpoints)

---

## API Architecture

### Base Configuration

**File:** `app/api/v1/routes.py` (39 lines)

The API uses FastAPI's router system to organize endpoints. All routers are combined into a single `api_router` which is mounted at `/api/v1`.

```python
# From routes.py - Router Registration Order
api_router = APIRouter()

# Core routers
api_router.include_router(campaigns.router)    # /campaigns
api_router.include_router(webhooks.router)     # /webhooks
api_router.include_router(websockets.router)   # WebSocket endpoints

# Frontend alignment routers
api_router.include_router(auth.router)         # /auth
api_router.include_router(plans.router)        # /plans
api_router.include_router(dashboard.router)    # /dashboard
api_router.include_router(analytics.router)    # /analytics
api_router.include_router(calls.router)        # /calls
api_router.include_router(recordings.router)   # /recordings
api_router.include_router(contacts.router)     # /contacts
api_router.include_router(clients.router)      # /clients
api_router.include_router(admin.router)        # /admin
```

### Dependency Injection

**File:** `app/api/v1/dependencies.py` (211 lines)

All endpoints use dependency injection for:
- **Supabase Client:** `get_supabase()` - Returns authenticated Supabase client
- **Current User:** `get_current_user()` - Extracts JWT and returns user info
- **Admin Check:** `require_admin()` - Requires admin role
- **Optional User:** `get_optional_user()` - Returns user or None

```python
# CurrentUser Model (from dependencies.py)
class CurrentUser(BaseModel):
    id: str                           # User UUID from Supabase Auth
    email: str                        # User email
    name: Optional[str] = None        # Display name
    business_name: Optional[str]      # Tenant business name
    tenant_id: Optional[str] = None   # Multi-tenant ID
    role: str = "user"                # user, owner, admin
    minutes_remaining: int = 0        # Calling minutes balance
```

---

## Authentication Endpoints

**File:** `app/api/v1/endpoints/auth.py` (291 lines)

Uses Supabase magic link (passwordless) authentication.

### POST /auth/register

**Purpose:** Register a new user with magic link

**Request Body:**
```json
{
    "email": "user@example.com",
    "business_name": "Acme Corp",
    "plan_id": "basic",
    "name": "John Doe"
}
```

**Process:**
1. Validates plan exists in database
2. Checks if email already registered
3. Creates tenant with plan's allocated minutes
4. Sends magic link via Supabase Auth
5. User metadata stored for profile creation on first login

**Response:**
```json
{
    "id": "tenant-uuid",
    "email": "user@example.com",
    "business_name": "Acme Corp",
    "role": "owner",
    "minutes_remaining": 100,
    "message": "Magic link sent to your email. Please check your inbox."
}
```

### POST /auth/login

**Purpose:** Login with magic link

**Request Body:**
```json
{
    "email": "user@example.com"
}
```

**Process:**
1. Checks if user profile exists
2. Sends magic link regardless (security - don't reveal if email exists)
3. Returns generic success message

### GET /auth/me

**Purpose:** Get current authenticated user info

**Requires:** Bearer token in Authorization header

**Response:**
```json
{
    "id": "user-uuid",
    "email": "user@example.com",
    "name": "John Doe",
    "business_name": "Acme Corp",
    "role": "owner",
    "minutes_remaining": 95
}
```

**Used By:**
- Frontend AuthContext on app load
- DashboardLayout top bar

### POST /auth/logout

**Purpose:** Logout current user

**Process:**
1. Calls `supabase.auth.sign_out()`
2. Client clears stored token

### POST /auth/create-profile

**Purpose:** Create user profile after first magic link login

**Process:**
1. Checks if profile already exists
2. Gets user metadata from auth (tenant_id, name from registration)
3. Creates user_profiles record linking to tenant

---

## Campaign Endpoints

**File:** `app/api/v1/endpoints/campaigns.py` (644 lines)

Campaign management with dialer integration.

### GET /campaigns/

**Purpose:** List all campaigns

**Response:**
```json
{
    "campaigns": [
        {
            "id": "uuid",
            "name": "Winter Campaign",
            "status": "running",
            "total_leads": 500,
            "calls_completed": 120,
            "calls_failed": 15,
            "created_at": "2025-12-10T10:00:00Z"
        }
    ]
}
```

### POST /campaigns/

**Purpose:** Create new campaign

**Request Body:**
```json
{
    "name": "Winter Campaign",
    "description": "Winter sales outreach",
    "system_prompt": "You are a friendly sales assistant...",
    "voice_id": "sonic-professional",
    "max_concurrent_calls": 10,
    "max_retries": 3,
    "goal": "Book appointment",
    "script_config": {
        "agent_name": "Alex",
        "greeting": "Hi, this is Alex from Acme Corp..."
    },
    "calling_config": {
        "time_window_start": "09:00",
        "time_window_end": "17:00",
        "timezone": "America/New_York"
    }
}
```

### GET /campaigns/{campaign_id}

**Purpose:** Get campaign details

**Response:** Full campaign object with all fields

### POST /campaigns/{campaign_id}/start

**Purpose:** Start campaign - enqueue all pending leads as dialer jobs

**Request Body (Optional):**
```json
{
    "priority_override": 8,
    "tenant_id": "tenant-uuid"
}
```

**Process:**
1. Validates campaign exists and is not already running
2. Fetches all leads with `status='pending'`
3. For each lead:
   - Calculates priority (base + high_value boost + urgent tags)
   - Creates DialerJob object
   - Enqueues to Redis (priority queue if >= 8, else tenant queue)
4. Stores jobs in database
5. Updates campaign status to 'running'

**Priority Calculation:**
```python
# From campaigns.py lines 191-206
base_priority = priority_override or lead.get("priority", 5)

if lead.get("is_high_value"):
    base_priority = min(base_priority + 2, 10)

if "urgent" in lead_tags or "appointment" in lead_tags:
    base_priority = min(base_priority + 1, 10)
```

### POST /campaigns/{campaign_id}/pause

**Purpose:** Pause running campaign

### POST /campaigns/{campaign_id}/stop

**Purpose:** Stop campaign completely

### GET /campaigns/{campaign_id}/jobs

**Purpose:** Get dialer jobs for campaign

### GET /campaigns/{campaign_id}/stats

**Purpose:** Get campaign statistics

**Response:**
```json
{
    "campaign_id": "uuid",
    "campaign_status": "running",
    "total_leads": 500,
    "job_status_counts": {
        "pending": 200,
        "completed": 150,
        "failed": 50,
        "retry_scheduled": 30
    },
    "call_outcome_counts": {
        "answered": 100,
        "no_answer": 40,
        "busy": 10
    },
    "goals_achieved": 25
}
```

---

## Contact Management Endpoints

### Campaign-Scoped Contact Endpoints (campaigns.py)

#### POST /campaigns/{campaign_id}/contacts

**Purpose:** Add single contact to campaign

**Request Body:**
```json
{
    "phone_number": "(555) 123-4567",
    "first_name": "John",
    "last_name": "Doe",
    "email": "john@example.com",
    "custom_fields": {
        "company": "Acme Inc"
    }
}
```

**Phone Validation:**
```python
# From campaigns.py - normalize_phone_number function
# Converts various formats to E.164:
#   (555) 123-4567  → +15551234567
#   555.123.4567    → +15551234567
#   +44 20 7946 0958 → +442079460958
# Validates:
#   - Minimum 7 digits
#   - Maximum 15 digits (E.164 max)
```

**Duplicate Check:**
- Queries existing leads in campaign with same phone
- Returns 409 if duplicate exists

#### GET /campaigns/{campaign_id}/contacts

**Purpose:** List contacts for campaign with pagination

**Query Parameters:**
- `page` (int, default=1)
- `page_size` (int, default=50, max=100)
- `status` (string, optional) - Filter by status
- `last_call_result` (string, optional) - Filter by call result

**Response:**
```json
{
    "items": [...],
    "page": 1,
    "page_size": 50,
    "total": 500
}
```

#### DELETE /campaigns/{campaign_id}/contacts/{contact_id}

**Purpose:** Soft-delete contact (sets status='deleted')

### Bulk Import Endpoints (contacts.py)

**File:** `app/api/v1/endpoints/contacts.py` (16,235 bytes)

#### POST /contacts/campaigns/{campaign_id}/upload

**Purpose:** Bulk CSV import with validation

**Request:** Multipart form data with CSV file

**Query Parameters:**
- `skip_duplicates` (bool, default=true)

**CSV Format:**
```csv
phone_number,first_name,last_name,email
+15551234567,John,Doe,john@example.com
555-987-6543,Jane,Smith,jane@example.com
```

**Process:**
1. Validates campaign exists
2. Reads and decodes CSV (handles UTF-8, Latin-1)
3. Fetches existing phone numbers in campaign
4. For each row:
   - Normalizes phone to E.164
   - Checks duplicate in file and campaign
   - Validates required fields
5. Batch inserts leads (chunks of 500)
6. Returns detailed results

**Response:**
```json
{
    "total_rows": 500,
    "imported": 480,
    "failed": 5,
    "duplicates_skipped": 15,
    "errors": [
        {
            "row": 12,
            "error": "Phone number too short",
            "phone": "123"
        }
    ]
}
```

#### POST /contacts/bulk (Legacy)

**Purpose:** Backward-compatible bulk import

**Note:** Preserved for existing integrations, new code should use campaign-scoped endpoint.

---

## Webhook Endpoints

**File:** `app/api/v1/endpoints/webhooks.py` (472 lines)

Handles incoming webhooks from Vonage telephony provider.

### Status Mapping

```python
# From webhooks.py - Vonage to CallOutcome mapping
VONAGE_STATUS_MAP = {
    "started": None,           # Call initiated, not outcome
    "ringing": None,           # Still ringing
    "answered": CallOutcome.ANSWERED,
    "completed": CallOutcome.GOAL_NOT_ACHIEVED,
    "busy": CallOutcome.BUSY,
    "timeout": CallOutcome.NO_ANSWER,
    "failed": CallOutcome.FAILED,
    "rejected": CallOutcome.REJECTED,
    "unanswered": CallOutcome.NO_ANSWER,
    "cancelled": CallOutcome.FAILED,
    "machine": CallOutcome.VOICEMAIL,
}
```

### POST /webhooks/vonage/answer

**Purpose:** Handle call answer webhook from Vonage

**Called When:** Outbound call is initiated

**Process:**
1. Extracts call_uuid, to/from numbers
2. Constructs WebSocket URL for voice processing
3. Returns NCCO (Nexmo Call Control Object)

**NCCO Response:**
```json
[
    {
        "action": "connect",
        "eventUrl": ["https://api/webhooks/vonage/event"],
        "from": "+1234567890",
        "endpoint": [
            {
                "type": "websocket",
                "uri": "wss://host/api/v1/ws/voice/{call_uuid}",
                "content-type": "audio/l16;rate=16000",
                "headers": {
                    "call_uuid": "..."
                }
            }
        ]
    }
]
```

### POST /webhooks/vonage/event

**Purpose:** Handle call status events from Vonage

**Processes:**
- Call status changes (answered, completed, busy, failed, timeout)
- Duration tracking
- Triggers `handle_call_status()`

### handle_call_status() Function

**Purpose:** Update database records when call status changes

**Updates:**
1. **calls table:**
   - status: "completed"
   - outcome: mapped from Vonage status
   - ended_at: timestamp
   - duration_seconds: call duration

2. **leads table:**
   - status: "called"/"contacted"/"completed"/"dnc"
   - last_call_result: outcome value
   - last_called_at: timestamp
   - call_attempts: incremented

3. **dialer_jobs table:**
   - Triggers retry logic if needed

4. **Campaign counters:**
   - Increments calls_completed or calls_failed via RPC

### Retry Logic

```python
# Retryable outcomes (will schedule retry)
RETRYABLE_OUTCOMES = {
    CallOutcome.BUSY,
    CallOutcome.NO_ANSWER,
    CallOutcome.FAILED,
    CallOutcome.VOICEMAIL,
}

# Non-retryable outcomes (permanent failure)
NON_RETRYABLE_OUTCOMES = {
    CallOutcome.SPAM,
    CallOutcome.INVALID,
    CallOutcome.UNAVAILABLE,
    CallOutcome.DISCONNECTED,
    CallOutcome.REJECTED,
    CallOutcome.GOAL_ACHIEVED,
}
```

---

## Dashboard & Analytics

### GET /dashboard/stats

**Purpose:** Get dashboard summary statistics

**Response:**
```json
{
    "total_calls": 1500,
    "successful_calls": 1200,
    "failed_calls": 300,
    "average_duration": 45.5,
    "active_campaigns": 3
}
```

### GET /analytics/calls

**Purpose:** Get call analytics data

### GET /analytics/outcomes

**Purpose:** Get call outcome breakdown

---

## Other Endpoints

### Plans Endpoints

**File:** `app/api/v1/endpoints/plans.py`

- `GET /plans/` - List available plans
- `GET /plans/{plan_id}` - Get plan details

### Calls Endpoints

**File:** `app/api/v1/endpoints/calls.py`

- `GET /calls/` - List calls
- `GET /calls/{call_id}` - Get call details
- `GET /calls/{call_id}/transcript` - Get call transcript

### Recordings Endpoints

**File:** `app/api/v1/endpoints/recordings.py`

- `GET /recordings/` - List recordings
- `GET /recordings/{recording_id}` - Get recording details
- `GET /recordings/{recording_id}/download` - Download audio

### Clients Endpoints

**File:** `app/api/v1/endpoints/clients.py`

- `GET /clients/` - List clients
- `POST /clients/` - Create client
- `GET /clients/{client_id}` - Get client details

### Admin Endpoints

**File:** `app/api/v1/endpoints/admin.py`

- Admin-only operations
- Requires `require_admin` dependency

### Health Endpoint

**File:** `app/api/v1/endpoints/health.py`

- `GET /health` - System health check

---

## WebSocket Endpoints

**File:** `app/api/v1/endpoints/websockets.py` (6,880 bytes)

### WebSocket /ws/voice/{call_uuid}

**Purpose:** Real-time voice AI pipeline connection

**Used For:**
- Receiving audio from Vonage
- Sending AI responses back
- Turn detection and barge-in

**Message Types:**
- Audio frames (PCM 16-bit, 16kHz)
- Control messages (start, end, barge-in)
- Status updates

---

## Error Handling

All endpoints use consistent error response format:

```json
{
    "detail": "Error message here"
}
```

**Standard HTTP Status Codes:**
- 200: Success
- 201: Created
- 400: Bad Request (validation error)
- 401: Unauthorized (missing/invalid token)
- 403: Forbidden (insufficient permissions)
- 404: Not Found
- 409: Conflict (duplicate)
- 500: Internal Server Error

---

## Next File

Continue to **file_three.md** for:
- Domain Models documentation
- Services documentation
- Business logic flow
