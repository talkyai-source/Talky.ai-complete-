# Talky.ai — API Connectivity Gap Analysis & Implementation Plan

**Date:** 2026-05-04  
**Scope:** Backend ↔ Frontend (Talk-Leee + Admin Panel) real-time data connectivity  
**Goal:** Eliminate all production mock data and connect every dashboard/admin view to live backend APIs.

---

## 1. Executive Summary

| Frontend | Status | Mock Areas |
|----------|--------|------------|
| **Talk-Leee** (User Dashboard) | Core features ✅ connected | Billing + Admin sub-pages ❌ fully mock |
| **Admin Panel** (Vite React) | Some modules ✅ connected | Auth + Command Center + 3 placeholder pages ❌ mock |

**Key Issue Categories:**
1. **Frontend uses mock data directly** — bypassing even hybrid API hooks (`billing-mock-data.ts` imported into pages).
2. **Backend endpoints missing** — Talk-Leee expects ~20 billing/admin endpoints that do not exist in the backend.
3. **Backend endpoints exist but UI is placeholder** — Admin panel has 3 "Coming Soon" pages for fully-built backend modules.
4. **Path mismatches** — Frontend expects `/admin/api-keys`, `/admin/webhooks`, etc.; backend serves `/admin/secrets`, `/admin/audit/logs`, etc.
5. **Real-time gap** — Dashboard live charts are client-side simulated sine waves; no backend time-series stream exists.

---

## 2. Talk-Leee (Next.js) — Gaps & Root Causes

### 2.1 Dashboard Live Charts — SIMULATED DATA
| Item | Detail |
|------|--------|
| **File** | `app/dashboard/page.tsx` → `useSimulatedLiveBuckets()` |
| **What it does** | Generates 48h of fake call-volume buckets using `Math.sin()` + `Math.random()` |
| **Why not connected** | Backend has `/dashboard/summary` (aggregates only: total calls, minutes, campaigns). **No time-series endpoint** exists for per-bucket call volume / queue depth / answered/failed breakdowns. |
| **Backend gap** | Missing: `GET /dashboard/live-buckets` or WebSocket/SSE stream for real-time KPI time-series. |

### 2.2 Billing — FULLY MOCK
All billing pages import `billing-mock-data.ts` **directly**; the hybrid `billing-api.ts` hooks are unused.

| Talk-Leee Page | Mock Imports | Expected Endpoint | Backend Reality |
|----------------|--------------|-------------------|-----------------|
| `billing/page.tsx` | `CURRENT_TENANT_PLAN`, `CURRENT_USAGE`, `DAILY_USAGE`, `INVOICES`, `ADJUSTMENTS`, `OVERAGE_ALERTS` | `/billing/plan`, `/billing/usage/summary`, `/billing/usage/daily`, `/billing/invoices`, `/billing/adjustments`, `/billing/overage-alerts` | **Only** `/billing/subscription`, `/billing/usage`, `/billing/invoices`, `/billing/config` exist. Paths differ; `/billing/plan`, `/billing/usage/summary`, `/billing/usage/daily`, `/billing/adjustments`, `/billing/overage-alerts` are **missing**. |
| `billing/plans/page.tsx` | `PLANS`, `CURRENT_TENANT_PLAN` | `/billing/plans` | **Missing**. Backend has `/plans/` (public pricing list) but not tenant-specific plan selection endpoint. |
| `billing/invoices/page.tsx` | `INVOICES` | `/billing/invoices` | **Exists** ✅ but page uses mock directly instead of hook. |
| `billing/invoices/[id]/page.tsx` | `INVOICES` | `/billing/invoices/{id}` | **Missing** individual invoice endpoint. |
| `admin/billing/page.tsx` | `PARTNER_BILLING` | `/billing/partners` | **Missing**. |
| `admin/billing/tenants/page.tsx` | `TENANT_BILLING` | `/billing/tenants` | **Missing**. |

**Mutation endpoints Talk-Leee expects (all missing):**
- `POST /billing/plan/change`
- `POST /billing/adjustment`

### 2.3 Admin Sub-Pages in Talk-Leee — FULLY MOCK
All these pages import mock data directly. `billing-api.ts` has fallback hooks that try these endpoints, but the pages don't use the hooks.

| Talk-Leee Page | Mock Data | Expected Endpoint | Backend Reality |
|----------------|-----------|-------------------|-----------------|
| `admin/audit-logs/page.tsx` | `AUDIT_LOGS` | `/admin/audit-logs` | **Path mismatch**. Backend has `GET /admin/audit/logs` ✅. |
| `admin/api-keys/page.tsx` | `API_KEYS` | `/admin/api-keys` | **Missing**. No API-key management router in backend. |
| `admin/webhooks/page.tsx` | `WEBHOOK_ENDPOINTS`, `WEBHOOK_DELIVERIES` | `/admin/webhooks`, `/admin/webhooks/deliveries` | **Missing** generic webhook mgmt. Backend has `/webhooks/secure/admin/configure` (HMAC secret only). |
| `admin/rate-limiting/page.tsx` | `RATE_LIMIT_RULES` + hardcoded `totalBlocked = 1_247` | `/admin/rate-limits` | **Missing**. Backend has telephony SIP rate-limiting (`telephony_concurrency.py`, `telephony_sip.py`) but no global admin rate-limit rules endpoint. |
| `admin/voice-security/page.tsx` | `CALL_GUARD_RULES`, `TENANT_LIMITS`, `PARTNER_LIMITS` | `/admin/call-guards`, `/admin/tenant-limits`, `/admin/partner-limits` | **Partial**. Backend has `/admin/tenants/{id}/call-limits` and `/admin/partners/{id}/limits` (in `call_limits.py`) but paths differ (`call-limits` vs `tenant-limits`). No `/admin/call-guards` endpoint. |
| `admin/secrets/page.tsx` | `SECRETS` | `/admin/secrets` | **Path mismatch**. Backend has `/admin/secrets/tenants/{tenant_id}/secrets` and `/admin/secrets/platform/secrets` ✅. No flat `/admin/secrets` list. |
| `admin/abuse-detection/page.tsx` | `ABUSE_EVENTS`, `BLOCKED_ENTITIES` | `/admin/abuse-events`, `/admin/blocked-entities` | **Partial**. Backend has `/admin/abuse/events`, `/admin/abuse/statistics`, `/admin/abuse/rules` ✅. No `/admin/blocked-entities` endpoint. |

### 2.4 Other Mock / Disconnected in Talk-Leee
| Feature | File | Issue | Backend Status |
|---------|------|-------|----------------|
| **Partner Analytics** | `app/white-label/[partner]/analytics/partner-analytics-client.tsx` | `makeMockData(partnerId)` generates deterministic fake charts | **Missing** partner-scoped analytics endpoint. |
| **MFA Setup** | `components/auth/mfa-setup.tsx` | Comment: *"generate them client-side as a placeholder"* | `/auth/mfa/setup` ✅ exists but frontend doesn't call it. |
| **Campaign Duplicate** | `app/campaigns/page.tsx` `handleDuplicate` | Creates local copy with synthetic ID `camp-copy-${now}`; no POST to backend | Missing `POST /campaigns/{id}/duplicate` or similar. |

---

## 3. Admin Panel (Vite React) — Gaps & Root Causes

### 3.1 Authentication — MOCK
| Item | Detail |
|------|--------|
| **File** | `src/lib/auth.tsx` |
| **Issue** | `USE_DUMMY_AUTH = true` hardcoded. `DUMMY_ADMIN_USER` always returned. Login accepts **any** non-empty email/password. |
| **Why** | Convenience for rapid UI development; never switched to production mode. |
| **Real endpoints bypassed** | `POST /auth/login`, `GET /auth/verify`, `POST /auth/logout` |

### 3.2 Command Center Dashboard (`/`) — MIXED MOCK
| Component | Status | Real Endpoint | Why Mock |
|-----------|--------|---------------|----------|
| `StatsGrid` | ✅ Real | `GET /admin/dashboard/stats` | — |
| `SystemHealth` | ✅ Real | `GET /admin/system-health` | — |
| `LiveCalls` | ❌ Mock | `GET /admin/calls/pause-status` (pause state only) | Imports static `mockCalls` array with 5 fake calls. `LiveCallsTable` (separate page `/calls`) uses real `GET /admin/calls/live`, but **Command Center widget does not**. |
| `Incidents` | ❌ Mock | None | Hardcoded static array ("Twilio Connection Failures", "STT Latency Spike"). Backend has full incidents API ✅. |
| `TopTenantsList` | ❌ Mock | None | Hardcoded ACME Inc / Beta Corp. Backend has `/admin/tenants` and `/analytics/system` ✅. |
| `TopTenantsPanel` | ❌ Mock | None | Hardcoded connector statuses. Backend has `/admin/connectors` ✅. |
| `QuotaUsage` | ❌ Mock | None | Hardcoded bars (Calls 85%, Tokens 45%, Storage 30%). Backend has `/admin/usage/summary` ✅. |

### 3.3 Placeholder Pages — NO API CALLS
| Page | Expected Backend Endpoint | Status |
|------|---------------------------|--------|
| `ActionsLogPage` (`/actions-log`) | `GET /admin/audit` (or `/admin/audit/logs`) | **Backend exists** ✅ — page is empty "Coming Soon" |
| `IncidentsPage` (`/incidents`) | `GET /admin/incidents` | **Backend exists** ✅ — page is empty "Coming Soon" |
| `UsageCostPage` (`/usage-cost`) | `GET /admin/usage/breakdown` | **Backend exists** ✅ — page is empty "Coming Soon" |

### 3.4 Header / UI Chrome — STATIC
| Component | Issue | Real Data Source |
|-----------|-------|------------------|
| `Header` | Hardcoded "Prod" env badge, static notification badge "5", static "Admin" username | `GET /auth/me` or `GET /admin/users/me` |
| `Sidebar` | Hardcoded navigation | — |

### 3.5 API Definitions Without UI
These endpoints are defined in `src/lib/api.ts` but **zero components call them**:

| Endpoint | Method | Why Unused |
|----------|--------|------------|
| `/auth/login` | POST | Bypassed by dummy auth |
| `/auth/verify` | GET | Bypassed by dummy auth |
| `/auth/logout` | POST | Bypassed by dummy auth |
| `/admin/users` | GET | No User Management page built |
| `/admin/users/{id}` | GET | No User Management page built |
| `/analytics/system` | GET | Not wired to Command Center |
| `/analytics/providers` | GET | No Provider Analytics page built |
| `/admin/audit` | GET | ActionsLogPage is placeholder |
| `/admin/security/events` | GET | No Security Events page built |
| `/admin/configuration` | GET | No System Config page built |
| `/admin/configuration/providers/{type}` | PATCH | No System Config page built |
| `/health` | GET | Not used |
| `/admin/health/database` | GET | Not used |
| `/admin/alerts/settings` | GET/PUT | Not used |
| Passkey endpoints | Various | No Passkey UI built |

---

## 4. Cross-Reference Matrix: Backend Exists vs Frontend Uses

| Backend Endpoint | Talk-Leee Uses? | Admin Uses? | Status |
|------------------|-----------------|-------------|--------|
| `GET /dashboard/summary` | ✅ Yes | — | Connected |
| `GET /analytics/calls` | ✅ Yes | — | Connected |
| `WS /api/v1/ws/ask-ai/{session_id}` | ✅ Yes | — | Connected |
| `GET /campaigns/*` | ✅ Yes | — | Connected |
| `GET /calls/*` | ✅ Yes | — | Connected |
| `GET /ai-options/*` | ✅ Yes | — | Connected |
| `GET /connectors/*` | ✅ Yes | ✅ Yes | Connected |
| `GET /meetings/*` | ✅ Yes | — | Connected |
| `GET /admin/dashboard/stats` | ✅ Yes | ✅ Yes | Connected |
| `GET /admin/system-health` | — | ✅ Yes | Connected |
| `GET /admin/tenants` | ✅ Yes | ✅ Yes | Connected |
| `GET /admin/calls/live` | — | ✅ Yes | Connected (Admin only) |
| `GET /admin/calls/history` | — | ✅ Yes | Connected |
| `GET /admin/actions` | ✅ Yes | ✅ Yes | Connected |
| `GET /admin/connectors` | — | ✅ Yes | Connected |
| `GET /admin/usage/summary` | — | ✅ Yes (in Connectors page) | Connected |
| `GET /admin/health/detailed` | — | ✅ Yes | Connected |
| `GET /admin/health/workers` | — | ✅ Yes | Connected |
| `GET /admin/health/queues` | — | ✅ Yes | Connected |
| `GET /admin/health/database` | — | ❌ No | **Unused** |
| `GET /admin/incidents` | — | ❌ No (placeholder) | **Unused** |
| `GET /admin/audit/logs` | ❌ No (mock) | ❌ No (placeholder) | **Unused** |
| `GET /admin/security-events` | ❌ No (mock) | ❌ No | **Unused** |
| `GET /admin/secrets/*` | ❌ No (mock) | — | **Unused** |
| `GET /admin/abuse/events` | ❌ No (mock) | — | **Unused** |
| `GET /analytics/system` | ❌ No (mock partners) | ❌ No | **Unused** |
| `GET /billing/subscription` | ❌ No (mock) | — | **Unused** |
| `GET /billing/usage` | ❌ No (mock) | — | **Unused** |
| `GET /billing/invoices` | ❌ No (mock) | — | **Unused** |
| `POST /auth/mfa/setup` | ❌ No (placeholder) | — | **Unused** |

---

## 5. Implementation Plan

### Phase 1 — Quick Wins (Fix Existing Backend + Frontend Wiring)
*Goal: Connect endpoints that already exist on both sides without new backend code.*

| # | Task | Files | Effort |
|---|------|-------|--------|
| 1.1 | **Admin Auth** — Replace dummy auth with real `api.login()` / `api.verifyToken()` flow; gate `USE_DUMMY_AUTH` behind `VITE_ADMIN_DEV_MODE=true`. | `Admin/frontend/src/lib/auth.tsx`, `LoginPage.tsx`, `AdminRouteGuard.tsx` | Small |
| 1.2 | **Admin Command Center — LiveCalls widget** — Replace `mockCalls` with `api.getLiveCalls()` (already built in `LiveCallsTable`). | `Admin/frontend/src/components/LiveCalls.tsx` | Small |
| 1.3 | **Admin Command Center — Incidents widget** — Wire `api.getIncidents()` to replace hardcoded list. | `Admin/frontend/src/components/Incidents.tsx` | Small |
| 1.4 | **Admin Command Center — TopTenantsList** — Wire `api.getSystemAnalytics()` or `api.getTenants()`. | `Admin/frontend/src/components/TopTenantsList.tsx` | Small |
| 1.5 | **Admin Command Center — QuotaUsage** — Wire `api.getUsageSummary()` to replace hardcoded bars. | `Admin/frontend/src/components/QuotaUsage.tsx` | Small |
| 1.6 | **Talk-Leee Invoices** — Switch `billing/invoices/page.tsx` to use `useBillingInvoices()` hook (which calls existing `/billing/invoices`). | `Talk-Leee/src/app/billing/invoices/page.tsx` | Small |
| 1.7 | **Talk-Leee MFA Setup** — Call `POST /auth/mfa/setup` instead of client-side placeholder. | `Talk-Leee/src/components/auth/mfa-setup.tsx` | Small |
| 1.8 | **Admin Header** — Wire real user name from auth context; replace static notification badge with API poll (or remove until notifications API exists). | `Admin/frontend/src/components/Header.tsx` | Small |

### Phase 2 — Build Missing Backend Endpoints (Billing & Admin)
*Goal: Create the ~20 endpoints Talk-Leee expects so mock data can be deleted.*

| # | Task | Backend Files | Effort |
|---|------|---------------|--------|
| 2.1 | **Billing Plan API** — Create `GET /billing/plan` (alias/wrapper over `/billing/subscription` with plan details). Create `GET /billing/plans` (tenant-scoped plan options). | `backend/app/api/v1/endpoints/billing.py` | Medium |
| 2.2 | **Billing Usage Daily** — Create `GET /billing/usage/daily` returning time-series daily usage (query `calls` table grouped by day). | `backend/app/api/v1/endpoints/billing.py` | Medium |
| 2.3 | **Billing Adjustments & Overage** — Create `GET /billing/adjustments` and `GET /billing/overage-alerts` (can start as DB-backed tables or computed from usage vs allocation). | `backend/app/api/v1/endpoints/billing.py` + new service | Medium |
| 2.4 | **Billing Invoice Detail** — Create `GET /billing/invoices/{id}` returning single invoice with line items. | `backend/app/api/v1/endpoints/billing.py` | Small |
| 2.5 | **Billing Mutations** — Create `POST /billing/plan/change` and `POST /billing/adjustment`. | `backend/app/api/v1/endpoints/billing.py` | Medium |
| 2.6 | **Admin API Keys** — Create CRUD router `GET|POST /admin/api-keys`, `POST /admin/api-keys/{id}/revoke`. Store in DB with hashed keys. | `backend/app/api/v1/endpoints/api_keys.py` | Medium |
| 2.7 | **Admin Webhooks Mgmt** — Create `GET /admin/webhooks`, `POST /admin/webhooks`, `DELETE /admin/webhooks/{id}`, `POST /admin/webhooks/{id}/test`, `GET /admin/webhooks/deliveries`. | `backend/app/api/v1/endpoints/webhooks_admin.py` | Medium |
| 2.8 | **Admin Rate Limits** — Create `GET|POST /admin/rate-limits`, `PATCH /admin/rate-limits/{id}`. | `backend/app/api/v1/endpoints/rate_limits.py` | Medium |
| 2.9 | **Admin Call Guards** — Create `GET|PATCH /admin/call-guards`. Can wrap telephony guard rules. | `backend/app/api/v1/endpoints/call_guards.py` | Small |
| 2.10 | **Admin Blocked Entities** — Create `GET|POST /admin/blocked-entities`, `DELETE /admin/blocked-entities/{id}`. | `backend/app/api/v1/endpoints/blocked_entities.py` | Small |
| 2.11 | **Flat Secrets List** — Add `GET /admin/secrets` that aggregates tenant + platform secrets for super-admins. | `backend/app/api/v1/endpoints/secrets.py` | Small |
| 2.12 | **Partner/Tenant Billing** — Create `GET /billing/partners` and `GET /billing/tenants` for admin billing overview. | `backend/app/api/v1/endpoints/billing.py` | Medium |

### Phase 3 — Frontend Refactor (Delete Mock Data)
*Goal: Swap all Talk-Leee admin/billing pages to use `billing-api.ts` hooks (which hit real APIs) and delete `billing-mock-data.ts`.*

| # | Task | Files | Effort |
|---|------|-------|--------|
| 3.1 | **Billing Pages** — Refactor all `app/billing/**/*.tsx` to use `billing-api.ts` hooks (`useBillingPlan`, `useBillingUsage`, `useBillingInvoices`, etc.). | `Talk-Leee/src/app/billing/**` | Medium |
| 3.2 | **Admin Pages** — Refactor all `app/admin/**/*.tsx` to use `billing-api.ts` admin hooks (`useApiKeys`, `useWebhookEndpoints`, `useRateLimitRules`, etc.). | `Talk-Leee/src/app/admin/**` | Medium |
| 3.3 | **Delete Mock File** — Remove `src/lib/billing-mock-data.ts` and all imports. | `Talk-Leee/src/lib/billing-mock-data.ts` | Small |
| 3.4 | **Partner Analytics** — Build `GET /analytics/partners/{partner_id}` (or query param) returning sub-tenant minutes, concurrency, daily usage. Replace `makeMockData`. | `backend/app/api/v1/endpoints/analytics.py` + frontend | Medium |

### Phase 4 — Admin Panel Placeholder Pages & Real-Time
*Goal: Implement empty Admin pages and add real-time streaming where needed.*

| # | Task | Files | Effort |
|---|------|-------|--------|
| 4.1 | **Actions Log Page** — Build table using `GET /admin/audit/logs` (or `GET /admin/audit`). | `Admin/frontend/src/pages/ActionsLogPage.tsx` | Medium |
| 4.2 | **Incidents Page** — Build table + detail/acknowledge/resolve using `GET|POST /admin/incidents`. | `Admin/frontend/src/pages/IncidentsPage.tsx` | Medium |
| 4.3 | **Usage & Cost Page** — Build breakdown charts using `GET /admin/usage/breakdown`. | `Admin/frontend/src/pages/UsageCostPage.tsx` | Medium |
| 4.4 | **User Management Page** — Build list/detail using existing `GET /admin/users`. | New page + route | Medium |
| 4.5 | **System Config Page** — Build provider toggle UI using `GET /admin/configuration` and `PATCH /admin/configuration/providers/{type}`. | New page + route | Medium |
| 4.6 | **Dashboard Live WebSocket/SSE** — Add `EventSource` or WebSocket to Talk-Leee dashboard for live call-volume buckets instead of `useSimulatedLiveBuckets`. Backend needs `GET /dashboard/live-stream` (SSE) or WebSocket pushing bucket updates. | `backend/app/api/v1/endpoints/dashboard.py` + `Talk-Leee/src/app/dashboard/page.tsx` | Large |
| 4.7 | **Campaign Duplicate** — Add `POST /campaigns/{id}/duplicate` backend endpoint and wire frontend. | `backend/app/api/v1/endpoints/campaigns.py` + frontend | Small |

### Phase 5 — Polish & Cleanup

| # | Task | Effort |
|---|------|--------|
| 5.1 | Remove unused API method definitions from `Admin/frontend/src/lib/api.ts` (or implement their pages). | Small |
| 5.2 | Unify endpoint naming conventions (e.g., decide between `/admin/audit-logs` and `/admin/audit/logs`; update frontend or backend to match). | Small |
| 5.3 | Add loading/error states to all newly-connected components. | Medium |
| 5.4 | Write integration tests for new backend endpoints. | Medium |

---

## 6. Priority Recommendation

If you want to go live **fast** with real data everywhere:

1. **Start with Phase 1** (1–2 days) — fixes ~8 disconnects using existing APIs.
2. **Then Phase 2.1–2.4** (2–3 days) — fills the most visible billing gaps so users see real subscription/usage/invoice data.
3. **Then Phase 3** (2 days) — deletes the mock data file from Talk-Leee.
4. **Phase 4.1–4.3** (2–3 days) — removes "Coming Soon" embarrassment from Admin panel.
5. **Phase 4.6** (3–5 days) — only if real-time dashboard charts are a user-facing priority; otherwise keep simulated visuals for now.

---

## 7. Files to Modify (Summary Count)

| Area | Files |
|------|-------|
| Backend new endpoints | ~8–12 new router files + modifications to `billing.py`, `dashboard.py`, `campaigns.py` |
| Talk-Leee frontend | `billing-api.ts`, `billing-mock-data.ts` (delete), `app/billing/**/*.tsx`, `app/admin/**/*.tsx`, `app/dashboard/page.tsx`, `app/campaigns/page.tsx`, `components/auth/mfa-setup.tsx` |
| Admin Panel frontend | `lib/auth.tsx`, `components/LiveCalls.tsx`, `components/Incidents.tsx`, `components/TopTenantsList.tsx`, `components/TopTenantsPanel.tsx`, `components/QuotaUsage.tsx`, `components/Header.tsx`, `pages/ActionsLogPage.tsx`, `pages/IncidentsPage.tsx`, `pages/UsageCostPage.tsx` |
