# Implementation Spec — All 5 Phases

## Backend API Contracts

### Billing (`/billing/*`)
| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/billing/plan` | `{ id, name, price, interval, minutes, features[], status, current_period_start, current_period_end, cancel_at_period_end }` | Current tenant plan |
| GET | `/billing/plans` | `Plan[]` | All available plans |
| POST | `/billing/plan/change` | `{ success, message, new_plan_id }` | Change plan |
| GET | `/billing/usage/summary` | `{ total_used, allocated, remaining, overage, usage_type }` | Alias for `/billing/usage` |
| GET | `/billing/usage/daily` | `{ date, used, allocated }[]` | Daily usage for last 30 days |
| GET | `/billing/invoices` | `Invoice[]` | List invoices (existing) |
| GET | `/billing/invoices/{id}` | `Invoice` | Single invoice detail |
| GET | `/billing/adjustments` | `Adjustment[]` | Billing adjustments |
| POST | `/billing/adjustment` | `{ success, adjustment }` | Create adjustment |
| GET | `/billing/overage-alerts` | `OverageAlert[]` | Overage alerts |
| GET | `/billing/partners` | `PartnerBilling[]` | Partner billing summary (admin) |
| GET | `/billing/tenants` | `TenantBilling[]` | Tenant billing summary (admin) |

### Admin (`/admin/*`)
| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/admin/api-keys` | `ApiKey[]` | List API keys |
| POST | `/admin/api-keys` | `ApiKey` | Create API key |
| POST | `/admin/api-keys/{id}/revoke` | `{ success }` | Revoke API key |
| GET | `/admin/webhooks` | `WebhookEndpoint[]` | List webhook endpoints |
| POST | `/admin/webhooks` | `WebhookEndpoint` | Create webhook |
| DELETE | `/admin/webhooks/{id}` | `{ success }` | Delete webhook |
| POST | `/admin/webhooks/{id}/test` | `{ success, delivery_id }` | Test webhook |
| GET | `/admin/webhooks/deliveries` | `WebhookDelivery[]` | Delivery history |
| GET | `/admin/rate-limits` | `RateLimitRule[]` | Rate limit rules |
| POST | `/admin/rate-limits` | `RateLimitRule` | Create rule |
| PATCH | `/admin/rate-limits/{id}` | `RateLimitRule` | Update rule status |
| GET | `/admin/call-guards` | `CallGuardRule[]` | Call guard rules |
| PATCH | `/admin/call-guards/{id}` | `CallGuardRule` | Toggle rule |
| GET | `/admin/tenant-limits` | `TenantLimit[]` | Tenant limits |
| PUT | `/admin/tenant-limits/{tenantId}` | `{ success }` | Update tenant limit |
| GET | `/admin/partner-limits` | `PartnerLimit[]` | Partner limits |
| PUT | `/admin/partner-limits/{partnerId}` | `{ success }` | Update partner limit |
| GET | `/admin/abuse-events` | `AbuseEvent[]` | Abuse events (alias to `/admin/abuse/events`) |
| GET | `/admin/blocked-entities` | `BlockedEntity[]` | Blocked IPs/numbers |
| POST | `/admin/blocked-entities` | `BlockedEntity` | Block entity |
| DELETE | `/admin/blocked-entities/{id}` | `{ success }` | Unblock entity |
| GET | `/admin/secrets` | `Secret[]` | Flat list of all secrets (super-admin) |
| POST | `/admin/secrets/{id}/rotate` | `{ success }` | Rotate secret |

### Dashboard
| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/dashboard/live-buckets` | `{ buckets: { timestamp, total_calls, answered, failed, active_calls, queue_size }[] }` | Time-series for last 48h |

### Campaigns
| Method | Path | Response | Description |
|--------|------|----------|-------------|
| POST | `/campaigns/{id}/duplicate` | `Campaign` | Duplicate campaign |

### Analytics
| Method | Path | Response | Description |
|--------|------|----------|-------------|
| GET | `/analytics/partners/{partner_id}` | `{ minutes, concurrency, daily_usage }` | Partner analytics |

## Frontend Contracts

### Talk-Leee `billing-api.ts` — Update ALL hooks to call the exact backend paths above. Remove all fallback to mock data. On error, return `null` or empty array, NOT mock data.

### Talk-Leee pages to refactor
- `app/billing/page.tsx` → use `useBillingPlan`, `useBillingUsage`, `useDailyUsage`, `useBillingInvoices`, `useBillingAdjustments`, `useOverageAlerts`
- `app/billing/plans/page.tsx` → use `useBillingPlans`, `useBillingPlan`
- `app/billing/invoices/page.tsx` → use `useBillingInvoices`
- `app/billing/invoices/[id]/page.tsx` → use `useBillingInvoice(id)`
- `app/admin/audit-logs/page.tsx` → use backend `GET /admin/audit/logs` (path fix)
- `app/admin/api-keys/page.tsx` → use `useApiKeys()`
- `app/admin/webhooks/page.tsx` → use `useWebhookEndpoints()`, `useWebhookDeliveries()`
- `app/admin/rate-limiting/page.tsx` → use `useRateLimitRules()`
- `app/admin/voice-security/page.tsx` → use `useCallGuardRules()`, `useTenantLimits()`, `usePartnerLimits()`
- `app/admin/secrets/page.tsx` → use `useSecrets()`
- `app/admin/abuse-detection/page.tsx` → use `useAbuseEvents()`, `useBlockedEntities()`
- `app/admin/billing/page.tsx` → use `usePartnerBilling()`
- `app/admin/billing/tenants/page.tsx` → use `useTenantBilling()`
- `app/dashboard/page.tsx` → replace `useSimulatedLiveBuckets` with `useLiveBuckets()` calling `/dashboard/live-buckets`
- `app/campaigns/page.tsx` → call `POST /campaigns/{id}/duplicate` on duplicate action
- `components/auth/mfa-setup.tsx` → call `POST /auth/mfa/setup`
- **DELETE** `src/lib/billing-mock-data.ts`

### Admin Panel
- `src/lib/auth.tsx` → `USE_DUMMY_AUTH = false` (or env-gated). Call `api.login()`, `api.verifyToken()`.
- `src/components/LiveCalls.tsx` → call `api.getLiveCalls()` instead of `mockCalls`
- `src/components/Incidents.tsx` → call `api.getIncidents()`
- `src/components/TopTenantsList.tsx` → call `api.getSystemAnalytics()` or `api.getTenants()`
- `src/components/TopTenantsPanel.tsx` → call `api.getConnectors()`
- `src/components/QuotaUsage.tsx` → call `api.getUsageSummary()`
- `src/components/Header.tsx` → show real user name from auth context
- `src/pages/ActionsLogPage.tsx` → build table with `api.getAuditLog()`
- `src/pages/IncidentsPage.tsx` → build table with `api.getIncidents()`, ack/resolve
- `src/pages/UsageCostPage.tsx` → build charts with `api.getUsageBreakdown()`
- Add `src/pages/UsersPage.tsx` → `api.getUsers()`
- Add `src/pages/SystemConfigPage.tsx` → `api.getConfiguration()`, `api.updateProviderConfig()`
- Update `src/App.tsx` with new routes
- Update `src/components/Sidebar.tsx` with new nav links
