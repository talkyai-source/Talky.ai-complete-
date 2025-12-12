# Talky.ai API & Endpoint Report

**Date:** December 9, 2025  
**Version:** 1.0.0  
**Status:** Production Ready

---

## Executive Summary

This document provides a comprehensive reference for all REST API endpoints implemented in the Talky.ai backend. The API is built with FastAPI and uses Supabase for authentication (magic link) and database storage.

**Base URL:** `http://localhost:8000/api/v1`

**Total Endpoints:** 31 routes  
**New Endpoints Added:** 19 routes  
**Authentication:** Supabase Magic Link (JWT Bearer Token)

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Plans & Pricing](#2-plans--pricing)
3. [Dashboard](#3-dashboard)
4. [Analytics](#4-analytics)
5. [Calls](#5-calls)
6. [Recordings](#6-recordings)
7. [Contacts](#7-contacts)
8. [Clients](#8-clients)
9. [Campaigns](#9-campaigns)
10. [Admin](#10-admin)
11. [Webhooks](#11-webhooks)
12. [WebSockets](#12-websockets)
13. [Database Schema](#13-database-schema)
14. [Error Handling](#14-error-handling)
15. [Files Reference](#15-files-reference)

---

## 1. Authentication

**File:** `app/api/v1/endpoints/auth.py`  
**Prefix:** `/api/v1/auth`  
**Dependencies:** `app/api/v1/dependencies.py`

Implements Supabase magic link (passwordless) authentication.

### POST `/auth/register`

Creates a new user account and sends a magic link to the email.

**Request Body:**
```json
{
  "email": "user@example.com",
  "business_name": "My Company",
  "plan_id": "basic",
  "name": "John Doe"
}
```

**Response (200 OK):**
```json
{
  "id": "tenant-uuid",
  "email": "user@example.com",
  "business_name": "My Company",
  "role": "owner",
  "minutes_remaining": 300,
  "message": "Magic link sent to your email. Please check your inbox."
}
```

**Errors:**
| Code | Detail |
|------|--------|
| 400 | Invalid plan_id |
| 400 | Email already registered |
| 500 | Registration failed |

---

### POST `/auth/login`

Sends a magic link to an existing user's email.

**Request Body:**
```json
{
  "email": "user@example.com"
}
```

**Response (200 OK):**
```json
{
  "id": "user-uuid",
  "email": "user@example.com",
  "business_name": "My Company",
  "role": "user",
  "minutes_remaining": 1500,
  "message": "Magic link sent to your email. Please check your inbox."
}
```

---

### GET `/auth/me`

Returns the current authenticated user's information.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "id": "user-uuid",
  "email": "user@example.com",
  "name": "John Doe",
  "business_name": "My Company",
  "role": "user",
  "minutes_remaining": 1500
}
```

**Errors:**
| Code | Detail |
|------|--------|
| 401 | Authorization header missing |
| 401 | Invalid authorization header format |
| 401 | Invalid or expired token |

---

### POST `/auth/logout`

Logs out the current user.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "detail": "Logged out"
}
```

---

### POST `/auth/create-profile`

Creates user profile after first login via magic link.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "detail": "Profile created"
}
```

---

## 2. Plans & Pricing

**File:** `app/api/v1/endpoints/plans.py`  
**Prefix:** `/api/v1/plans`  
**Authentication:** Public (no auth required)

### GET `/plans`

Returns all available pricing plans.

**Response (200 OK):**
```json
[
  {
    "id": "basic",
    "name": "Basic",
    "price": 29.0,
    "description": "Perfect for startups and solo entrepreneurs.",
    "minutes": 300,
    "agents": 1,
    "concurrent_calls": 1,
    "features": ["300 minutes/month", "1 AI agent", "Basic analytics", "Email support"],
    "not_included": ["API access", "Custom voices", "Priority support"],
    "popular": false
  },
  {
    "id": "professional",
    "name": "Professional",
    "price": 79.0,
    "description": "Ideal for growing businesses.",
    "minutes": 1500,
    "agents": 3,
    "concurrent_calls": 3,
    "features": ["1500 minutes/month", "3 AI agents", "Advanced analytics", "Priority support", "Custom voices"],
    "not_included": ["API access", "White-label"],
    "popular": true
  },
  {
    "id": "enterprise",
    "name": "Enterprise",
    "price": 199.0,
    "description": "For large scale operations.",
    "minutes": 5000,
    "agents": 10,
    "concurrent_calls": 10,
    "features": ["5000 minutes/month", "10 AI agents", "Full analytics", "24/7 support", "API access", "White-label"],
    "not_included": [],
    "popular": false
  }
]
```

---

## 3. Dashboard

**File:** `app/api/v1/endpoints/dashboard.py`  
**Prefix:** `/api/v1/dashboard`  
**Authentication:** Required

### GET `/dashboard/summary`

Returns aggregated metrics for the dashboard overview.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "total_calls": 1234,
  "answered_calls": 980,
  "failed_calls": 254,
  "minutes_used": 2300,
  "minutes_remaining": 2700,
  "active_campaigns": 3
}
```

---

## 4. Analytics

**File:** `app/api/v1/endpoints/analytics.py`  
**Prefix:** `/api/v1/analytics`  
**Authentication:** Required

### GET `/analytics/calls`

Returns call analytics with date range and grouping options.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `from` | string | 30 days ago | Start date (YYYY-MM-DD) |
| `to` | string | today | End date (YYYY-MM-DD) |
| `group_by` | string | "day" | Grouping: day, week, month |

**Response (200 OK):**
```json
{
  "series": [
    {
      "date": "2025-01-01",
      "total_calls": 40,
      "answered": 32,
      "failed": 8
    },
    {
      "date": "2025-01-02",
      "total_calls": 55,
      "answered": 48,
      "failed": 7
    }
  ]
}
```

---

## 5. Calls

**File:** `app/api/v1/endpoints/calls.py`  
**Prefix:** `/api/v1/calls`  
**Authentication:** Required

### GET `/calls`

Returns a paginated list of calls.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 20 | Items per page (max 100) |
| `status` | string | null | Filter by call status |
| `from` | string | null | Start date filter (YYYY-MM-DD) |
| `to` | string | null | End date filter (YYYY-MM-DD) |

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": "call_123",
      "timestamp": "2025-01-01T10:00:00Z",
      "to_number": "+1234567890",
      "status": "answered",
      "duration_seconds": 180,
      "outcome": "appointment_booked"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 120
}
```

---

### GET `/calls/{call_id}`

Returns detailed information about a specific call.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `call_id` | string | UUID of the call |

**Response (200 OK):**
```json
{
  "id": "call_123",
  "timestamp": "2025-01-01T10:00:00Z",
  "to_number": "+1234567890",
  "status": "answered",
  "duration_seconds": 180,
  "outcome": "appointment_booked",
  "transcript": "Agent: Hello! Customer: Hi, I'd like to...",
  "recording_id": "rec_456",
  "campaign_id": "campaign_789",
  "lead_id": "lead_012",
  "summary": "Customer interested in product demo."
}
```

**Errors:**
| Code | Detail |
|------|--------|
| 404 | Call not found |

---

## 6. Recordings

**File:** `app/api/v1/endpoints/recordings.py`  
**Prefix:** `/api/v1/recordings`  
**Authentication:** Required

### GET `/recordings`

Returns a paginated list of call recordings.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `call_id` | string | null | Filter by call ID |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page (max 100) |

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": "rec_456",
      "call_id": "call_123",
      "created_at": "2025-01-01T10:00:00Z",
      "duration_seconds": 180
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 10
}
```

---

### GET `/recordings/{recording_id}/stream`

Streams the audio file for playback in HTML5 `<audio>` player.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `recording_id` | string | UUID of the recording |

**Response (200 OK):**
- Content-Type: `audio/wav` (or appropriate MIME type)
- Body: Streamed audio bytes

**Errors:**
| Code | Detail |
|------|--------|
| 404 | Recording not found |
| 404 | Recording file not accessible |

---

## 7. Contacts

**File:** `app/api/v1/endpoints/contacts.py`  
**Prefix:** `/api/v1/contacts`  
**Authentication:** Required

### POST `/contacts/bulk`

Bulk imports contacts from a CSV file.

**Headers:**
```
Authorization: Bearer <jwt-token>
Content-Type: multipart/form-data
```

**Form Data:**
| Field | Type | Description |
|-------|------|-------------|
| `file` | file | CSV file with contacts |
| `campaign_id` | string | Optional: Add as leads to this campaign |

**CSV Format:**
```csv
phone_number,first_name,last_name,email,company
+1234567890,John,Doe,john@example.com,Acme Inc
+1987654321,Jane,Smith,jane@example.com,
```

**Response (200 OK):**
```json
{
  "total_rows": 500,
  "imported": 480,
  "failed": 20,
  "errors": [
    {
      "row": 12,
      "error": "Invalid phone number"
    },
    {
      "row": 45,
      "error": "Missing phone_number"
    }
  ]
}
```

**Errors:**
| Code | Detail |
|------|--------|
| 400 | Only CSV files are supported |
| 400 | Unable to decode CSV file |

---

## 8. Clients

**File:** `app/api/v1/endpoints/clients.py`  
**Prefix:** `/api/v1/clients`  
**Authentication:** Required

### GET `/clients`

Returns all clients for the current tenant.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
[
  {
    "id": "client_1",
    "name": "John Doe",
    "company": "Acme Inc.",
    "phone": "+1234567890",
    "email": "john@example.com",
    "tags": ["warm", "priority"]
  }
]
```

---

### POST `/clients`

Creates a new client.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Request Body:**
```json
{
  "name": "John Doe",
  "company": "Acme Inc.",
  "phone": "+1234567890",
  "email": "john@example.com",
  "tags": ["warm"],
  "notes": "Met at conference"
}
```

**Response (200 OK):**
```json
{
  "id": "client_new_uuid",
  "name": "John Doe",
  "company": "Acme Inc.",
  "phone": "+1234567890",
  "email": "john@example.com",
  "tags": ["warm"]
}
```

---

### GET `/clients/{client_id}`

Returns a single client by ID.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "id": "client_1",
  "name": "John Doe",
  "company": "Acme Inc.",
  "phone": "+1234567890",
  "email": "john@example.com",
  "tags": ["warm", "priority"]
}
```

---

### DELETE `/clients/{client_id}`

Deletes a client by ID.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
{
  "detail": "Client deleted"
}
```

---

## 9. Campaigns

**File:** `app/api/v1/endpoints/campaigns.py`  
**Prefix:** `/api/v1/campaigns`  
**Authentication:** Uses Supabase service key (internal)

### GET `/campaigns`

Returns all campaigns.

**Response (200 OK):**
```json
{
  "campaigns": [
    {
      "id": "campaign_uuid",
      "name": "Sales Outreach Q1",
      "description": "Quarterly sales campaign",
      "status": "running",
      "system_prompt": "You are a friendly sales representative...",
      "voice_id": "voice-professional-male",
      "max_concurrent_calls": 5,
      "created_at": "2025-01-01T00:00:00Z"
    }
  ]
}
```

---

### POST `/campaigns`

Creates a new campaign.

**Request Body:**
```json
{
  "name": "New Campaign",
  "description": "Campaign description",
  "system_prompt": "AI agent instructions",
  "voice_id": "voice-professional-female",
  "max_concurrent_calls": 10
}
```

**Response (200 OK):**
```json
{
  "campaign": {
    "id": "new_campaign_uuid",
    "name": "New Campaign",
    "status": "draft"
  }
}
```

---

### GET `/campaigns/{campaign_id}`

Returns a single campaign by ID.

**Response (200 OK):**
```json
{
  "campaign": {
    "id": "campaign_uuid",
    "name": "Sales Outreach Q1",
    "status": "running"
  }
}
```

---

### POST `/campaigns/{campaign_id}/start`

Starts a campaign.

**Response (200 OK):**
```json
{
  "message": "Campaign campaign_uuid started",
  "campaign": {
    "id": "campaign_uuid",
    "status": "running"
  }
}
```

---

### POST `/campaigns/{campaign_id}/pause`

Pauses a running campaign.

**Response (200 OK):**
```json
{
  "message": "Campaign campaign_uuid paused",
  "campaign": {
    "id": "campaign_uuid",
    "status": "paused"
  }
}
```

---

## 10. Admin

**File:** `app/api/v1/endpoints/admin.py`  
**Prefix:** `/api/v1/admin`  
**Authentication:** Required (Admin role only)

### GET `/admin/tenants`

Returns all tenants (organizations).

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
[
  {
    "id": "tenant_1",
    "business_name": "Client A",
    "plan_id": "professional",
    "minutes_used": 1200,
    "minutes_allocated": 1500
  }
]
```

**Errors:**
| Code | Detail |
|------|--------|
| 403 | Admin access required |

---

### GET `/admin/users`

Returns all users.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200 OK):**
```json
[
  {
    "id": "user_1",
    "email": "owner@clienta.com",
    "role": "owner",
    "tenant_id": "tenant_1"
  }
]
```

---

### GET `/admin/tenants/{tenant_id}`

Returns a single tenant by ID.

**Response (200 OK):**
```json
{
  "id": "tenant_1",
  "business_name": "Client A",
  "plan_id": "professional",
  "minutes_used": 1200,
  "minutes_allocated": 1500
}
```

---

### PATCH `/admin/tenants/{tenant_id}/minutes`

Updates a tenant's allocated minutes.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `minutes_allocated` | int | New allocated minutes |

**Response (200 OK):**
```json
{
  "detail": "Minutes updated",
  "minutes_allocated": 2000
}
```

---

## 11. Webhooks

**File:** `app/api/v1/endpoints/webhooks.py`  
**Prefix:** `/api/v1/webhooks`  
**Authentication:** None (validated by telephony provider)

### POST `/webhooks/vonage/answer`

Handles Vonage call answer webhook.

---

### POST `/webhooks/vonage/event`

Handles Vonage call events.

---

### POST `/webhooks/vonage/rtc`

Handles Vonage RTC events.

---

## 12. WebSockets

**File:** `app/api/v1/endpoints/websockets.py`  
**Prefix:** `/api/v1`

### WS `/ws/voice/{call_id}`

WebSocket endpoint for bidirectional voice streaming.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `call_id` | string | UUID of the call |

**Protocol:**
- Input: PCM audio (16kHz mono)
- Output: PCM audio (16kHz mono)
- Pipeline: STT → LLM → TTS

---

## 13. Database Schema

### Existing Tables (Unchanged)

| Table | Description |
|-------|-------------|
| `campaigns` | Campaign definitions |
| `leads` | Contacts assigned to campaigns |
| `calls` | Call records |
| `conversations` | Conversation transcripts |

### New Tables

| Table | Description |
|-------|-------------|
| `plans` | Pricing plans (basic, professional, enterprise) |
| `tenants` | Organizations/businesses |
| `user_profiles` | User info linked to Supabase Auth |
| `recordings` | Call recording metadata |
| `clients` | Contacts for outreach |

### Schema File

**Location:** `database/schema_update.sql`

Run this in Supabase SQL Editor to create the new tables.

---

## 14. Error Handling

All endpoints return errors in this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request (invalid input) |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (insufficient permissions) |
| 404 | Not Found |
| 500 | Internal Server Error |

---

## 15. Files Reference

### Core Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI application entry point |
| `app/api/v1/routes.py` | Router configuration |
| `app/api/v1/dependencies.py` | Auth dependencies |

### Endpoint Files

| File | Endpoints |
|------|-----------|
| `app/api/v1/endpoints/auth.py` | register, login, me, logout |
| `app/api/v1/endpoints/plans.py` | list plans |
| `app/api/v1/endpoints/dashboard.py` | summary |
| `app/api/v1/endpoints/analytics.py` | call analytics |
| `app/api/v1/endpoints/calls.py` | list, detail |
| `app/api/v1/endpoints/recordings.py` | list, stream |
| `app/api/v1/endpoints/contacts.py` | bulk import |
| `app/api/v1/endpoints/clients.py` | CRUD |
| `app/api/v1/endpoints/campaigns.py` | CRUD, start, pause |
| `app/api/v1/endpoints/admin.py` | tenants, users |
| `app/api/v1/endpoints/webhooks.py` | Vonage webhooks |
| `app/api/v1/endpoints/websockets.py` | Voice streaming |

### Test Files

| File | Tests |
|------|-------|
| `tests/unit/test_api_endpoints.py` | New endpoint tests |
| `tests/unit/test_core.py` | Core functionality tests |

---

## Quick Reference

### Authentication Flow

```
1. User calls POST /auth/register with email
2. System creates tenant, sends magic link
3. User clicks magic link in email
4. Frontend receives JWT token from Supabase
5. Frontend includes token in Authorization header
6. All protected endpoints validate token via Supabase
```

### Example cURL Commands

**Get Plans (Public):**
```bash
curl http://localhost:8000/api/v1/plans/
```

**Register:**
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","business_name":"My Co","plan_id":"basic"}'
```

**Get Dashboard (Protected):**
```bash
curl http://localhost:8000/api/v1/dashboard/summary \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | Dec 9, 2025 | Initial API implementation with 31 routes |
