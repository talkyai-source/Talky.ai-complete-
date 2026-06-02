# Talky.ai API Endpoint Mapping

> Complete mapping of backend endpoints to frontend (Talk-Leee) and admin panel consumers.
> Auto-generated from codebase analysis. Updated: 2026-05-04

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Connected to real backend endpoint (direct) |
| 🔌 | Connected via Next.js proxy to backend |
| ⚪ | Backend endpoint exists, no frontend consumer yet |
| 🔄 | WebSocket endpoint |
| 📍 | Handled locally in Next.js proxy (not forwarded to backend) |

---

## Backend Endpoint Inventory

All backend routes are mounted under `/api/v1` unless noted otherwise.

### Auth (`/auth`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/auth/register` | `route.ts` (local handler) | 📍 |
| `POST` | `/auth/login` | `route.ts` (local handler) | 📍 |
| `GET`  | `/auth/me` | `api.ts`, `admin/api.ts`, `middleware.ts`, `server-auth.ts` | ✅ |
| `PATCH`| `/auth/me` | `api.ts` | ✅ |
| `POST` | `/auth/logout` | `api.ts`, `route.ts` (local) | 📍 |
| `POST` | `/auth/logout-all` | `api.ts` | ✅ |
| `POST` | `/auth/passkey-check` | `api.ts`, `passkeys.ts` | ✅ |
| `POST` | `/auth/change-password` | `api.ts` | ✅ |
| `GET`  | `/auth/verify-email` | — | ⚪ |

### MFA (`/auth/mfa`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/auth/mfa/setup` | `mfa-utils.ts`, `mfa-setup.tsx` | ✅ |
| `POST` | `/auth/mfa/confirm` | `mfa-setup.tsx` | ✅ |
| `POST` | `/auth/mfa/verify` | `mfa-utils.ts` | ✅ |
| `GET`  | `/auth/mfa/status` | `api.ts` | ✅ |
| `POST` | `/auth/mfa/disable` | `mfa-utils.ts` | ✅ |
| `POST` | `/auth/mfa/recovery-codes/regenerate` | `mfa-utils.ts` | ✅ |

### Passkeys (`/auth/passkeys`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/auth/passkeys/register/begin` | `passkey-registration.tsx`, `passkeys.ts` | ✅ |
| `POST` | `/auth/passkeys/register/complete` | `passkey-registration.tsx`, `passkeys.ts` | ✅ |
| `POST` | `/auth/passkeys/login/begin` | `passkey-login.tsx`, `passkeys.ts` | ✅ |
| `POST` | `/auth/passkeys/login/complete` | `passkey-login.tsx`, `passkeys.ts` | ✅ |
| `GET`  | `/auth/passkeys` | `api.ts`, `passkeys.ts` | ✅ |
| `PATCH`| `/auth/passkeys/{id}` | `api.ts`, `passkeys.ts` | ✅ |
| `DELETE`| `/auth/passkeys/{id}` | `api.ts`, `passkeys.ts` | ✅ |

### Campaigns (`/campaigns`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/campaigns` | `campaigns/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/campaigns` | `campaigns/new/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/campaigns/{id}` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/campaigns/{id}/start` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/campaigns/{id}/pause` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/campaigns/{id}/stop` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/campaigns/{id}/stats` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/campaigns/{id}/calls` | `extended-api.ts` | ✅ |
| `GET`  | `/campaigns/{id}/jobs` | — | ⚪ |
| `GET`  | `/campaigns/{id}/contacts` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/campaigns/{id}/contacts` | `campaigns/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `DELETE`| `/campaigns/{id}/contacts/{contactId}` | — | ⚪ |
| `POST` | `/campaigns/{id}/duplicate` | `campaigns/page.tsx`, `api-hooks.ts` | ✅ |

### Calls (`/calls`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/calls` | `calls/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/calls/{id}` | `calls/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/calls/{id}/transcript` | `calls/[id]/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/calls/{id}/events` | — | ⚪ |
| `GET`  | `/calls/{id}/legs` | — | ⚪ |

### Contacts (`/contacts`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/contacts/campaigns/{id}/upload` | `contacts/page.tsx`, `api-hooks.ts` | ✅ |
| `POST` | `/contacts/bulk` | — | ⚪ |

### Recordings (`/recordings`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/recordings` | `recordings/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/recordings/{id}/stream` | `recordings/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/recordings/{id}/url` | — | ⚪ |
| `DELETE`| `/recordings/{id}` | — | ⚪ |

### Dashboard (`/dashboard`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/dashboard/summary` | `dashboard/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/dashboard/live-buckets` | `dashboard/page.tsx`, `api-hooks.ts` | ✅ |
| `WS`   | `/dashboard/ws` | `dashboard/page.tsx` | 🔄 |

### Analytics (`/analytics`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/analytics/calls` | `analytics/page.tsx`, `api-hooks.ts` | ✅ |
| `GET`  | `/analytics/partners/{id}` | `partner-analytics-client.tsx`, `api-hooks.ts` | ✅ |

### Billing (`/billing`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/billing/plan` | `billing/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/plans` | `billing/plans/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/billing/plan/change` | `billing-api.ts` | ✅ |
| `GET`  | `/billing/usage/summary` | `billing/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/usage/daily` | `billing/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/usage` | — | ⚪ |
| `GET`  | `/billing/invoices` | `billing/invoices/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/invoices/{id}` | `billing/invoices/[id]/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/adjustments` | `billing/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/billing/adjustment` | `billing-api.ts` | ✅ |
| `GET`  | `/billing/overage-alerts` | `billing/page.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/billing/partners` | `billing-api.ts` | ✅ |
| `GET`  | `/billing/tenants` | `billing-api.ts` | ✅ |
| `GET`  | `/billing/config` | — | ⚪ |
| `POST` | `/billing/create-checkout-session` | — | ⚪ |
| `POST` | `/billing/webhooks` | `route.ts` (local handler) | 📍 |
| `POST` | `/billing/portal` | — | ⚪ |
| `POST` | `/billing/cancel` | — | ⚪ |

### Plans (`/plans`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/plans` | — | ⚪ |

### Clients (`/clients`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/clients` | — | ⚪ |
| `POST` | `/clients` | — | ⚪ |
| `GET`  | `/clients/{id}` | — | ⚪ |
| `DELETE`| `/clients/{id}` | — | ⚪ |

### Connectors (`/connectors`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/connectors` | `settings/connectors/page.tsx`, `api-hooks.ts`, `backend-api.ts` | 🔌 |
| `GET`  | `/connectors/{id}` | — | ⚪ |
| `POST` | `/connectors/authorize` | — | ⚪ |
| `GET`  | `/connectors/providers` | `ai-options/page.tsx` | 🔌 |
| `DELETE`| `/connectors/{id}` | — | ⚪ |
| `POST` | `/connectors/{id}/refresh` | — | ⚪ |

### Meetings (`/meetings`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/meetings` | `meetings/page.tsx`, `backend-api.ts`, `api-hooks.ts` | 🔌 |
| `POST` | `/meetings` | `meetings/page.tsx`, `backend-api.ts` | 🔌 |
| `GET`  | `/meetings/{id}` | — | ⚪ |
| `PUT`  | `/meetings/{id}` | — | ⚪ |
| `DELETE`| `/meetings/{id}` | — | ⚪ |
| `GET`  | `/meetings/availability` | — | ⚪ |

### Assistant (`/assistant`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/assistant/actions` | `assistant/actions/page.tsx`, `backend-api.ts`, `api-hooks.ts` | 🔌 |
| `GET`  | `/assistant/runs` | `assistant/actions/page.tsx`, `backend-api.ts`, `api-hooks.ts` | 🔌 |
| `POST` | `/assistant/execute` | `assistant/actions/page.tsx`, `backend-api.ts`, `api-hooks.ts` | 🔌 |
| `POST` | `/assistant/plan` | `api-hooks.ts` | 🔌 |
| `POST` | `/assistant/runs/{id}/retry` | `api-hooks.ts` | 🔌 |
| `WS`   | `/assistant/chat` | — | 🔄 |
| `GET`  | `/assistant/conversations` | — | ⚪ |
| `GET`  | `/assistant/conversations/{id}` | — | ⚪ |
| `DELETE`| `/assistant/conversations/{id}` | — | ⚪ |

### Ask AI (`/ask-ai`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/ask-ai/greeting` | — | ⚪ |
| `WS`   | `/ws/ask-ai/{session_id}` | — | 🔄 |

### AI Options (`/ai-options`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/ai-options/providers` | `ai-options/page.tsx`, `backend-api.ts` | 🔌 |
| `GET`  | `/ai-options/voices` | `ai-options/page.tsx`, `backend-api.ts` | 🔌 |
| `GET`  | `/ai-options/voices/{id}/sample` | — | ⚪ |
| `POST` | `/ai-options/voices/preview` | `voice-agent-popup.tsx` | ✅ |
| `GET`  | `/ai-options/voices/prefetch-status` | — | ⚪ |
| `POST` | `/ai-options/voices/prefetch` | — | ⚪ |
| `POST` | `/ai-options/test/llm` | — | ⚪ |
| `POST` | `/ai-options/test/tts` | — | ⚪ |
| `GET`  | `/ai-options/config` | — | ⚪ |
| `POST` | `/ai-options/config` | — | ⚪ |
| `POST` | `/ai-options/benchmark` | — | ⚪ |

### Webhooks (`/webhooks`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/webhooks/call/goal-achieved` | — | ⚪ |
| `POST` | `/webhooks/call/mark-spam` | — | ⚪ |

### Webhooks Secure (`/webhooks/secure`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/webhooks/secure/*` | — | ⚪ |
| `GET`  | `/webhooks/secure/*` | — | ⚪ |

### Telephony Bridge (`/sip/telephony`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/sip/telephony/start` | — | ⚪ |
| `POST` | `/sip/telephony/stop` | — | ⚪ |
| `GET`  | `/sip/telephony/status` | — | ⚪ |
| `POST` | `/sip/telephony/call` | — | ⚪ |
| `POST` | `/sip/telephony/hangup/{id}` | — | ⚪ |
| `POST` | `/sip/telephony/transfer/blind` | — | ⚪ |
| `POST` | `/sip/telephony/transfer/attended` | — | ⚪ |
| `POST` | `/sip/telephony/transfer/deflect` | — | ⚪ |
| `POST` | `/sip/telephony/audio/{session_id}` | — | ⚪ |
| `WS`   | `/sip/telephony/ws-audio/{call_uuid}` | — | 🔄 |

### Vonage Bridge (`/vonage`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/vonage/answer` | — | ⚪ |
| `POST` | `/vonage/event` | — | ⚪ |
| `WS`   | `/vonage/ws-audio/{call_uuid}` | — | 🔄 |

### Telephony SIP (`/telephony/sip`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/telephony/sip/trunks` | — | ⚪ |
| `POST` | `/telephony/sip/trunks` | — | ⚪ |
| `PATCH`| `/telephony/sip/trunks/{id}` | — | ⚪ |
| `POST` | `/telephony/sip/trunks/{id}/activate` | — | ⚪ |
| `POST` | `/telephony/sip/trunks/{id}/deactivate` | — | ⚪ |
| `GET`  | `/telephony/sip/codec-policies` | — | ⚪ |
| `POST` | `/telephony/sip/codec-policies` | — | ⚪ |
| `PATCH`| `/telephony/sip/codec-policies/{id}` | — | ⚪ |
| `POST` | `/telephony/sip/codec-policies/{id}/activate` | — | ⚪ |
| `POST` | `/telephony/sip/codec-policies/{id}/deactivate` | — | ⚪ |
| `GET`  | `/telephony/sip/route-policies` | — | ⚪ |
| `POST` | `/telephony/sip/route-policies` | — | ⚪ |
| `PATCH`| `/telephony/sip/route-policies/{id}` | — | ⚪ |
| `POST` | `/telephony/sip/route-policies/{id}/activate` | — | ⚪ |
| `POST` | `/telephony/sip/route-policies/{id}/deactivate` | — | ⚪ |
| `GET`  | `/telephony/sip/quotas/status` | — | ⚪ |

### Telephony Runtime (`/telephony/sip/runtime`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/telephony/sip/runtime/compile/preview` | — | ⚪ |
| `POST` | `/telephony/sip/runtime/activate` | — | ⚪ |
| `POST` | `/telephony/sip/runtime/rollback` | — | ⚪ |
| `GET`  | `/telephony/sip/runtime/versions` | — | ⚪ |
| `GET`  | `/telephony/sip/runtime/metrics/activation` | — | ⚪ |

### Telephony Concurrency (`/telephony/sip/runtime/concurrency`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `POST` | `/telephony/sip/runtime/concurrency/leases/acquire` | — | ⚪ |
| `POST` | `/telephony/sip/runtime/concurrency/leases/{id}/release` | — | ⚪ |
| `POST` | `/telephony/sip/runtime/concurrency/leases/{id}/heartbeat` | — | ⚪ |
| `POST` | `/telephony/sip/runtime/concurrency/leases/expire` | — | ⚪ |
| `GET`  | `/telephony/sip/runtime/concurrency/status` | — | ⚪ |

### Tenant Phone Numbers (`/tenant-phone-numbers`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/tenant-phone-numbers` | — | ⚪ |
| `POST` | `/tenant-phone-numbers` | — | ⚪ |
| `POST` | `/tenant-phone-numbers/{id}/verify` | — | ⚪ |
| `DELETE`| `/tenant-phone-numbers/{id}` | — | ⚪ |

### Tenant AI Credentials (`/tenant-ai-credentials`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/tenant-ai-credentials` | — | ⚪ |
| `POST` | `/tenant-ai-credentials` | — | ⚪ |
| `DELETE`| `/tenant-ai-credentials/{id}` | — | ⚪ |

### DNC (`/dnc`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/dnc` | — | ⚪ |
| `POST` | `/dnc` | — | ⚪ |
| `POST` | `/dnc/bulk-import` | — | ⚪ |
| `POST` | `/dnc/caller-opt-out` | — | ⚪ |
| `GET`  | `/dnc/check` | — | ⚪ |
| `DELETE`| `/dnc/{id}` | — | ⚪ |

### Sessions (`/sessions`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/sessions/active` | `api.ts` | ✅ |
| `DELETE`| `/sessions/{id}` | `api.ts` | ✅ |
| `POST` | `/sessions/verify` | — | ⚪ |
| `GET`  | `/sessions/security-status` | `api.ts` | ✅ |

### Health (`/health`)

| Method | Endpoint | Frontend Consumer | Status |
|--------|----------|-------------------|--------|
| `GET`  | `/health` | `api-hooks.ts`, `backend-api.ts` | ✅ |
| `GET`  | `/health/detailed` | — | ⚪ |

---

## Admin Endpoints (`/admin/*`)

### Base (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/dashboard/stats` | `StatsGrid.tsx` | ✅ |
| `GET`  | `/admin/system-health` | `SystemHealth.tsx` | ✅ |
| `POST` | `/admin/calls/pause` | `LiveCalls.tsx` | ✅ |
| `GET`  | `/admin/calls/pause-status` | `LiveCalls.tsx` | ✅ |

### Tenants (`/admin/tenants`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/tenants` | `TopTenantsList.tsx`, `TenantsPage.tsx`, `QuotaUsage.tsx` | ✅ |
| `GET`  | `/admin/users` | `UsersPage.tsx` | ✅ |
| `GET`  | `/admin/tenants/{id}` | — (defined in api.ts) | ⚪ |
| `PATCH`| `/admin/tenants/{id}/quota` | `TenantsTable.tsx` | ✅ |
| `POST` | `/admin/tenants/{id}/suspend` | `TenantsTable.tsx` | ✅ |
| `POST` | `/admin/tenants/{id}/resume` | `TenantsTable.tsx` | ✅ |

### Calls (`/admin/calls`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/calls/live` | `LiveCalls.tsx`, `LiveCallsTable.tsx` | ✅ |
| `GET`  | `/admin/calls/history` | `CallsPage.tsx`, `CallHistoryTable.tsx` | ✅ |
| `GET`  | `/admin/calls/{id}` | `CallDetailDrawer.tsx` | ✅ |
| `POST` | `/admin/calls/{id}/terminate` | `LiveCallsTable.tsx` | ✅ |

### Actions (`/admin/actions`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/actions` | `ActionsPage.tsx`, `ActionsTable.tsx` | ✅ |
| `GET`  | `/admin/actions/{id}` | `ActionDetailDrawer.tsx` | ✅ |
| `POST` | `/admin/actions/{id}/retry` | `ActionDetailDrawer.tsx` | ✅ |
| `POST` | `/admin/actions/{id}/cancel` | `ActionDetailDrawer.tsx` | ✅ |

### Connectors (`/admin/connectors`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/connectors` | `TopTenantsPanel.tsx`, `ConnectorsPage.tsx` | ✅ |
| `GET`  | `/admin/connectors/{id}` | `ConnectorDetailDrawer.tsx` | ✅ |
| `POST` | `/admin/connectors/{id}/reconnect` | `ConnectorsTable.tsx`, `ConnectorDetailDrawer.tsx` | ✅ |
| `POST` | `/admin/connectors/{id}/revoke` | `ConnectorsTable.tsx`, `ConnectorDetailDrawer.tsx` | ✅ |

### Usage (`/admin/usage`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/usage/summary` | `UsageBreakdownCard.tsx` | ✅ |
| `GET`  | `/admin/usage/breakdown` | `UsageCostPage.tsx` | ✅ |

### Health (`/admin/health`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/health/detailed` | `HealthOverviewCards.tsx` | ✅ |
| `GET`  | `/admin/health/workers` | `WorkerStatusTable.tsx` | ✅ |
| `GET`  | `/admin/health/queues` | `QueueDepthChart.tsx` | ✅ |
| `GET`  | `/admin/health/database` | `DatabaseHealthCard.tsx` | ✅ |

### Incidents (`/admin/incidents`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/incidents` | `Incidents.tsx`, `IncidentsPage.tsx` | ✅ |
| `GET`  | `/admin/incidents/{id}` | — | ⚪ |
| `POST` | `/admin/incidents/{id}/acknowledge` | `IncidentsPage.tsx` | ✅ |
| `POST` | `/admin/incidents/{id}/resolve` | `IncidentsPage.tsx` | ✅ |

### Audit Logs (`/admin/audit`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/audit/logs` | `ActionsLogPage.tsx`, `billing-api.ts` | ✅ |
| `GET`  | `/admin/audit/logs/{id}` | — | ⚪ |
| `POST` | `/admin/audit/logs/export` | — | ⚪ |
| `GET`  | `/admin/audit/stats/events-by-type` | — | ⚪ |
| `GET`  | `/admin/audit/stats/failed-logins` | — | ⚪ |
| `GET`  | `/admin/audit/verify-integrity` | — | ⚪ |

### Security Events (`/admin/security-events`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/security-events/events` | — (defined in api.ts) | ⚪ |
| `GET`  | `/admin/security-events/events/{id}` | — | ⚪ |
| `POST` | `/admin/security-events/events` | — | ⚪ |
| `PATCH`| `/admin/security-events/events/{id}` | — | ⚪ |
| `POST` | `/admin/security-events/events/{id}/resolve` | — | ⚪ |
| `GET`  | `/admin/security-events/alerts/open` | — | ⚪ |
| `GET`  | `/admin/security-events/alerts/overdue` | — | ⚪ |
| `POST` | `/admin/security-events/events/{id}/escalate` | — | ⚪ |

### Suspensions (`/admin/suspensions`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `POST` | `/admin/suspensions/users/{id}/suspend` | — | ⚪ |
| `POST` | `/admin/suspensions/users/{id}/restore` | — | ⚪ |
| `GET`  | `/admin/suspensions/users/{id}/status` | — | ⚪ |
| `GET`  | `/admin/suspensions/users/{id}/history` | — | ⚪ |
| `POST` | `/admin/suspensions/tenants/{id}/suspend` | — | ⚪ |
| `POST` | `/admin/suspensions/tenants/{id}/restore` | — | ⚪ |
| `GET`  | `/admin/suspensions/tenants/{id}/status` | — | ⚪ |
| `POST` | `/admin/suspensions/partners/{id}/suspend` | — | ⚪ |
| `POST` | `/admin/suspensions/partners/{id}/restore` | — | ⚪ |
| `POST` | `/admin/suspensions/{id}/appeal` | — | ⚪ |
| `POST` | `/admin/suspensions/{id}/appeal/review` | — | ⚪ |
| `POST` | `/admin/suspensions/bulk-suspend` | — | ⚪ |
| `GET`  | `/admin/suspensions/propagation-status/{id}` | — | ⚪ |

### Secrets (`/admin/secrets`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/secrets` | `billing-api.ts` | ✅ |
| `POST` | `/admin/secrets/tenants/{id}/secrets` | — | ⚪ |
| `GET`  | `/admin/secrets/tenants/{id}/secrets` | — | ⚪ |
| `GET`  | `/admin/secrets/tenants/{id}/secrets/{sid}` | — | ⚪ |
| `POST` | `/admin/secrets/tenants/{id}/secrets/{sid}/rotate` | — | ⚪ |
| `DELETE`| `/admin/secrets/tenants/{id}/secrets/{sid}` | — | ⚪ |
| `POST` | `/admin/secrets/tenants/{id}/secrets/{sid}/compromise` | — | ⚪ |
| `POST` | `/admin/secrets/validate-api-key` | — | ⚪ |
| `GET`  | `/admin/secrets/platform/secrets` | — | ⚪ |
| `POST` | `/admin/secrets/platform/secrets` | — | ⚪ |
| `POST` | `/admin/secrets/platform/secrets/{id}/rotate` | — | ⚪ |
| `GET`  | `/admin/secrets/expiring` | — | ⚪ |
| `POST` | `/admin/secrets/{id}/rotate` | `billing-api.ts` | ✅ |

### Emergency Access (`/admin/emergency`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `POST` | `/admin/emergency/request` | — | ⚪ |
| `POST` | `/admin/emergency/{id}/approve` | — | ⚪ |
| `POST` | `/admin/emergency/{id}/deny` | — | ⚪ |
| `POST` | `/admin/emergency/{id}/session` | — | ⚪ |
| `DELETE`| `/admin/emergency/{id}/session` | — | ⚪ |
| `GET`  | `/admin/emergency/requests` | — | ⚪ |
| `GET`  | `/admin/emergency/{id}` | — | ⚪ |
| `POST` | `/admin/emergency/{id}/review` | — | ⚪ |

### Call Limits (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/tenant-limits` | `billing-api.ts` | ✅ |
| `PUT`  | `/admin/tenant-limits/{id}` | `billing-api.ts` | ✅ |
| `GET`  | `/admin/partner-limits` | `billing-api.ts` | ✅ |
| `PUT`  | `/admin/partner-limits/{id}` | `billing-api.ts` | ✅ |

### Abuse Monitoring (`/admin/abuse`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/abuse-events` | `billing-api.ts` | ✅ |
| Various | `/admin/abuse/*` | — | ⚪ |

### API Keys (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/api-keys` | `admin/api-keys/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/admin/api-keys` | `billing-api.ts` | ✅ |
| `POST` | `/admin/api-keys/{id}/revoke` | `billing-api.ts` | ✅ |

### Webhooks (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/webhooks` | `admin/webhooks/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/admin/webhooks` | `billing-api.ts` | ✅ |
| `DELETE`| `/admin/webhooks/{id}` | `billing-api.ts` | ✅ |
| `POST` | `/admin/webhooks/{id}/test` | `billing-api.ts` | ✅ |
| `GET`  | `/admin/webhooks/deliveries` | `billing-api.ts` | ✅ |

### Rate Limits (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/rate-limits` | `admin/rate-limiting/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/admin/rate-limits` | `billing-api.ts` | ✅ |
| `PATCH`| `/admin/rate-limits/{id}` | `billing-api.ts` | ✅ |

### Call Guards (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/call-guards` | `admin/voice-security/page.tsx`, `billing-api.ts` | ✅ |
| `PATCH`| `/admin/call-guards/{id}` | `billing-api.ts` | ✅ |

### Blocked Entities (`/admin`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/blocked-entities` | `admin/abuse-detection/page.tsx`, `billing-api.ts` | ✅ |
| `POST` | `/admin/blocked-entities` | `billing-api.ts` | ✅ |
| `DELETE`| `/admin/blocked-entities/{id}` | `billing-api.ts` | ✅ |

### Alerts (`/admin/alerts`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/alerts/settings` | — (defined in api.ts) | ⚪ |
| `PUT`  | `/admin/alerts/settings` | — (defined in api.ts) | ⚪ |

### Configuration (`/admin/configuration`)

| Method | Endpoint | Admin Consumer | Status |
|--------|----------|----------------|--------|
| `GET`  | `/admin/configuration` | `SystemConfigPage.tsx` | ✅ |
| `PATCH`| `/admin/configuration/providers/{type}` | `SystemConfigPage.tsx` | ✅ |

---

## Next.js Local Handlers (Talk-Leee Proxy)

These paths are intercepted by `app/api/v1/[...path]/route.ts` and handled locally in Node.js, **not** forwarded to the FastAPI backend.

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| `GET`  | `/api/v1/health` | Returns `{ status: "ok" }` | Local health stub |
| `POST` | `/api/v1/auth/register` | `registerUser()` | Local auth |
| `POST` | `/api/v1/auth/login` | `verifyPasswordLoginAttempt()` | Local auth |
| `GET`  | `/api/v1/auth/me` | `authMeFromRequest()` | Local auth |
| `POST` | `/api/v1/auth/logout` | `logoutSession()` | Local auth |
| `POST` | `/api/v1/auth/logout_all` | `logoutAllSessionsForUser()` | Local auth |
| `GET`  | `/api/v1/auth/sessions` | `listUserSessions()` | Local auth |
| `POST` | `/api/v1/auth/sessions/revoke` | `revokeUserSessionByHandle()` | Local auth |
| `POST` | `/api/v1/auth/mfa/enroll/start` | `startTotpEnrollment()` | Local MFA |
| `POST` | `/api/v1/auth/mfa/enroll/verify` | `verifyTotpEnrollment()` | Local MFA |
| `POST` | `/api/v1/auth/mfa/disable` | `disableTotpMfa()` | Local MFA |
| `POST` | `/api/v1/auth/passkeys/registration/options` | `getPasskeyRegistrationOptions()` | Local passkeys |
| `POST` | `/api/v1/auth/passkeys/registration/verify` | `verifyPasskeyRegistration()` | Local passkeys |
| `POST` | `/api/v1/auth/passkeys/login/options` | `getPasskeyAuthenticationOptions()` | Local passkeys |
| `POST` | `/api/v1/auth/passkeys/login/verify` | `verifyPasskeyAuthentication()` | Local passkeys |
| `POST` | `/api/v1/voice/calls/guard` | `call_guard()` | Local voice guard |
| `POST` | `/api/v1/voice/calls/start` | `startGuardedVoiceCallSession()` | Local voice |
| `GET`  | `/api/v1/white-label/partners` | Returns seed/DB data | Local white-label |
| `POST` | `/api/v1/white-label/partners` | `upsertPartner()` | Local white-label |
| `GET`  | `/api/v1/white-label/partners/*/tenants/*/agent-settings` | Local handler | Local white-label |
| `PATCH`| `/api/v1/white-label/partners/*/tenants/*/agent-settings` | Local handler | Local white-label |
| `GET`  | `/api/v1/platform/partners` | `listPartners()` | Local platform |
| `POST` | `/api/v1/platform/partners` | `upsertPartner()` | Local platform |
| `GET`  | `/api/v1/partners/*/tenants` | `listPartnerTenants()` | Local platform |
| `POST` | `/api/v1/partners/*/tenants` | `upsertTenant()` | Local platform |
| `POST` | `/api/v1/tenants/*/users` | `assignTenantUserRole()` | Local platform |
| `GET`  | `/api/v1/email/templates` | Returns static templates | Local email |
| `POST` | `/api/v1/email/send` | `sendEmail()` via nodemailer | Local email |
| `POST` | `/api/v1/billing/webhooks/stripe` | Stripe signature verification | Local billing |
| `GET`  | `/api/white-label/branding/*` | `app/api/white-label/branding/*` | Local branding |
| `GET`  | `/api/voices` | Proxies to backend `/voices` or `/ai/voices` | Proxy + map |

---

## WebSocket Endpoints

| Endpoint | Backend File | Frontend Consumer | Status |
|----------|-------------|-------------------|--------|
| `WS /api/v1/dashboard/ws` | `dashboard.py` | `dashboard/page.tsx` | 🔄 |
| `WS /api/v1/assistant/chat` | `assistant_ws.py` | — | 🔄 |
| `WS /api/v1/ws/ask-ai/{session_id}` | `ask_ai_ws.py` | — | 🔄 |
| `WS /api/v1/sip/telephony/ws-audio/{call_uuid}` | `telephony_bridge.py` | — | 🔄 |
| `WS /api/v1/vonage/ws-audio/{call_uuid}` | `vonage_bridge.py` | — | 🔄 |

---

## App-Level Endpoints (outside `/api/v1`)

| Method | Path | Consumer | Status |
|--------|------|----------|--------|
| `GET`  | `/` | — | ⚪ |
| `GET`  | `/health` | — | ⚪ |
| `GET`  | `/metrics` | — | ⚪ |

---

## Talk-Leee Frontend → Backend Call Paths

### Direct API Clients

| Client | Base URL | Used By |
|--------|----------|---------|
| `api` (`lib/api.ts`) | `apiBaseUrl()` | Auth, MFA, sessions, passkeys, health |
| `backendApi` (`lib/backend-api.ts`) | `apiBaseUrl()` | Connectors, meetings, calendar, reminders, email, assistant |
| `extendedApi` (`lib/extended-api.ts`) | `apiBaseUrl()` | Contacts CSV, analytics, recordings, campaigns/calls |
| `dashboardApi` (`lib/dashboard-api.ts`) | `apiBaseUrl()` | Dashboard, campaigns, calls |
| `billingFetch` (`lib/billing-api.ts`) | `NEXT_PUBLIC_API_URL` | Billing, admin (bypasses Next.js proxy) |

### URL Resolution

```
apiBaseUrl():
  → NEXT_PUBLIC_API_BASE_URL (if set)
  → otherwise "http://localhost:8000/api/v1" (or /api/v1 for proxy)

NEXT_PUBLIC_API_URL (billing):
  → direct backend URL (e.g., http://localhost:8000/api/v1)
```

---

## Admin Panel → Backend Call Paths

| Client | Base URL | Auth |
|--------|----------|------|
| `ApiClient` (`lib/api.ts`) | `VITE_API_BASE_URL` or `http://localhost:8000/api/v1` | Bearer token from localStorage (`admin_token`) |
| `apiRequest` (`lib/passkeys.ts`) | Same as above | `credentials: 'include'` |

---

## Backend Endpoints with No Frontend Consumer

| Category | Endpoints | Count |
|----------|-----------|-------|
| Telephony SIP | trunks, codec-policies, route-policies, quotas | 15+ |
| Telephony Runtime | compile, activate, rollback, versions, metrics | 5 |
| Telephony Concurrency | leases acquire/release/heartbeat/expire, status | 5 |
| Telephony Bridge | start, stop, status, call, hangup, transfer, audio | 10 |
| Vonage | answer, event | 3 |
| Tenant Phone Numbers | CRUD + verify | 4 |
| Tenant AI Credentials | CRUD | 3 |
| DNC | list, add, bulk-import, opt-out, check, delete | 6 |
| Clients | CRUD | 4 |
| Plans | list | 1 |
| Webhooks (receivers) | goal-achieved, mark-spam, secure | 6+ |
| Sessions | verify | 1 |
| Health | detailed | 1 |
| Assistant | chat WS, conversations, runs | 4+ |
| Ask AI | greeting, WS | 2 |
| AI Options | sample, prefetch, test, benchmark, config | 7+ |
| Admin — Security Events | full CRUD + alerts + escalate | 8 |
| Admin — Suspensions | users, tenants, partners, appeals, bulk | 13 |
| Admin — Secrets | tenant secrets, platform secrets, expiring, validate | 10+ |
| Admin — Emergency Access | request, approve, deny, session, review | 8 |
| Admin — Audit Logs | export, stats, verify-integrity | 4 |
| Admin — Alerts | settings | 2 |
| Admin — Call Limits | (partially used via billing-api) | ~2 unused |
| Admin — Abuse | (partially used via billing-api) | ~7 unused |

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| Total backend endpoints | ~260+ |
| Connected via Talk-Leee | ~85 |
| Connected via Admin Panel | ~50 |
| WebSocket endpoints | 5 |
| Next.js local handlers | ~25 |
| Unused backend endpoints | ~120+ |
| Direct (✅) | ~135 |
| Proxied (🔌) | ~25 |
| Local only (📍) | ~25 |
| Unused (⚪) | ~120+ |

---

## Architecture Notes

### Next.js Proxy (`app/api/v1/[...path]/route.ts`)
- **Proxied to backend:** All unmatched paths go to `http://localhost:8000/api/v1`
- **Handled locally:** Auth, MFA, passkeys, white-label, platform, voice guard, email templates, Stripe webhooks
- **No local fallbacks remain** for connectors, meetings, calendar, reminders, assistant

### WebSocket Authentication
- Backend `dashboard/ws` accepts token via first text message after connection
- Invalid tokens receive `401 Unauthorized` and immediate close
- Frontend sends: `ws.send(JSON.stringify({ token: localStorage.getItem("access_token") }))`

### Admin Auth
- Dummy auth only activates when `VITE_ADMIN_DEV_MODE === "true"`
- Otherwise uses real `POST /auth/login` and `GET /auth/me`
