# Admin Panel - Day 2: Command Center Dashboard

## Date: January 27, 2026

---

## Executive Summary

Day 2 focuses on implementing the **Command Center Dashboard** - the central control hub for the Talky.ai admin panel. This includes real-time statistics, provider health monitoring, and a global pause/resume system for calls.

---

## Architecture Overview

The Admin Panel follows industry-standard architecture where the **frontend-only** application connects to the **main Talky.ai backend** via REST APIs:

```
┌─────────────────────────────────────────────────────────────────┐
│                        ADMIN PANEL                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌───────────────────┐                                         │
│   │  Admin Frontend   │                                         │
│   │  (React + Vite)   │                                         │
│   │                   │                                         │
│   │  - StatsGrid      │     HTTP/REST            ┌────────────┐ │
│   │  - LiveCalls      │  ──────────────────────▶ │   Main     │ │
│   │  - SystemHealth   │                          │  Backend   │ │
│   │  - Sidebar        │  ◀────────────────────── │  (FastAPI) │ │
│   │                   │      JSON Response       │            │ │
│   └───────────────────┘                          │  /admin/*  │ │
│                                                  │  endpoints │ │
│   Location:                                      └──────┬─────┘ │
│   Admin/frontend/                                       │       │
│                                                         │       │
│                                                   ┌─────▼─────┐ │
│                                                   │  Supabase │ │
│                                                   │  Database │ │
│                                                   └───────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Why This Architecture?

| Benefit | Description |
|---------|-------------|
| **Single Source of Truth** | Admin sees exactly what users see from the same database |
| **Code Reuse** | Tenant, User, Call models already exist in backend |
| **Security** | One place to enforce RLS and admin permissions |
| **Maintainability** | One backend codebase to update |
| **Cost Efficiency** | No additional backend infrastructure |

---

## Backend Changes

### File: [admin.py](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/backend/app/api/v1/endpoints/admin.py)

#### New Response Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `DashboardStatsResponse` | active_calls, error_rate_24h, active_tenants, api_errors_24h | Real-time dashboard stats |
| `SystemHealthItem` | name, status, latency_ms, latency_display | Single provider status |
| `SystemHealthResponse` | providers[] | All provider health |
| `PauseCallsResponse` | paused, paused_at, message | Pause state |

#### New Endpoints

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/admin/dashboard/stats` | GET | Real-time dashboard statistics | Admin |
| `/admin/system-health` | GET | Provider health status | Admin |
| `/admin/calls/pause` | POST | Toggle global pause | Admin |
| `/admin/calls/pause-status` | GET | Get pause state | Admin |

### File: [dependencies.py](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/backend/app/api/v1/dependencies.py)

Added **Dev Mode Bypass** in `require_admin()`:
- When `ADMIN_DEV_MODE=true` in `.env`, returns mock admin user
- Enables testing without authentication

---

## Frontend Changes

### API Client: [api.ts](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/lib/api.ts)

#### New Interfaces

```typescript
interface DashboardStats {
    active_calls: number;
    error_rate_24h: string;
    active_tenants: number;
    api_errors_24h: number;
}

interface SystemHealthItem {
    name: string;
    status: 'operational' | 'degraded' | 'down';
    latency_ms: number;
    latency_display: string;
}

interface PauseCallsResponse {
    paused: boolean;
    paused_at: string | null;
    message: string;
}
```

#### New Methods

| Method | Endpoint | Description |
|--------|----------|-------------|
| `getDashboardStats()` | GET /admin/dashboard/stats | Fetch live stats |
| `getSystemHealth()` | GET /admin/system-health | Fetch provider health |
| `pauseAllCalls()` | POST /admin/calls/pause | Toggle pause |
| `getPauseStatus()` | GET /admin/calls/pause-status | Get pause state |

---

### Component Updates

#### [StatsGrid.tsx](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/components/StatsGrid.tsx)

| Feature | Implementation |
|---------|----------------|
| **Live Data** | Fetches from `/admin/dashboard/stats` on mount |
| **Auto-Refresh** | Updates every 30 seconds |
| **Loading State** | Shows spinner while fetching |
| **Error Handling** | Logs warnings, shows default values |

#### [SystemHealth.tsx](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/components/SystemHealth.tsx)

| Feature | Implementation |
|---------|----------------|
| **Live Data** | Fetches from `/admin/system-health` on mount |
| **Auto-Refresh** | Updates every 60 seconds |
| **Status Icons** | Green (operational), Orange (degraded), Red (down) |
| **Latency Display** | Shows provider latency values |

#### [LiveCalls.tsx](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/components/LiveCalls.tsx)

| Feature | Implementation |
|---------|----------------|
| **Pause/Resume Button** | Calls `/admin/calls/pause` |
| **Confirmation Dialog** | Asks "Pause all calls?" before pausing |
| **Status Banner** | Shows red banner when system is paused |
| **Visual Feedback** | Button changes between Pause/Resume with icons |

#### [AdminRouteGuard.tsx](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/components/AdminRouteGuard.tsx)

Added dev mode bypass:
- When `VITE_ADMIN_DEV_MODE=true`, skips all auth checks
- Enables development without login

---

### CSS Updates: [index.css](file:///c:/Users/AL%20AZIZ%20TECH/Desktop/Talky.ai-complete-/Admin/frontend/src/index.css)

| Style | Purpose |
|-------|---------|
| `.pause-controls` | Container for pause button |
| `.btn-pause` / `.btn-resume` | Styled toggle buttons |
| `.confirm-dialog` | Inline confirmation prompt |
| `.pause-banner` | Red warning banner when paused |
| `.animate-spin` | Loading spinner animation |

---

## Environment Configuration

### Backend (.env)

```bash
# Admin Panel Dev Mode
ADMIN_DEV_MODE=true
```

### Frontend (Admin/frontend/.env)

```bash
# Backend API URL
VITE_API_BASE_URL=http://localhost:8000/api/v1

# Dev Mode - bypasses authentication
VITE_ADMIN_DEV_MODE=true
```

> [!WARNING]
> Set both dev mode flags to `false` in production!

---

## Testing Instructions

### 1. Start Backend

```bash
cd c:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\backend
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Start Frontend

```bash
cd c:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\Admin\frontend
npm run dev
```

### 3. Test Command Center

1. Navigate to `http://localhost:5173`
2. Verify:
   - Stats cards display (may show 0 if no data)
   - System Health shows provider status
   - "Pause All Calls" button works with confirmation

### 4. API Testing (curl)

```bash
# Dashboard stats
curl http://localhost:8000/api/v1/admin/dashboard/stats

# System health
curl http://localhost:8000/api/v1/admin/system-health

# Pause toggle
curl -X POST http://localhost:8000/api/v1/admin/calls/pause

# Pause status
curl http://localhost:8000/api/v1/admin/calls/pause-status
```

---

## Files Changed

| Category | File | Change |
|----------|------|--------|
| Backend | `admin.py` | +4 endpoints, +4 models |
| Backend | `dependencies.py` | Dev mode bypass |
| Backend | `.env` | +ADMIN_DEV_MODE |
| Frontend | `api.ts` | +4 types, +4 methods |
| Frontend | `StatsGrid.tsx` | Live data, auto-refresh |
| Frontend | `SystemHealth.tsx` | Live data, auto-refresh |
| Frontend | `LiveCalls.tsx` | Pause functionality |
| Frontend | `AdminRouteGuard.tsx` | Dev mode bypass |
| Frontend | `index.css` | Pause control styles |
| Frontend | `.env` | +VITE_ADMIN_DEV_MODE |

---

## Next Steps (Day 3)

- [ ] Implement Tenants page with CRUD operations
- [ ] Add live calls data from actual calls endpoint
- [ ] Implement end call functionality
- [ ] Add real provider health checks (ping actual services)
