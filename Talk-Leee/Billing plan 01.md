# Frontend Implementation Plan - Talk-Lee Billing Layer UI
**Project:** Talk-Lee (Frontend Only)  
**Date:** 2026-04-14  
**Version:** 4.0 (FULLY COMPLETED)  
**Status:** ALL FRONTEND FEATURES 100% IMPLEMENTED

---

## Executive Summary

This document outlines the complete frontend implementation for the Billing & Security Layer as per `Plan.txt`. The frontend now includes **all UI/UX components** covering every DAY (1-8) of the security plan and the entire billing layer:

**Overall Frontend Completion: 100% (35 out of 35 features fully implemented)**

---

## Part 1: Authentication & Security Layer (DAY 1-5)

### 1. Login Form Component - Email + OTP

**Status:** IMPLEMENTED

**Files:**
- `src/app/auth/login/login-client.tsx`
- `src/app/auth/login/page.tsx`

**What Was Implemented:**
- Email input with OTP verification code flow
- Step-based navigation (email > OTP)
- Token storage (access_token + refresh_token)
- Role-based redirect after login (white_label_admin > /white-label/dashboard, others > /dashboard)
- MFA verification step support
- Passkey login option with "Sign in with Passkey" button
- Dynamic step management for: email > otp > mfa > passkey
- Error handling for all authentication methods

**Integration:**
- URL: `/auth/login`

---

### 2. Login Form Component - Password

**Status:** IMPLEMENTED

**Files:**
- `src/app/auth/login/login-client.tsx`

**What Was Implemented:**
- Toggle between "Sign in with Email Code" and "Sign in with Password" on the login page
- Email > Password two-step flow
- Password input with lock icon and autocomplete support
- MFA check after successful password authentication
- Same role-based redirect logic as OTP flow
- Calls `POST /api/v1/auth/login/password` endpoint
- Error handling for invalid credentials

**Integration:**
- URL: `/auth/login` (toggle via "Sign in with Password" link)

---

### 3. MFA Verification Component

**Status:** IMPLEMENTED

**File:** `src/components/auth/mfa-verification.tsx`

**What Was Implemented:**
- TOTP (Time-based One-Time Password) code input (6-8 digits)
- Real-time countdown timer showing code expiration (30s)
- Numeric-only input with auto-formatting
- Loading states during verification
- Error handling with user feedback
- Back button to return to login
- Recovery code fallback suggestion

**Integration:**
- Called from: `src/app/auth/login/login-client.tsx` (step === "mfa")

---

### 4. MFA Setup/Enable Component

**Status:** IMPLEMENTED

**File:** `src/components/auth/mfa-setup.tsx`

**What Was Implemented:**
- Three-step setup wizard (QR Code > Verify > Recovery Codes)
- QR code display for authenticator apps (Google Authenticator, Authy)
- Manual entry key display with copy-to-clipboard
- TOTP code verification during setup
- Recovery codes generation and display (8 codes)
- Download recovery codes as text file
- Tab-based navigation between steps

**Integration:**
- Called from: `src/app/settings/page.tsx` (Security tab)

---

### 5. Passkey Registration Component

**Status:** IMPLEMENTED

**File:** `src/components/auth/passkey-registration.tsx`

**What Was Implemented:**
- Two-step registration (intro > name > register)
- WebAuthn support detection and platform authenticator check
- Passkey naming for device identification
- WebAuthn credential creation flow via browser API
- Error handling for cancellation and unsupported devices

**Integration:**
- Called from: `src/app/settings/page.tsx` (Security tab)

---

### 6. Passkey Login Component

**Status:** IMPLEMENTED

**File:** `src/components/auth/passkey-login.tsx`

**What Was Implemented:**
- Single-button passkey login interface
- WebAuthn support detection
- Browser authenticator UI (biometric/PIN dialog)
- Credential assertion flow
- Error handling with user-friendly messages

**Integration:**
- Called from: `src/app/auth/login/login-client.tsx`

---

### 7. Device/Session List

**Status:** IMPLEMENTED

**File:** `src/components/auth/device-list.tsx`

**What Was Implemented:**
- List of all active sessions/devices
- Device icon based on type (mobile/desktop), name, browser, last activity, IP address
- "Current" badge for active device
- Logout from individual device button
- "Sign out from all other devices" button
- Human-readable timestamps

**Integration:**
- Called from: `src/app/settings/page.tsx` (Devices tab)

---

### 8. Logout Button

**Status:** IMPLEMENTED

**File:** `src/components/auth/logout-button.tsx`

**What Was Implemented:**
- Logout button with loading state
- API call to invalidate session
- Local storage cleanup (tokens removed)
- Automatic redirect to login page

**Integration:**
- Called from: `src/app/settings/page.tsx` (Logout tab)

---

### 9. Role-Based Conditional UI Rendering

**Status:** IMPLEMENTED

**Files:**
- `src/lib/auth-roles.ts` - Role definitions, hierarchy, and permission utilities
- `src/hooks/useAuth.ts` - React hook wrapping AuthContext + role checking
- `src/components/auth/role-based-render.tsx` - Component for conditional rendering

**Role Hierarchy:**
```
readonly (1) > user (2) > tenant_admin (3) > partner_admin (4) > white_label_admin (4) > platform_admin (5)
```

**Permission Matrix:**

| Feature | readonly | user | tenant_admin | partner_admin | white_label_admin | platform_admin |
|---------|----------|------|-------------|---------------|-------------------|---|
| Manage Users | No | No | Yes | Yes | Yes | Yes |
| Manage Billing | No | No | Yes | Yes | Yes | Yes |
| View Analytics | No | Yes | Yes | Yes | Yes | Yes |
| Manage Settings | Yes | Yes | Yes | Yes | Yes | Yes |
| Access Admin | No | No | Yes | Yes | Yes | Yes |
| Create Tenants | No | No | No | Yes | Yes | Yes |
| Manage Partners | No | No | No | No | No | Yes |

---

### 10. User Settings/Profile Page

**Status:** IMPLEMENTED

**File:** `src/app/settings/page.tsx`

**What Was Implemented:**
- Tab-based navigation: Profile, Security, Devices, Logout
- Profile tab: Name and email inputs
- Security tab: MFA Setup + Passkey Registration
- Devices tab: DeviceList component
- Logout tab: LogoutButton component

**Integration:**
- URL: `/settings`

---

## Part 2: Billing Layer UI

### 11. Billing Plan Display

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (PlanDisplay section)

**What Was Implemented:**
- Current plan name, tier, and price display
- Billing cycle date range
- Included minutes and concurrent calls
- Billing state badge (active/trialing/past_due/grace_period/suspended/canceled)
- "Change Plan" button linking to `/billing/plans`
- Next invoice date display

---

### 12. Usage Summary Dashboard

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (UsageSummarySection)

**What Was Implemented:**
- KPI grid: Total Calls, Successful, Failed, Average Duration
- Daily minutes bar chart (last 14 days)
- Date range labels on chart

---

### 13. Minutes Tracking Widget

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (MinutesTracker)

**What Was Implemented:**
- Large counter showing minutes used vs included
- Remaining minutes display
- Progress bar with color thresholds (green <75%, amber 75-90%, red 90%+)
- Overage minutes indicator when exceeded

---

### 14. Concurrent Calls Meter

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (ConcurrencyMeter)

**What Was Implemented:**
- Peak concurrent calls display vs limit
- Progress bar with color thresholds
- Available slots count

---

### 15. Overage Indicators/Alerts

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (OverageAlerts)

**What Was Implemented:**
- Real overage alerts when usage exceeds plan limits
- Smart near-limit warnings at 85%+ usage
- Color-coded severity (amber for warning, red for critical)
- Estimated overage charge display

---

### 16. Invoice List Page

**Status:** IMPLEMENTED

**File:** `src/app/billing/invoices/page.tsx`

**What Was Implemented:**
- Full invoice history table
- Status badges (paid/open/past_due/void/draft)
- Download button per invoice

**Integration:**
- URL: `/billing/invoices`

---

### 17. Invoice Detail Page

**Status:** IMPLEMENTED

**File:** `src/app/billing/invoices/[id]/page.tsx`

**What Was Implemented:**
- Full invoice breakdown matching Plan.txt Section 13
- Line items table
- Adjustments/credits subtotal
- Print and download buttons
- Invoice status badge

**Integration:**
- URL: `/billing/invoices/[id]`

---

### 18. Payment Status Display

**Status:** IMPLEMENTED

**Files:** All billing pages

**What Was Implemented:**
- Billing state badges covering all Plan.txt Section 10 states (active, trialing, past_due, grace_period, suspended, canceled)
- Invoice status badges (paid, open, past_due, void, draft)

---

### 19. Plan Selection/Upgrade UI

**Status:** IMPLEMENTED

**File:** `src/app/billing/plans/page.tsx`

**What Was Implemented:**
- 4-tier plan cards: Starter, Professional, Business, Enterprise
- Monthly/Yearly billing toggle with savings label
- Feature list per plan with checkmarks
- Overage pricing per plan
- Upgrade/Downgrade buttons

**Integration:**
- URL: `/billing/plans`

---

### 20. Billing Adjustments/Credits Display

**Status:** IMPLEMENTED

**File:** `src/app/billing/page.tsx` (AdjustmentsList)

**What Was Implemented:**
- Table of all adjustments
- Color-coded type badges: Credit, Refund, Charge, Promo

---

### 21. Partner Billing Aggregation View

**Status:** IMPLEMENTED

**File:** `src/app/admin/billing/page.tsx`

**What Was Implemented:**
- Summary KPI cards: Total Revenue, Total Minutes, Total Tenants, Overage Charges
- Partner table with progress bars

**Integration:**
- URL: `/admin/billing`

---

### 22. Tenant Billing Breakdown View

**Status:** IMPLEMENTED

**File:** `src/app/admin/billing/tenants/page.tsx`

**What Was Implemented:**
- Tenant table with usage progress bars
- Payment status badges per tenant

**Integration:**
- URL: `/admin/billing/tenants`

---

### 23. Audit Log Viewer

**Status:** IMPLEMENTED

**File:** `src/app/admin/audit-logs/page.tsx`

**What Was Implemented:**
- Category filter buttons: All, Auth, Billing, Role, Suspension, Settings, Security
- Log table with severity badges
- Admin-only page

**Integration:**
- URL: `/admin/audit-logs`

---

## Part 3: API Security & Rate Limiting (DAY 6) — NEW

### 24. API Key Management Page

**Status:** IMPLEMENTED

**File:** `src/app/admin/api-keys/page.tsx`

**What Was Implemented:**
- Summary bar: Total Keys, Active, Revoked counts
- "Create API Key" button with modal form (name, scopes checkboxes, rate limit, expiration)
- Scope options: calls:read, calls:write, campaigns:read, contacts:read, contacts:write, analytics:read, webhooks:write
- Table: Name, Key Prefix (monospace), Status badge, Scopes (pills), Rate Limit, Created, Last Used, Expires, Actions
- Status badges: active=green, revoked=red, expired=gray
- Revoke button with confirmation dialog
- Admin-only (platform_admin, partner_admin)

**Integration:**
- URL: `/admin/api-keys`
- Sidebar: "API Keys" (admin only)

---

### 25. Webhook Management Page

**Status:** IMPLEMENTED

**File:** `src/app/admin/webhooks/page.tsx`

**What Was Implemented:**
- Summary cards: Total Endpoints, Active, Failing
- "Add Endpoint" button with form (URL, description, event type multi-select)
- Endpoint table: URL, Description, Events count, Status badge, Failure Count, Last Delivery, Actions (Edit, Delete, Test)
- Recent Deliveries table: Event Type pill, Endpoint, Status, Response Code, Duration, Attempts, Timestamp
- Expandable payload preview (JSON)
- Signature verification info card with masked secrets
- "Rotate Secret" button per endpoint
- 12 webhook event types supported: call.started, call.ended, call.failed, billing.invoice_created, billing.payment_received, billing.payment_failed, billing.plan_changed, tenant.created, tenant.suspended, partner.suspended, security.mfa_enabled, security.login_failed

**Integration:**
- URL: `/admin/webhooks`
- Sidebar: "Webhooks" (admin only)

---

### 26. Rate Limiting Configuration Page

**Status:** IMPLEMENTED

**File:** `src/app/admin/rate-limiting/page.tsx`

**What Was Implemented:**
- Summary KPIs: Total Rules, Active Rules, Inactive Rules, Total Blocked
- "Add Rule" button with form (Name, Scope, Endpoint, Max Requests, Window, Burst Limit, Action)
- Rule table: Name, Scope badge, Endpoint, Limit display, Burst, Action badge, Status toggle (Switch), Usage progress bar, Actions
- Scope badges: per_user=blue, per_tenant=purple, per_ip=amber, global=gray
- Action badges: reject=red, throttle=amber, log_only=blue
- Usage progress bar with color coding (green <50%, amber 50-80%, red >80%)
- Toggle switch to enable/disable rules
- Idempotency support via rate limit configuration

**Integration:**
- URL: `/admin/rate-limiting`
- Sidebar: "Rate Limiting" (admin only)

---

## Part 4: Voice Security & Abuse Protection (DAY 7) — NEW

### 27. Voice Security & Call Guards Page

**Status:** IMPLEMENTED

**File:** `src/app/admin/voice-security/page.tsx`

**What Was Implemented:**
- Three tabs: Call Guards, Tenant Limits, Partner Limits

**Call Guards Tab:**
- Pre-call check rules table: Priority, Name, Check Type badge, Description, Action badge (block/warn/log_only), Enabled toggle, Last Triggered, Trigger Count
- 7 guard types: tenant_active, partner_active, concurrency_limit, rate_limit, allowed_feature, billing_active, caller_whitelist
- Edit action type inline
- Execution order note

**Tenant Limits Tab:**
- Table: Tenant Name, Status badge, Max Concurrent Calls, Calls/Min, Calls/Hour, Max Duration, Allowed Features (pills), Edit button
- Inline editing for all limit values
- Status display: active=green, suspended=red, restricted=amber

**Partner Limits Tab:**
- Table: Partner Name, Status badge, Max Tenants, Max Total Concurrent, Calls/Min, Calls/Hour, Allowed Features (pills), Edit button
- Same edit pattern as tenant limits

**Integration:**
- URL: `/admin/voice-security`
- Sidebar: "Voice Security" (admin only)

---

### 28. Abuse Detection Dashboard

**Status:** IMPLEMENTED

**File:** `src/app/admin/abuse-detection/page.tsx`

**What Was Implemented:**
- Summary KPIs: Total Events, Open, Critical, Blocked Entities
- Two tabs: Abuse Events, Blocked Entities

**Abuse Events Tab:**
- Filter buttons: All, Open, Investigating, Resolved, Dismissed
- Table: Severity badge (low/medium/high/critical), Type (formatted), Tenant, Source IP, Description, Detected At, Status badge, Actions
- Expandable detail rows: Call Count, Action Taken, Metadata key-value display
- 8 abuse types: rapid_dialing, concurrent_flood, unusual_destination, short_duration_spam, after_hours_spike, credential_stuffing, api_scraping, geo_anomaly

**Blocked Entities Tab:**
- "Block Entity" button with form: Type (ip/phone_number/tenant/user), Value, Reason, Expiry
- Table: Type badge, Value (monospace), Reason, Blocked By, Blocked At, Expires, Status badge, Unblock action
- Entity types: IP, Phone Number, Tenant, User

**Integration:**
- URL: `/admin/abuse-detection`
- Sidebar: "Abuse Detection" (admin only)

---

## Part 5: Audit Logs, Suspension & Secrets (DAY 8) — COMPLETED

### 29. Admin Operations Console

**Status:** IMPLEMENTED

**File:** `src/components/admin/admin-operations-console.tsx`

**What Was Implemented:**
- Four tabs: Audit Logs, Security Events, Suspensions, Configuration
- Pagination (20 per page)
- Partner/Tenant suspension management with confirm dialogs
- Real API integration via hooks

---

### 30. Suspension System

**Status:** IMPLEMENTED

**File:** `src/components/admin/suspension-state-provider.tsx`

**What Was Implemented:**
- Real-time suspension state tracking
- Partner and tenant level suspension
- BroadcastChannel for cross-tab synchronization
- 30-second polling for updates
- Suspension banner with scope and reason display
- Instant block propagation via cross-tab sync

---

### 31. Secrets / Environment Management Page

**Status:** IMPLEMENTED

**File:** `src/app/admin/secrets/page.tsx`

**What Was Implemented:**
- Security notice banner (secrets are masked, never exposed in frontend)
- Summary KPIs: Total Secrets, Production count, Staging count, Needs Rotation count
- Environment filter: All, Production, Staging, Development
- "Add Secret" form: Name (auto-uppercase), Category dropdown, Environment, Rotation Interval, Description
- Table: Name (monospace), Category badge, Masked Value (monospace), Environment badge, Last Rotated, Rotation Interval, Rotation Status (OK/Overdue indicator), Updated By, Rotate action
- 8 secret categories: api_key, database, payment, voice_provider, email, storage, monitoring, other
- Rotation status: calculates if overdue based on lastRotatedAt + rotationIntervalDays
- Rotate button with confirmation dialog
- Platform admin only access (strictest restriction)
- Note: "Value will be set securely via backend CLI"

**Integration:**
- URL: `/admin/secrets`
- Sidebar: "Secrets" (admin only)

---

## Part 6: Billing API Integration Layer — NEW

### 32. Billing API Hooks

**Status:** IMPLEMENTED

**File:** `src/lib/billing-api.ts`

**What Was Implemented:**
- `billingFetch` helper: tries real API endpoints first, falls back to mock data
- Auth token included from localStorage
- 20 query hooks with mock fallback:
  - `useBillingPlan()`, `useBillingUsage()`, `useDailyUsage()`
  - `useBillingInvoices()`, `useBillingInvoice(id)`
  - `useBillingPlans()`, `useBillingAdjustments()`, `useOverageAlerts()`
  - `usePartnerBilling()`, `useTenantBilling()`
  - `useApiKeys()`, `useWebhookEndpoints()`, `useWebhookDeliveries()`
  - `useRateLimitRules()`, `useCallGuardRules()`
  - `useTenantLimits()`, `usePartnerLimits()`
  - `useAbuseEvents()`, `useBlockedEntities()`, `useSecrets()`
- 15 mutation hooks:
  - `useCreateApiKey()`, `useRevokeApiKey()`
  - `useCreateWebhook()`, `useDeleteWebhook()`, `useTestWebhook()`
  - `useCreateRateLimitRule()`, `useToggleRateLimitRule()`
  - `useToggleCallGuard()`
  - `useUpdateTenantLimit()`, `useUpdatePartnerLimit()`
  - `useBlockEntity()`, `useUnblockEntity()`
  - `useRotateSecret()`
  - `useChangePlan()`, `useCreateAdjustment()`
- All mutations invalidate relevant queries on success
- Query key constants for cache management

---

### 33. Billing Type Definitions

**Status:** IMPLEMENTED

**File:** `src/lib/billing-types.ts`

**Types Defined:**
- `BillingState`, `InvoiceStatus`, `OverageType`
- `BillingPlan`, `TenantPlan`, `UsageSummary`, `UsageLedgerEntry`
- `Invoice`, `InvoiceLineItem`, `BillingAdjustment`, `OverageAlert`
- `PartnerBillingSummary`, `TenantBillingSummary`
- `AuditLogEntry`
- `ApiKey` (DAY 6)
- `WebhookEndpoint`, `WebhookDelivery`, `WebhookEventType` (DAY 6)
- `RateLimitRule`, `RateLimitScope` (DAY 6)
- `CallGuardRule`, `TenantLimit`, `PartnerLimit` (DAY 7)
- `AbuseEvent`, `AbuseEventType`, `AbuseSeverity`, `BlockedEntity` (DAY 7)
- `SecretEntry`, `SecretCategory` (DAY 8)

---

### 34. Mock Data System

**Status:** IMPLEMENTED

**File:** `src/lib/billing-mock-data.ts`

**Mock Data Sets:**
- `PLANS` — 4 billing plans
- `CURRENT_TENANT_PLAN` — Active tenant plan
- `CURRENT_USAGE` — Monthly usage summary
- `DAILY_USAGE` — 14-day daily usage array
- `OVERAGE_ALERTS` — Overage warning alerts
- `ADJUSTMENTS` — 4 billing adjustments
- `INVOICES` — 3 invoices with line items
- `PARTNER_BILLING` — 4 partner summaries
- `TENANT_BILLING` — 6 tenant summaries
- `AUDIT_LOGS` — 12 audit log entries
- `API_KEYS` — 5 API keys (DAY 6)
- `WEBHOOK_ENDPOINTS` — 4 webhook endpoints (DAY 6)
- `WEBHOOK_DELIVERIES` — 8 delivery records (DAY 6)
- `RATE_LIMIT_RULES` — 7 rate limit rules (DAY 6)
- `CALL_GUARD_RULES` — 7 call guard rules (DAY 7)
- `TENANT_LIMITS` — 6 tenant limit configs (DAY 7)
- `PARTNER_LIMITS` — 4 partner limit configs (DAY 7)
- `ABUSE_EVENTS` — 8 abuse events (DAY 7)
- `BLOCKED_ENTITIES` — 6 blocked entities (DAY 7)
- `SECRETS` — 12 environment secrets (DAY 8)

---

### 35. Sidebar Navigation Updates

**Status:** IMPLEMENTED

**File:** `src/components/layout/sidebar.tsx`

**New Admin Navigation Items:**
- API Keys (`/admin/api-keys`) — Key icon
- Webhooks (`/admin/webhooks`) — Webhook icon
- Rate Limiting (`/admin/rate-limiting`) — Gauge icon
- Voice Security (`/admin/voice-security`) — ShieldCheck icon
- Abuse Detection (`/admin/abuse-detection`) — ShieldAlert icon
- Secrets (`/admin/secrets`) — Lock icon

All admin links are role-gated (adminOnly: true).

---

## Supporting Files

### Utility Files

| File | Type | Purpose |
|------|------|---------|
| `src/lib/billing-types.ts` | Types | All billing + security TypeScript interfaces |
| `src/lib/billing-mock-data.ts` | Mock Data | All mock data for development |
| `src/lib/billing-api.ts` | API Hooks | React Query hooks with mock fallback |
| `src/lib/mfa-utils.ts` | Utilities | TOTP validation, recovery codes |
| `src/lib/webauthn-utils.ts` | Utilities | WebAuthn support, passkey flows |
| `src/lib/session-utils.ts` | Utilities | Session management, device detection |
| `src/lib/auth-roles.ts` | Utilities | Role hierarchy, permissions |

### Page Files

| File | Route | Purpose |
|------|-------|---------|
| `src/app/auth/login/login-client.tsx` | `/auth/login` | Login (OTP + Password + Passkey + MFA) |
| `src/app/settings/page.tsx` | `/settings` | Profile, Security, Devices, Logout tabs |
| `src/app/billing/page.tsx` | `/billing` | Billing dashboard |
| `src/app/billing/invoices/page.tsx` | `/billing/invoices` | Invoice list |
| `src/app/billing/invoices/[id]/page.tsx` | `/billing/invoices/[id]` | Invoice detail |
| `src/app/billing/plans/page.tsx` | `/billing/plans` | Plan selection |
| `src/app/admin/page.tsx` | `/admin` | Admin operations console |
| `src/app/admin/billing/page.tsx` | `/admin/billing` | Partner billing (admin) |
| `src/app/admin/billing/tenants/page.tsx` | `/admin/billing/tenants` | Tenant billing (admin) |
| `src/app/admin/audit-logs/page.tsx` | `/admin/audit-logs` | Audit log viewer (admin) |
| `src/app/admin/api-keys/page.tsx` | `/admin/api-keys` | API key management (admin) |
| `src/app/admin/webhooks/page.tsx` | `/admin/webhooks` | Webhook management (admin) |
| `src/app/admin/rate-limiting/page.tsx` | `/admin/rate-limiting` | Rate limiting config (admin) |
| `src/app/admin/voice-security/page.tsx` | `/admin/voice-security` | Call guards & limits (admin) |
| `src/app/admin/abuse-detection/page.tsx` | `/admin/abuse-detection` | Abuse detection (admin) |
| `src/app/admin/secrets/page.tsx` | `/admin/secrets` | Secrets management (platform_admin) |

---

## API Endpoints Expected from Backend

### Authentication
- `POST /api/v1/auth/login` - Send email for OTP
- `POST /api/v1/auth/login/password` - Password login
- `POST /api/v1/auth/verify-otp` - Verify OTP code
- `GET /api/v1/auth/me` - Get current user info
- `POST /api/v1/auth/logout` - Logout current session

### MFA
- `POST /api/v1/auth/mfa/setup/start` - Start MFA setup
- `POST /api/v1/auth/mfa/setup/verify` - Verify MFA code during setup
- `POST /api/v1/auth/mfa/verify` - Verify MFA during login
- `POST /api/v1/auth/mfa/disable` - Disable MFA
- `POST /api/v1/auth/mfa/recovery-codes/regenerate` - Regenerate recovery codes

### Passkeys
- `POST /api/v1/auth/passkey/register/start` - Start passkey registration
- `POST /api/v1/auth/passkey/register/complete` - Complete passkey registration
- `POST /api/v1/auth/passkey/authenticate/start` - Start passkey login
- `POST /api/v1/auth/passkey/authenticate/complete` - Complete passkey login
- `GET /api/v1/auth/passkeys` - List user passkeys
- `DELETE /api/v1/auth/passkeys/{id}` - Delete a passkey

### Sessions
- `GET /api/v1/auth/sessions` - Get all active sessions
- `POST /api/v1/auth/sessions/{id}/logout` - Logout specific session
- `POST /api/v1/auth/sessions/logout-all-others` - Logout all but current

### Billing
- `GET /api/v1/billing/plan` - Get current tenant plan
- `GET /api/v1/billing/usage/summary` - Get usage summary
- `GET /api/v1/billing/usage/daily` - Get daily usage
- `GET /api/v1/billing/invoices` - List invoices
- `GET /api/v1/billing/invoices/{id}` - Get invoice detail
- `POST /api/v1/billing/adjustment` - Create billing adjustment
- `GET /api/v1/billing/plans` - List available plans
- `POST /api/v1/billing/plan/change` - Change plan
- `GET /api/v1/billing/adjustments` - List adjustments
- `GET /api/v1/billing/overage-alerts` - Get overage alerts
- `GET /api/v1/billing/partners` - Partner billing aggregation (admin)
- `GET /api/v1/billing/tenants` - Tenant billing breakdown (admin)

### API Keys (DAY 6)
- `GET /api/v1/admin/api-keys` - List API keys
- `POST /api/v1/admin/api-keys` - Create API key
- `POST /api/v1/admin/api-keys/{id}/revoke` - Revoke API key

### Webhooks (DAY 6)
- `GET /api/v1/admin/webhooks` - List webhook endpoints
- `POST /api/v1/admin/webhooks` - Create webhook endpoint
- `DELETE /api/v1/admin/webhooks/{id}` - Delete webhook endpoint
- `POST /api/v1/admin/webhooks/{id}/test` - Test webhook delivery
- `GET /api/v1/admin/webhooks/deliveries` - List webhook deliveries

### Rate Limiting (DAY 6)
- `GET /api/v1/admin/rate-limits` - List rate limit rules
- `POST /api/v1/admin/rate-limits` - Create rate limit rule
- `PATCH /api/v1/admin/rate-limits/{id}` - Toggle rate limit rule

### Voice Security (DAY 7)
- `GET /api/v1/admin/call-guards` - List call guard rules
- `PATCH /api/v1/admin/call-guards/{id}` - Toggle call guard
- `GET /api/v1/admin/tenant-limits` - List tenant limits
- `PUT /api/v1/admin/tenant-limits/{id}` - Update tenant limit
- `GET /api/v1/admin/partner-limits` - List partner limits
- `PUT /api/v1/admin/partner-limits/{id}` - Update partner limit

### Abuse Detection (DAY 7)
- `GET /api/v1/admin/abuse-events` - List abuse events
- `GET /api/v1/admin/blocked-entities` - List blocked entities
- `POST /api/v1/admin/blocked-entities` - Block entity
- `DELETE /api/v1/admin/blocked-entities/{id}` - Unblock entity

### Secrets Management (DAY 8)
- `GET /api/v1/admin/secrets` - List secrets (masked)
- `POST /api/v1/admin/secrets/{id}/rotate` - Rotate secret

### Audit
- `GET /api/v1/audit/logs` - Get audit logs with optional category filter

---

## Plan.txt Coverage Map

| Plan.txt Section | Frontend Status | Detail |
|---|---|---|
| DAY 1 — Core Auth | 100% | Login (OTP + Password), sessions, logout |
| DAY 2 — MFA (TOTP) | 100% | Setup, verify, recovery codes |
| DAY 3 — Passkeys | 100% | Registration, login, detection |
| DAY 4 — Roles + Tenant Isolation | 100% | RBAC, guards, role-based render |
| DAY 5 — Session Security | 100% | Device list, session management |
| DAY 6 — API Security + Rate Limiting | 100% | API keys, webhooks, rate limiting, idempotency |
| DAY 7 — Voice Security + Abuse Protection | 100% | Call guards, tenant/partner limits, abuse detection |
| DAY 8 — Audit + Suspension + Secrets | 100% | Audit logs, suspension system, secrets management |
| Billing Layer — UI/Display | 100% | All pages, components, types |
| Billing Layer — API Integration | 100% | React Query hooks with mock fallback for all endpoints |

---

## Build Status

- Build: Successful (zero errors)
- TypeScript: Full type safety, zero errors
- All pages responsive (mobile, tablet, desktop)
- Dark mode: Supported throughout
- Accessibility: ARIA labels, keyboard navigation
- All admin pages role-gated

---

**Document Version:** 4.0 (FULLY COMPLETED)  
**Last Updated:** 2026-04-14  
**Status:** ALL FRONTEND FEATURES 100% IMPLEMENTED  
**Completion Rate:** 100% (35/35 features)
