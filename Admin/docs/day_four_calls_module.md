# Day 4: Calls Module - Admin Panel

**Date**: January 29, 2026  
**Goal**: Calls are traceable end-to-end

---

## Overview

Day 4 implements a comprehensive Calls Module for the Admin Panel, allowing administrators to:
- Monitor live calls in real-time with auto-refresh
- Browse call history with search, filter, and pagination
- View detailed call information including timeline and transcript
- Terminate active calls when necessary

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Admin Frontend                                   │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                        CallsPage.tsx                                │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │  │
│  │  │  LiveCallsTable │  │ CallHistoryTable │  │  CallDetailDrawer   │ │  │
│  │  │  - Auto-refresh │  │ - Search/Filter  │  │  - Timeline         │ │  │
│  │  │  - Terminate    │  │ - Pagination     │  │  - Transcript       │ │  │
│  │  └─────────────────┘  └─────────────────┘  │  - Recording        │ │  │
│  │                                            └─────────────────────┘ │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                  │                                        │
│                                  ▼                                        │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                          api.ts                                     │  │
│  │  getLiveCalls(), getCallHistory(), getAdminCallDetail(),           │  │
│  │  terminateCall()                                                    │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          Backend (FastAPI)                                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                    admin.py - New Endpoints                         │  │
│  │  GET  /admin/calls/live       - Active calls list                  │  │
│  │  GET  /admin/calls/history    - Paginated history with filters     │  │
│  │  GET  /admin/calls/{id}       - Full call detail with timeline     │  │
│  │  POST /admin/calls/{id}/terminate - Force-end active call          │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Backend Changes

### New Endpoints in `admin.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/calls/live` | GET | Returns active calls with tenant/campaign names |
| `/admin/calls/history` | GET | Paginated history with search, status, tenant, date filters |
| `/admin/calls/{id}` | GET | Full call detail including timeline and transcript |
| `/admin/calls/{id}/terminate` | POST | Sets call status to 'terminated' |

### New Pydantic Models

```python
class TimelineEvent(BaseModel):
    event: str
    timestamp: str
    status: Optional[str]

class LiveCallItem(BaseModel):
    id, tenant_id, tenant_name, phone_number, campaign_name,
    status, started_at, duration_seconds

class CallHistoryItem(BaseModel):
    id, tenant_id, tenant_name, phone_number, campaign_name,
    status, outcome, duration_seconds, started_at, ended_at, created_at

class AdminCallDetail(BaseModel):
    # Full call details including transcript, timeline, recording_url, cost
```

---

## Frontend Changes

### New Components

| Component | Purpose |
|-----------|---------|
| `LiveCallsTable.tsx` | Real-time active calls with 10s auto-refresh, terminate confirmation |
| `CallHistoryTable.tsx` | Searchable, filterable, paginated call history |
| `CallDetailDrawer.tsx` | Slide-in drawer with timeline, transcript chat bubbles, recording |

### Updated Components

| Component | Changes |
|-----------|---------|
| `CallsPage.tsx` | Replaced placeholder with tabbed interface (Live/History) |
| `api.ts` | Added types and methods for calls module |
| `index.css` | Added 650+ lines of calls module styles |

### New API Types

```typescript
interface LiveCallItem {
    id, tenant_id, tenant_name, phone_number, campaign_name,
    status, started_at, duration_seconds
}

interface AdminCallDetail {
    id, tenant_id, tenant_name, phone_number, campaign_id, campaign_name,
    lead_id, status, outcome, goal_achieved, started_at, answered_at,
    ended_at, duration_seconds, transcript, transcript_json,
    summary, recording_url, cost, timeline, created_at, updated_at
}
```

---

## Features

### Live Calls Tab
- Auto-refreshes every 10 seconds
- Shows tenant and campaign context
- Status badges (in_progress, ringing, queued, initiated)
- Duration counter
- Terminate with confirmation dialog

### History Tab
- Search by phone number
- Filter by status
- Pagination (20 items per page)
- Click row to open detail drawer
- Status badges with icons

### Call Detail Drawer
- Slide-in animation from right
- Phone number and status header
- Quick stats: tenant, duration, date
- Goal achieved indicator
- Campaign and cost display
- Call summary (AI-generated)
- Recording audio player
- Tabbed content:
  - **Timeline**: Visual vertical timeline of call events
  - **Transcript**: Chat-style bubbles (AI Agent / Customer)

---

## CSS Additions

New styles added to `index.css`:

- **Status Badges**: Color-coded for in_progress, ringing, queued, completed, failed, terminated
- **Page Tabs**: Segmented control for Live/History switching
- **Drawer**: Slide-in animation, overlay, header, body
- **Timeline**: Vertical timeline with dots and content
- **Transcript**: Chat bubbles with role labels
- **Table Enhancements**: Clickable rows, phone formatting, duration cells

---

## Testing Instructions

### Backend Testing

```bash
cd backend
python -m uvicorn app.main:app --reload

# Test live calls
curl http://localhost:8000/api/v1/admin/calls/live

# Test history
curl "http://localhost:8000/api/v1/admin/calls/history?page=1&status=completed"

# Test call detail
curl http://localhost:8000/api/v1/admin/calls/{call_id}

# Test terminate
curl -X POST http://localhost:8000/api/v1/admin/calls/{call_id}/terminate
```

### Frontend Testing

```bash
cd Admin/frontend
npm run dev
```

1. Navigate to http://localhost:5173/calls
2. Verify "Live Calls" tab displays active calls
3. Verify "History" tab with search/filter/pagination
4. Click a history row → verify drawer opens
5. Verify timeline and transcript tabs work
6. Test terminate on a live call

---

## Files Changed

### Backend
- `backend/app/api/v1/endpoints/admin.py` - Added 420+ lines for calls module

### Frontend
- `Admin/frontend/src/lib/api.ts` - Added call types and 4 API methods
- `Admin/frontend/src/pages/CallsPage.tsx` - Full implementation
- `Admin/frontend/src/components/LiveCallsTable.tsx` - New component
- `Admin/frontend/src/components/CallHistoryTable.tsx` - New component
- `Admin/frontend/src/components/CallDetailDrawer.tsx` - New component
- `Admin/frontend/src/index.css` - Added 650+ lines for calls module styles

---

## Notes

> **Terminate Call**: This is a database-level status change. Actual VoIP disconnection depends on telephony provider integration. The call's status will be set to 'terminated' and outcome to 'terminated_by_admin'.

> **Real-time Updates**: Live calls table auto-refreshes every 10 seconds. For true real-time updates, WebSocket integration would be required (future enhancement).

---

## Summary

Day 4 delivers a fully functional Calls Module enabling administrators to:
- ✅ Monitor live calls with real-time refresh
- ✅ Browse and search call history
- ✅ View detailed call information with timeline and transcript
- ✅ Terminate active calls with confirmation
- ✅ Play call recordings (when available)
- ✅ View AI-generated call summaries
