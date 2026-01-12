# Day 25: Meeting Booking Feature

> **Date**: January 7, 2026  
> **Focus**: AI-powered meeting booking with Google Calendar and Microsoft Outlook  
> **Status**: Implementation Complete

---

## Overview

Today we implemented an end-to-end meeting booking feature that enables Talky.ai to schedule meetings through multiple trigger points. The system integrates with Google Calendar (Google Meet) and Microsoft Outlook (Teams) for seamless calendar management and video conferencing.

### Key Features

- ✅ **MeetingService** - Central orchestration layer for booking operations
- ✅ **Google Calendar** - OAuth integration with Google Meet links
- ✅ **Microsoft Outlook** - Microsoft Graph API with Teams links
- ✅ **Assistant Tools** - 4 new AI tools for meeting management
- ✅ **REST API** - Complete CRUD endpoints at `/api/v1/meetings`
- ✅ **Error Handling** - User-friendly messages when calendar not connected
- ✅ **Multi-Tenant** - Strict tenant isolation via RLS policies

---

## Architecture

### Directory Structure

```
backend/app/
├── services/
│   ├── __init__.py
│   └── meeting_service.py          # Core booking orchestration
│
├── infrastructure/connectors/calendar/
│   ├── __init__.py
│   ├── base.py                     # CalendarProvider ABC
│   ├── google_calendar.py          # Google Calendar (existing)
│   └── outlook_calendar.py         # Microsoft Outlook (NEW)
│
└── api/v1/endpoints/
    └── meetings.py                 # REST API endpoints (NEW)
```

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MEETING BOOKING FLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Voice Agent  │     │  Assistant   │     │  Dashboard   │
│ Call Outcome │     │  Chat Tool   │     │   REST API   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                 ┌─────────────────────┐
                 │   MeetingService    │
                 │  ─────────────────  │
                 │ • get_availability  │
                 │ • create_meeting    │
                 │ • update_meeting    │
                 │ • cancel_meeting    │
                 └──────────┬──────────┘
                            │
          ┌─────────────────┴─────────────────┐
          ▼                                   ▼
┌──────────────────┐              ┌──────────────────┐
│  Google Calendar │              │ Outlook Calendar │
│  ──────────────  │              │  ──────────────  │
│  + Google Meet   │              │  + MS Teams      │
└────────┬─────────┘              └────────┬─────────┘
         │                                 │
         └─────────────┬───────────────────┘
                       ▼
              ┌────────────────┐
              │   Database     │
              │  ────────────  │
              │ • meetings     │
              │ • actions      │
              │ • connectors   │
              └────────────────┘
```

### Sequence Diagram (Book Meeting)

```
┌────────┐    ┌─────────────┐    ┌────────────┐    ┌──────────┐    ┌──────────┐
│ Client │    │ MeetingAPI  │    │MeetingServ.│    │ Calendar │    │ Database │
└───┬────┘    └──────┬──────┘    └─────┬──────┘    └────┬─────┘    └────┬─────┘
    │                │                 │                │               │
    │ POST /meetings │                 │                │               │
    │───────────────▶│                 │                │               │
    │                │ create_meeting()│                │               │
    │                │────────────────▶│                │               │
    │                │                 │ get_connector  │               │
    │                │                 │────────────────────────────────▶
    │                │                 │                │ connector data│
    │                │                 │◀───────────────────────────────│
    │                │                 │                │               │
    │                │                 │ create_event() │               │
    │                │                 │───────────────▶│               │
    │                │                 │ event + link   │               │
    │                │                 │◀───────────────│               │
    │                │                 │                │               │
    │                │                 │ INSERT meeting │               │
    │                │                 │────────────────────────────────▶
    │                │                 │                │    meeting_id │
    │                │                 │◀───────────────────────────────│
    │                │   Meeting       │                │               │
    │                │◀────────────────│                │               │
    │  {join_link}   │                 │                │               │
    │◀───────────────│                 │                │               │
```

---

## API Endpoints

### Base URL: `/api/v1/meetings`

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/availability` | Get free time slots | Yes |
| POST | `/` | Book a new meeting | Yes |
| GET | `/` | List tenant's meetings | Yes |
| GET | `/{id}` | Get meeting details | Yes |
| PUT | `/{id}` | Update/reschedule | Yes |
| DELETE | `/{id}` | Cancel meeting | Yes |

### Request/Response Examples

**GET /availability?date=2026-01-08&duration_minutes=30**
```json
[
  {"start": "2026-01-08T09:00:00", "end": "2026-01-08T10:30:00", "duration_minutes": 30},
  {"start": "2026-01-08T14:00:00", "end": "2026-01-08T18:00:00", "duration_minutes": 30}
]
```

**POST /meetings**
```json
// Request
{
  "title": "Demo Call with John",
  "start_time": "2026-01-08T10:00:00",
  "duration_minutes": 30,
  "attendees": ["john@example.com"],
  "add_video_conference": true
}

// Response
{
  "success": true,
  "meeting_id": "uuid-here",
  "title": "Demo Call with John",
  "start_time": "2026-01-08T10:00:00",
  "end_time": "2026-01-08T10:30:00",
  "join_link": "https://meet.google.com/abc-defg-hij",
  "calendar_link": "https://calendar.google.com/event?eid=...",
  "provider": "google_calendar"
}
```

**Error Response (No Calendar Connected)**
```json
{
  "success": false,
  "error": "No calendar connected. Please connect Google Calendar or Microsoft Outlook from Settings > Integrations.",
  "calendar_required": true
}
```

---

## Assistant Tools

### Tool Registry

| Tool | Type | Description |
|------|------|-------------|
| `check_availability` | Action | Check open slots for a date |
| `book_meeting` | Action | Book meeting with video link |
| `update_meeting` | Action | Reschedule existing meeting |
| `cancel_meeting` | Action | Cancel scheduled meeting |

### Usage Examples

**Check Availability**
```
User: "What times are free tomorrow for a 30 minute call?"

Assistant uses: check_availability(date="2026-01-08", duration_minutes=30)
Returns: 5 available slots between 9 AM - 6 PM
```

**Book Meeting**
```
User: "Book a demo call with john@acme.com tomorrow at 2pm"

Assistant uses: book_meeting(
    title="Demo Call",
    start_time="2026-01-08T14:00:00",
    attendees=["john@acme.com"],
    add_video_conference=true
)
Returns: Meeting booked with Google Meet link
```

---

## Calendar Providers

### Google Calendar

| Feature | Support |
|---------|---------|
| OAuth Scopes | `calendar`, `calendar.events` |
| Video Conferencing | Google Meet (auto-generated) |
| Event CRUD | ✅ Full support |
| Availability | ✅ Free/busy detection |

### Microsoft Outlook (NEW)

| Feature | Support |
|---------|---------|
| OAuth Scopes | `Calendars.ReadWrite`, `OnlineMeetings.ReadWrite` |
| Video Conferencing | Microsoft Teams |
| Event CRUD | ✅ Full support |
| Availability | ✅ CalendarView-based |
| API | Microsoft Graph v1.0 |

**Environment Variables**
```bash
# Google (existing)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

# Microsoft (NEW)
MICROSOFT_CLIENT_ID=...
MICROSOFT_CLIENT_SECRET=...
```

---

## Database Schema

Meetings are stored in the existing `meetings` table (created in Day 23):

```sql
CREATE TABLE meetings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    lead_id UUID REFERENCES leads(id),
    call_id UUID REFERENCES calls(id),
    connector_id UUID REFERENCES connectors(id),
    action_id UUID REFERENCES assistant_actions(id),
    external_event_id TEXT,               -- Provider's event ID
    title TEXT NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    timezone TEXT DEFAULT 'UTC',
    location TEXT,
    join_link TEXT,                       -- Google Meet / Teams URL
    status TEXT DEFAULT 'scheduled',      -- scheduled, completed, cancelled
    attendees JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Error Handling

| Scenario | Error Message | HTTP Code |
|----------|---------------|-----------|
| No calendar connected | "No calendar connected. Please connect..." | 400 |
| Calendar expired | "Calendar connection expired. Please reconnect..." | 400 |
| Meeting not found | "Meeting not found" | 404 |
| Invalid date format | "Invalid date format" | 400 |
| Authentication required | "Unauthorized" | 401 |

---

## Files Created/Modified

### New Files

| File | Lines | Description |
|------|-------|-------------|
| `app/services/__init__.py` | 1 | Services package |
| `app/services/meeting_service.py` | ~350 | Core booking logic |
| `app/infrastructure/connectors/calendar/outlook_calendar.py` | ~400 | MS Graph connector |
| `app/api/v1/endpoints/meetings.py` | ~330 | REST endpoints |

### Modified Files

| File | Changes |
|------|---------|
| `app/infrastructure/assistant/tools.py` | +4 meeting tools (+186 lines) |
| `app/api/v1/routes.py` | +meetings router |
| `app/api/v1/endpoints/connectors.py` | +Outlook provider metadata |
| `app/infrastructure/connectors/calendar/__init__.py` | +OutlookCalendarConnector export |

---

## Testing

### Verification Commands

```bash
# Test MeetingService import
python -c "from app.services.meeting_service import MeetingService; print('OK')"

# Test OutlookCalendarConnector import  
python -c "from app.infrastructure.connectors.calendar import OutlookCalendarConnector; print('OK')"

# Test assistant tools
python -c "from app.infrastructure.assistant.tools import ALL_TOOLS; print(len(ALL_TOOLS), 'tools')"

# Test API endpoints
python -c "from app.api.v1.endpoints.meetings import router; print(len(router.routes), 'endpoints')"
```

---

## Next Steps (Phase 2)

- [ ] Voice agent call outcome integration
- [ ] Unit tests for MeetingService
- [ ] Integration tests for /meetings API
- [ ] Email notifications for meeting invites
- [ ] Calendar webhook for event updates
