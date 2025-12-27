# Day 7: Database Schema, CRUD API & Data Persistence

## Overview

**Date:** Week 2, Day 7  
**Goal:** Design the database schema, implement CRUD endpoints for campaigns/leads/calls, and integrate with Supabase for data persistence.

This document covers the PostgreSQL schema design, Supabase integration, RESTful API endpoints, and authentication dependencies.

---

## Table of Contents

1. [Database Schema Design](#1-database-schema-design)
2. [Supabase Integration](#2-supabase-integration)
3. [Campaign API Endpoints](#3-campaign-api-endpoints)
4. [Leads and Calls API](#4-leads-and-calls-api)
5. [Authentication & Dependencies](#5-authentication--dependencies)
6. [Test Results & Verification](#6-test-results--verification)
7. [Rationale Summary](#7-rationale-summary)

---

## 1. Database Schema Design

### 1.1 Entity Relationship Diagram

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    campaigns    │     │      leads      │     │      calls      │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ id (PK, UUID)   │────<│ campaign_id (FK)│     │ campaign_id (FK)│
│ tenant_id       │     │ id (PK, UUID)   │────<│ lead_id (FK)    │
│ name            │     │ tenant_id       │     │ id (PK, UUID)   │
│ description     │     │ phone_number    │     │ tenant_id       │
│ status          │     │ first_name      │     │ phone_number    │
│ system_prompt   │     │ last_name       │     │ status          │
│ voice_id        │     │ email           │     │ duration_seconds│
│ max_concurrent  │     │ custom_fields   │     │ recording_url   │
│ max_retries     │     │ status          │     │ transcript      │
│ created_at      │     │ call_attempts   │     │ created_at      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        │
                                                ┌───────┴───────┐
                                                │ conversations │
                                                ├───────────────┤
                                                │ call_id (FK)  │
                                                │ id (PK, UUID) │
                                                │ messages JSONB│
                                                │ status        │
                                                └───────────────┘
```

### 1.2 Campaigns Table

**File: `database/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    system_prompt TEXT NOT NULL,
    voice_id VARCHAR(100) NOT NULL,
    max_concurrent_calls INTEGER DEFAULT 10,
    retry_failed BOOLEAN DEFAULT true,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    total_leads INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_id ON campaigns(tenant_id);
```

### 1.3 Leads Table

```sql
CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(255),
    custom_fields JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_called_at TIMESTAMP WITH TIME ZONE,
    call_attempts INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'pending',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone_number ON leads(phone_number);
```

### 1.4 Calls Table

```sql
CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(255) NOT NULL,
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone_number VARCHAR(20) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'initiated',
    started_at TIMESTAMP WITH TIME ZONE,
    answered_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcript TEXT,
    summary TEXT,
    cost DECIMAL(10, 4),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_campaign_id ON calls(campaign_id);
CREATE INDEX IF NOT EXISTS idx_calls_lead_id ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
```

### 1.5 Auto-Update Timestamps Trigger

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_campaigns_updated_at 
    BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_leads_updated_at 
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_calls_updated_at 
    BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

---

## 2. Supabase Integration

### 2.1 Client Dependency

**File: `app/api/v1/dependencies.py`**

```python
def get_supabase() -> Client:
    """Get Supabase client with validation."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url:
        raise RuntimeError("SUPABASE_URL is not configured")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_KEY is not configured")
    
    return create_client(url, key)
```

### 2.2 Query Patterns

```python
# SELECT with filters and pagination
response = supabase.table("campaigns")\
    .select("*", count="exact")\
    .eq("status", "active")\
    .order("created_at", desc=True)\
    .range(offset, offset + page_size - 1)\
    .execute()

# INSERT
response = supabase.table("campaigns")\
    .insert(campaign_data)\
    .execute()

# UPDATE
response = supabase.table("campaigns")\
    .update({"status": "running"})\
    .eq("id", campaign_id)\
    .execute()

# JOIN (via foreign key)
response = supabase.table("leads")\
    .select("*, campaigns(name, status)")\
    .eq("campaign_id", campaign_id)\
    .execute()
```

---

## 3. Campaign API Endpoints

### 3.1 List Campaigns

**File: `app/api/v1/endpoints/campaigns.py`**

```python
@router.get("/")
async def list_campaigns(
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """List all campaigns ordered by creation date."""
    response = supabase.table("campaigns")\
        .select("*")\
        .order("created_at", desc=True)\
        .execute()
    return {"campaigns": response.data}
```

### 3.2 Create Campaign

```python
@router.post("/")
async def create_campaign(
    campaign_data: dict,
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """Create a new campaign."""
    response = supabase.table("campaigns")\
        .insert(campaign_data)\
        .execute()
    return {"campaign": response.data[0] if response.data else None}
```

### 3.3 Get Campaign Details

```python
@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    request: Request,
    supabase: Client = Depends(get_supabase)
):
    """Get campaign details by ID."""
    response = supabase.table("campaigns")\
        .select("*")\
        .eq("id", campaign_id)\
        .execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": response.data[0]}
```

### 3.4 Start Campaign

```python
@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    supabase: Client = Depends(get_supabase)
):
    """
    Start campaign - enqueue leads as dialer jobs.
    
    1. Validate campaign exists
    2. Get pending leads
    3. Create DialerJob for each lead
    4. Enqueue to Redis
    5. Update campaign status
    """
    # Get pending leads
    leads = supabase.table("leads")\
        .select("*")\
        .eq("campaign_id", campaign_id)\
        .eq("status", "pending")\
        .execute()
    
    # Update campaign status
    supabase.table("campaigns").update({
        "status": "running",
        "started_at": datetime.utcnow().isoformat()
    }).eq("id", campaign_id).execute()
    
    return {"message": f"Campaign started", "jobs_enqueued": len(leads.data)}
```

### 3.5 Campaign Statistics

```python
@router.get("/{campaign_id}/stats")
async def get_campaign_stats(
    campaign_id: str,
    supabase: Client = Depends(get_supabase)
):
    """Get campaign statistics."""
    # Get job counts by status
    jobs = supabase.table("dialer_jobs")\
        .select("status")\
        .eq("campaign_id", campaign_id)\
        .execute()
    
    status_counts = {}
    for job in jobs.data or []:
        status = job.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return {
        "campaign_id": campaign_id,
        "job_status_counts": status_counts
    }
```

---

## 4. Leads and Calls API

### 4.1 Add Contact to Campaign

```python
@router.post("/{campaign_id}/contacts")
async def add_contact_to_campaign(
    campaign_id: str,
    contact: ContactCreate,
    supabase: Client = Depends(get_supabase)
):
    """Add a single contact to a campaign."""
    
    # Normalize phone number
    normalized_phone = normalize_phone_number(contact.phone_number)
    
    # Check for duplicate
    existing = supabase.table("leads")\
        .select("id")\
        .eq("campaign_id", campaign_id)\
        .eq("phone_number", normalized_phone)\
        .execute()
    
    if existing.data:
        raise HTTPException(status_code=409, detail="Phone already exists")
    
    # Create lead
    lead_data = {
        "id": str(uuid.uuid4()),
        "campaign_id": campaign_id,
        "phone_number": normalized_phone,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "status": "pending"
    }
    
    response = supabase.table("leads").insert(lead_data).execute()
    return {"contact": response.data[0]}
```

### 4.2 List Calls with Pagination

**File: `app/api/v1/endpoints/calls.py`**

```python
@router.get("/", response_model=CallListResponse)
async def list_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    supabase: Client = Depends(get_supabase)
):
    """Get paginated list of calls."""
    
    query = supabase.table("calls").select(
        "id, created_at, phone_number, status, duration_seconds, outcome",
        count="exact"
    )
    
    if status:
        query = query.eq("status", status)
    
    offset = (page - 1) * page_size
    response = query.order("created_at", desc=True)\
        .range(offset, offset + page_size - 1)\
        .execute()
    
    return CallListResponse(
        items=response.data,
        page=page,
        page_size=page_size,
        total=response.count
    )
```

### 4.3 Get Call Details

```python
@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    supabase: Client = Depends(get_supabase)
):
    """Get individual call details with transcript and recording."""
    
    call_response = supabase.table("calls")\
        .select("*")\
        .eq("id", call_id)\
        .single()\
        .execute()
    
    if not call_response.data:
        raise HTTPException(status_code=404, detail="Call not found")
    
    # Get recording if exists
    recording = supabase.table("recordings")\
        .select("id")\
        .eq("call_id", call_id)\
        .execute()
    
    return CallDetail(
        id=call_response.data["id"],
        transcript=call_response.data.get("transcript"),
        recording_id=recording.data[0]["id"] if recording.data else None
    )
```

---

## 5. Authentication & Dependencies

### 5.1 Current User Model

```python
class CurrentUser(BaseModel):
    """Current authenticated user model"""
    id: str
    email: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    tenant_id: Optional[str] = None
    role: str = "user"
    minutes_remaining: int = 0
```

### 5.2 JWT Token Authentication

```python
async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    supabase: Client = Depends(get_supabase)
) -> CurrentUser:
    """Get current user from JWT token."""
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    # Extract token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid format")
    
    token = parts[1]
    
    # Verify with Supabase
    user_response = supabase.auth.get_user(token)
    
    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    auth_user = user_response.user
    
    # Get user profile
    profile = supabase.table("user_profiles")\
        .select("*, tenants(business_name)")\
        .eq("id", auth_user.id)\
        .single()\
        .execute()
    
    return CurrentUser(
        id=str(auth_user.id),
        email=auth_user.email,
        tenant_id=profile.data.get("tenant_id") if profile.data else None
    )
```

### 5.3 Admin Authorization

```python
async def require_admin(
    current_user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """Require admin role."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
```

---

## 6. Test Results & Verification

### 6.1 Database Schema Verification

```sql
-- Verify tables created
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public';

-- Results:
-- campaigns
-- leads  
-- calls
-- conversations
```

### 6.2 API Endpoint Tests

```
tests/integration/test_api_endpoints.py

TestCampaignAPI
  test_list_campaigns PASSED
  test_create_campaign PASSED
  test_get_campaign PASSED
  test_get_campaign_not_found PASSED (404)
  test_start_campaign PASSED
  test_pause_campaign PASSED
  test_stop_campaign PASSED
  test_campaign_stats PASSED

TestContactAPI
  test_add_contact PASSED
  test_add_contact_duplicate PASSED (409)
  test_list_contacts_paginated PASSED
  test_remove_contact PASSED

TestCallAPI
  test_list_calls PASSED
  test_list_calls_filtered PASSED
  test_get_call_details PASSED
  test_get_call_transcript PASSED

==================== 16 passed in 2.1s ====================
```

### 6.3 Sample API Responses

```json
// GET /api/v1/campaigns
{
  "campaigns": [
    {
      "id": "11111111-1111-1111-1111-111111111111",
      "name": "Test Sales Campaign",
      "status": "draft",
      "total_leads": 50,
      "created_at": "2024-12-12T10:00:00Z"
    }
  ]
}

// GET /api/v1/calls?page=1&page_size=20
{
  "items": [...],
  "page": 1,
  "page_size": 20,
  "total": 150
}
```

---

## 7. Rationale Summary

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | PostgreSQL (Supabase) | Built-in auth, real-time, hosting |
| Primary Keys | UUID | Distributed ID generation, no collisions |
| Timestamps | WITH TIME ZONE | Correct handling across timezones |
| Soft Delete | Status field | Preserve data for analytics |
| Phone Format | E.164 | International standard, unique indexing |

### API Design Patterns

| Pattern | Implementation | Benefit |
|---------|----------------|---------|
| Pagination | page/page_size params | Handles large datasets |
| Filtering | Query parameters | Flexible data access |
| Dependency Injection | FastAPI Depends | Testable, reusable |
| Error Handling | HTTPException | Consistent error responses |

### Files Created/Modified

| File | Purpose |
|------|---------|
| `database/schema.sql` | Core database schema |
| `app/api/v1/dependencies.py` | Supabase client, auth |
| `app/api/v1/endpoints/campaigns.py` | Campaign CRUD |
| `app/api/v1/endpoints/calls.py` | Call history API |
| `app/api/v1/endpoints/contacts.py` | Contact management |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| SUPABASE_URL | Database connection URL |
| SUPABASE_SERVICE_KEY | Service role key (server-side) |
| SUPABASE_ANON_KEY | Public key (client-side) |

---

*Document Version: 1.0*  
*Last Updated: Day 7 of Development Sprint*
