# API Connection Verification

This document verifies that all frontend APIs are properly connected to the backend.

## ✅ Connected APIs

### Authentication (via api.ts - already connected)
- `POST /auth/login` - Email login
- `POST /auth/verify-otp` - OTP verification
- `POST /auth/register` - User registration
- `GET /auth/me` - Get current user
- `POST /auth/logout` - Logout

### Dashboard
- `GET /dashboard/summary` - Dashboard metrics
  - Frontend: `dashboardApi.getDashboardSummary()`
  - Returns: total_calls, answered_calls, failed_calls, minutes_used, minutes_remaining, active_campaigns

### Campaigns
- `GET /campaigns` - List all campaigns
  - Frontend: `dashboardApi.listCampaigns()`
- `GET /campaigns/{id}` - Get campaign details
  - Frontend: `dashboardApi.getCampaign(id)`
- `POST /campaigns` - Create new campaign
  - Frontend: `dashboardApi.createCampaign(data)`
- `POST /campaigns/{id}/start` - Start campaign
  - Frontend: `dashboardApi.startCampaign(id)`
- `POST /campaigns/{id}/pause` - Pause campaign
  - Frontend: `dashboardApi.pauseCampaign(id)`
- `POST /campaigns/{id}/stop` - Stop campaign
  - Frontend: `dashboardApi.stopCampaign(id)`
- `GET /campaigns/{id}/stats` - Campaign statistics
  - Frontend: `dashboardApi.getCampaignStats(id)`

### Contacts
- `GET /campaigns/{id}/contacts` - List campaign contacts
  - Frontend: `dashboardApi.listContacts(campaignId, page, pageSize)`
- `POST /campaigns/{id}/contacts` - Add single contact
  - Frontend: `dashboardApi.addContact(campaignId, data)`
- `POST /contacts/campaigns/{id}/upload` - CSV bulk upload
  - Frontend: `extendedApi.uploadCSV(campaignId, file, skipDuplicates)`

### Calls
- `GET /calls` - List calls with pagination
  - Frontend: `dashboardApi.listCalls(page, pageSize)`
  - Backend returns: `{ items: CallListItem[], total, page, page_size }`
  - Frontend maps to: `{ calls: Call[], total }`
- `GET /calls/{id}` - Get call details
  - Frontend: `dashboardApi.getCall(id)`
  - Backend returns: `{ id, timestamp, to_number, ... }`
  - Frontend maps to: `{ id, created_at, phone_number, ... }`
- `GET /calls/{id}/transcript` - Get call transcript
  - Frontend: `dashboardApi.getCallTranscript(id, format)`

### Analytics
- `GET /analytics/calls` - Call analytics with date range
  - Frontend: `extendedApi.getCallAnalytics(fromDate, toDate, groupBy)`
  - Supports grouping by: day, week, month

### Recordings
- `GET /recordings` - List recordings
  - Frontend: `extendedApi.listRecordings(callId, page, pageSize)`
- `GET /recordings/{id}/stream` - Stream recording audio
  - Frontend: `extendedApi.getRecordingStreamUrl(recordingId)`

### Connectors (via backend-api.ts - already connected)
- `GET /connectors` - List connectors
- `POST /connectors` - Create connector
- `GET /connectors/status` - Connector status
- `POST /connectors/{type}/authorize` - OAuth authorization
- `DELETE /connectors/{type}/disconnect` - Disconnect connector

### Meetings & Calendar (via backend-api.ts - already connected)
- `GET /meetings` - List meetings
- `GET /calendar-events` - List calendar events
- `POST /calendar-events` - Create calendar event
- `DELETE /calendar-events/{id}` - Cancel event

### Reminders (via backend-api.ts - already connected)
- `GET /reminders` - List reminders
- `POST /reminders` - Create reminder
- `DELETE /reminders/{id}` - Cancel reminder

### Email (via backend-api.ts - already connected)
- `GET /email/templates` - List email templates
- `POST /email/send` - Send email

### Assistant (via backend-api.ts - already connected)
- `GET /assistant/actions` - List assistant actions
- `GET /assistant/runs` - List assistant runs
- `POST /assistant/execute` - Execute assistant action
- `POST /assistant/plan` - Generate assistant plan

## 🔧 Field Mapping

### Calls List Response
Backend → Frontend:
- `items` → `calls`
- `to_number` → `phone_number`
- `timestamp` → `created_at`

### Call Detail Response
Backend → Frontend:
- `to_number` → `phone_number`
- `timestamp` → `created_at`
- `recording_id` → generates `recording_url`

## 🔐 Authentication

All API calls include:
- `Authorization: Bearer <token>` header (automatically added by http-client)
- Token stored in localStorage as `talklee.auth.token`
- Dev mode bypasses auth with stub responses

## 🌐 Base URL Configuration

- Environment: `.env.local`
- Variable: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`
- The `apiBaseUrl()` function handles the configuration
- All paths are relative to this base URL

## ⚠️ Important Notes

1. **CSV Upload**: Uses raw `fetch` with FormData instead of http-client (required for file upload)
2. **Auth Token**: CSV upload manually includes auth token from http-client
3. **Field Mapping**: Backend uses snake_case, frontend uses camelCase - mapping layer handles conversion
4. **Response Format**: Backend returns `{ items: [], total }`, frontend often expects different structure
5. **Error Handling**: All API calls include proper error handling with ApiClientError

## 🧪 Testing

To test the integration:

1. Start backend:
   ```bash
   cd backend
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. Start frontend:
   ```bash
   cd Talk-Leee
   npm run dev
   ```

3. Visit pages:
   - Dashboard: `http://localhost:3000/dashboard`
   - Campaigns: `http://localhost:3000/campaigns`
   - Calls: `http://localhost:3000/calls`
   - Analytics: `http://localhost:3000/analytics`

4. Check browser console for API calls:
   - Look for `[api] GET /dashboard/summary -> 200` logs
   - Verify no 404 or 500 errors
   - Check Network tab for request/response details
