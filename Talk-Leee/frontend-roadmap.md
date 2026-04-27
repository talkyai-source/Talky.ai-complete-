## System Architecture
- The Talk-lee platform is composed of four primary layers that work together to deliver real-time voice sessions while exposing configuration, operations, and observability through the dashboard:
  - Frontend (Next.js Dashboard): the web UI that users and operators interact with to configure AI options, manage connectors, view sessions/records, and trigger workflows
  - Backend API Layer: the authoritative system-of-record for users, tenants, configuration, and operational workflows; exposes REST endpoints consumed by the dashboard
  - Session + Voice Pipeline: real-time session orchestration and audio processing that manages active calls and voice interaction flows
  - Telephony Infrastructure: SIP routing and call handling systems that connect Talk-lee to carriers and BYO SIP providers

### Architecture Flow
```text
Browser Dashboard
        |
        v
Next.js Frontend
        |
        v
Backend API
        |
        v
Session Layer
        |
        v
Call Stability Layer
        |
        v
Voice Pipeline (C++)
        |
        v
Asterisk
        |
        v
OpenSIPS
        |
        v
SIP Provider / BYO SIP
```

### Layer Responsibilities (High-Level)
- Browser Dashboard: renders the UI, handles authenticated navigation, and triggers API calls (React Query + typed API modules)
- Next.js Frontend: application shell, feature pages, UI components, and a small set of local Next route handlers used for proxying/prototyping in this repo
- Backend API: validates auth/tenant context, persists configuration and workflows, and provides endpoints for dashboard features (connectors, meetings, reminders, email, voice configuration)
- Session Layer: manages the lifecycle of live sessions (call initiation, session state, handoffs) and exposes session-related operations to the backend
- Call Stability Layer: focuses on resiliency during live calls (reconnect/retry strategies, health checks, and graceful degradation)
- Voice Pipeline (C++): real-time audio/voice processing and integration glue used during active calls
- Asterisk: core telephony engine handling call setup/media bridging and interaction with SIP systems
- OpenSIPS: SIP proxy/routing and policy layer that routes calls to upstream carriers or BYO SIP endpoints
- SIP Provider / BYO SIP: external carrier/provider interface or partner-managed SIP infrastructure

### How the Frontend Fits In
- The Next.js dashboard does not talk directly to SIP infrastructure. It interacts with the platform via the Backend API.
- Configuration and workflows created in the dashboard (AI provider settings, connector state, meeting/reminder actions, email templates) are persisted and enforced by the Backend API.
- Live session and call operations surface through the Backend API, which coordinates downstream session and telephony layers.

## 1) Project Overview
- Framework: Next.js App Router (Next 15) + React 19 ([package.json](file:///c:/Users/User/Desktop/Talk-Leee/package.json))
- UI approach: Tailwind CSS v4 + a shadcn-style component set in `src/components/ui` (see [globals.css](file:///c:/Users/User/Desktop/Talk-Leee/src/app/globals.css))
- Data layer: TanStack React Query v5 for server state + a few lightweight client stores for UI/local persistence (see [app-providers.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/providers/app-providers.tsx), [notifications.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/notifications.ts), [email-audit.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/email-audit.ts))
- API strategy: a custom HTTP client + typed “API modules” and React Query hooks; plus a local `/api/v1/*` Next route acting as a dev/prototype backend for many features (see [http-client.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/http-client.ts), [api-hooks.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/api-hooks.ts), [route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/v1/%5B...path%5D/route.ts))

## Environment Configuration
- The frontend uses environment variables (typically via `.env.local`) to select backend routing, control observability integration, and set runtime environment behavior.

### Frontend Environment Variables
| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | Base URL used by the frontend to call backend APIs. If unset, the app may fallback to local `/api/v1` behavior depending on runtime and environment defaults. |
| `NEXT_PUBLIC_SENTRY_DSN` | Enables Sentry monitoring for client-side error reporting when configured. |
| `NEXT_PUBLIC_APP_ENV` | Defines the application environment: `development`, `staging`, or `production`. |

### `NEXT_PUBLIC_APP_ENV` Values
- `development`
- `staging`
- `production`

### Example: `.env.local`
```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:3100/api/v1
NEXT_PUBLIC_APP_ENV=development
```

### Environment Rules
- Production deployments must define `NEXT_PUBLIC_API_BASE_URL`.
- Local development may fallback to `/api/v1` when `NEXT_PUBLIC_API_BASE_URL` is not set (see [env.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/env.ts#L55-L66)).

## Development Workflow
### Start the Project
```bash
npm install
npm run dev
```

- The application runs at: `http://localhost:3000`

### Connecting to Backend Environments
- Local backend:
  - `NEXT_PUBLIC_API_BASE_URL=http://localhost:3100/api/v1`
- Remote development backend:
  - `NEXT_PUBLIC_API_BASE_URL=https://dev-api.talklee.ai`

### Testing
```bash
npm run test
npm run test:visual
```

### Linting and Type Checking
```bash
npm run lint
npm run typecheck
```

### Storybook
```bash
npm run storybook
```

- Storybook is used for UI component development and visual testing of shared primitives under `src/components/ui`.

## Local dev commands (canonical):
  - `npm run dev`
  - `npm run lint`
  - `npm run typecheck`
  - `npm run test`
  - `npm run test:visual`
  - `npm run storybook`
  ([package.json](file:///c:/Users/User/Desktop/Talk-Leee/package.json))

## 2) Folder Structure & Key Files
- `src/app/*`: App Router pages + local API routes
  - Prototype backend route: [src/app/api/v1/[...path]/route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/v1/%5B...path%5D/route.ts)
  - AI voices route: [src/app/api/voices/route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/voices/route.ts)
  - High-signal feature pages: [ai-options/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/ai-options/page.tsx), [meetings/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/meetings/page.tsx), [reminders/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/reminders/page.tsx), [settings/connectors/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/settings/connectors/page.tsx), [email/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/email/page.tsx)
- `src/components/*`: reusable UI + domain components
  - Layout + shell: [dashboard-layout.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/layout/dashboard-layout.tsx)
  - Guarding: [route-guard.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/guards/route-guard.tsx)
  - Providers: [app-providers.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/providers/app-providers.tsx)
- `src/lib/*`: API clients, hooks, stores, model types, utilities
  - HTTP + errors: [http-client.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/http-client.ts)
  - Environment: [env.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/env.ts)
  - React Query hooks: [api-hooks.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/api-hooks.ts)
  - Prototype data APIs: [dashboard-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/dashboard-api.ts), [extended-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/extended-api.ts)
  - Client-side stores: [notifications.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/notifications.ts), [email-audit.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/email-audit.ts)
- Root config / tooling
  - Next config (Sentry + caching headers): [next.config.ts](file:///c:/Users/User/Desktop/Talk-Leee/next.config.ts)
  - CI commands baseline: [.github/workflows/ci.yml](file:///c:/Users/User/Desktop/Talk-Leee/.github/workflows/ci.yml)

## 3) Routing, Layouts & Navigation
- Shell layout: most authenticated pages render inside [DashboardLayout](file:///c:/Users/User/Desktop/Talk-Leee/src/components/layout/dashboard-layout.tsx), which:
  - Enforces auth by default (`requireAuth = true`) and redirects to `/auth/login?next=...` if needed
  - Hosts global UI: sidebar, breadcrumbs, health indicator, notifications
- Feature gating by connectors:
  - Pages wrap content in [RouteGuard](file:///c:/Users/User/Desktop/Talk-Leee/src/components/guards/route-guard.tsx) and declare `requiredConnectors={["calendar","email",...]}` (example: [email/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/email/page.tsx#L79-L84))
  - Connectors setup UX supports `?required=calendar,email&next=/some/page` on [settings/connectors](file:///c:/Users/User/Desktop/Talk-Leee/src/app/settings/connectors/page.tsx)
- Navigation structure is driven by sidebar config + route matching (see [sidebar.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/sidebar.ts) and [sidebar.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/layout/sidebar.tsx))

## 4) State Management
- Server state: React Query is the standard for remote data + mutations (provider wiring in [app-providers.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/providers/app-providers.tsx); hooks in [api-hooks.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/api-hooks.ts))
- UI/local state patterns:
  - Sidebar state persisted in localStorage (client helpers in [sidebar-client.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/sidebar-client.ts))
  - Notifications store:
    - Persisted settings/history in localStorage
    - Also supports optional webhook dispatch if privacy + config allow it ([notifications.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/notifications.ts))
  - Email send audit store:
    - Local-only audit history for send attempts/status in localStorage ([email-audit.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/email-audit.ts))
- Cross-tab / cross-window sync patterns:
  - Connectors page listens to `window.postMessage`, `BroadcastChannel("connectors")`, and `localStorage` keys to refresh state ([connectors page](file:///c:/Users/User/Desktop/Talk-Leee/src/app/settings/connectors/page.tsx#L71-L142))
  - Reminders page listens on `BroadcastChannel("reminders")` and `window.message` to invalidate queries ([reminders page](file:///c:/Users/User/Desktop/Talk-Leee/src/app/reminders/page.tsx#L93-L109))

## 5) API Integration
- Base URL selection:
  - `apiBaseUrl()` prefers `NEXT_PUBLIC_API_BASE_URL`; otherwise defaults to `/api/v1` on the current origin (client-side) and `http://127.0.0.1:3100/api/v1` server-side in non-prod ([env.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/env.ts#L55-L66))
- Transport + error normalization:
  - Centralized in [http-client.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/http-client.ts) with a typed error (`ApiClientError`) and helper `isApiClientError`
- API surface organization:
  - “Backend endpoints” are centralized in [backend-endpoints.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/backend-endpoints.ts)
  - React Query hooks and query keys live in [api-hooks.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/api-hooks.ts)
  - Some pages still call “prototype API modules” directly (not React Query), notably:
    - `dashboardApi` ([dashboard-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/dashboard-api.ts))
    - `extendedApi` ([extended-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/extended-api.ts))
- Local prototype backend:
  - Implemented as a Next Route Handler at [src/app/api/v1/[...path]/route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/v1/%5B...path%5D/route.ts)
  - Important behavior: many endpoints are dev-only; in production it returns 404 for most non-email endpoints (see “Implementation Status” below)

## 6) Auth & Access Control
- Client auth state:
  - Auth context in [auth-context.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/auth-context.tsx) powers `useAuth()`
  - [DashboardLayout](file:///c:/Users/User/Desktop/Talk-Leee/src/components/layout/dashboard-layout.tsx) calls `refreshUser()` once before redirecting if `user` is missing
- Middleware:
  - Security headers + auth routing are enforced in [middleware.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/middleware.ts)
- Feature access via connectors:
  - `RouteGuard` blocks pages until required connectors are “connected” and guides user to connectors setup (see [route-guard.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/components/guards/route-guard.tsx))

## 7) Feature Modules (What Exists + What It Depends On)
- AI Options / Provider config
  - Page: [ai-options/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/ai-options/page.tsx)
  - Supports provider selection, saving config, LLM “test” flows, and voice preview (base64 audio decode)
- AI Voices
  - Page: [ai-voices/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/ai-voices/page.tsx)
  - Route: [api/voices/route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/voices/route.ts) (server-side aggregation/proxy)
Screenshot:


- Connectors
  - Page: [settings/connectors/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/settings/connectors/page.tsx)
  - UX includes postMessage / BroadcastChannel refresh after OAuth callbacks
- Email
  - Page: [email/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/email/page.tsx)
  - UI includes templates panel, send modal, and local audit history (see [email-audit.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/email-audit.ts))
- Meetings
  - Page: [meetings/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/meetings/page.tsx)
  - Uses React Query hooks for calendar events + create/cancel; gated by connectors (calendar + email) via `RouteGuard`
- Reminders
  - Page: [reminders/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/reminders/page.tsx)
  - Uses React Query hooks, filtering/grouping utilities, and cross-tab refresh
- Assistant
  - Landing: [assistant/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/assistant/page.tsx)
  - Actions: [assistant/actions/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/assistant/actions/page.tsx) (rich UI: audit log, filtering, downloads, execution)
- Contacts import (CSV)
  - Page: [contacts/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/contacts/page.tsx)
  - Uses `dashboardApi.listCampaigns()` and `extendedApi.uploadCSV()` (both currently prototype-mode modules)
- Recordings
  - Page: [recordings/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/recordings/page.tsx)
  - Uses `extendedApi.listRecordings()` + stream URL helper (prototype-mode module)
Screenshot:


## Feature Ownership
- Feature ownership helps developers and external partners identify the team responsible for maintenance, implementation decisions, and review routing.

| Feature | Owner |
| --- | --- |
| AI Options | AI Team |
| Voice Configuration | Voice Team |
| Connectors | Integration Team |
| Meetings | Scheduling Team |
| Assistant Actions | Agent Team |

## Feature Integration Status
- This table indicates which frontend modules are production-ready end-to-end and which are still operating in prototype mode (local routes/dummy modules) and require a real backend at `NEXT_PUBLIC_API_BASE_URL`.

| Feature | Status |
| --- | --- |
| AI Voices | Production |
| Email | Production |
| Meetings | Prototype |
| Reminders | Prototype |
| Contacts Import | Prototype |
| Recordings | Prototype |

## White-Label Compatibility
- The Talk-lee frontend supports multi-tenant white-label deployments where multiple tenant organizations share the same codebase while keeping data and feature access isolated.

### Key Capabilities
- Tenant-scoped data: all feature data is expected to be tenant-isolated by the backend and surfaced in the dashboard per authenticated tenant context.
- Configurable branding layer: partner-specific branding (logo, colors, naming) can be applied without changing core product flows.
- Feature flagging per tenant: features can be enabled/disabled for a tenant to support phased rollouts and partner contracts.
- Tenant-specific dashboard configuration: navigation, default views, and available modules can be tailored per tenant.

### What White-Label Partners Receive
- Custom branded dashboard aligned to partner identity
- Restricted feature access via tenant-level feature flags and role-based access control
- Tenant-isolated data governed by the Backend API and auth context

## 8) Styling, Theming & UI System
- Tailwind v4 is used for styling (see dependencies in [package.json](file:///c:/Users/User/Desktop/Talk-Leee/package.json))
- App-wide styling lives in [globals.css](file:///c:/Users/User/Desktop/Talk-Leee/src/app/globals.css) (custom background shapes, `content-card` style, theme tokens)
- UI primitives:
  - Located under `src/components/ui/*` (buttons, inputs, modal/drawer, etc.)
  - Conventions: props-forwarding, `cn()` helper ([utils.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/utils.ts)), Tailwind + small variants
- Theming:
  - Theme provider used by some pages (example: [assistant/page.tsx](file:///c:/Users/User/Desktop/Talk-Leee/src/app/assistant/page.tsx))

## 9) Testing, Observability, Storybook & Security
- Tests
  - Unit/integration: Node’s built-in test runner + TSX runtime loader ([package.json](file:///c:/Users/User/Desktop/Talk-Leee/package.json#L5-L18))
  - Global setup includes DOM shims and polyfills ([setup.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/test-utils/setup.ts))
  - Visual regression: Playwright (`npm run test:visual`), with snapshots committed under `tests/*-snapshots/`
- CI expectations (must stay green)
  - Lint, typecheck, unit tests, build, OpenAPI check, Storybook build ([ci.yml](file:///c:/Users/User/Desktop/Talk-Leee/.github/workflows/ci.yml#L18-L24))
- Observability (Sentry)
  - Next config wraps with Sentry and configures sourcemaps/release based on env ([next.config.ts](file:///c:/Users/User/Desktop/Talk-Leee/next.config.ts))
  - Runtime controls are driven by env schema + helpers ([env.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/env.ts#L72-L85))
- Security
  - `poweredByHeader: false` and caching headers for static assets ([next.config.ts](file:///c:/Users/User/Desktop/Talk-Leee/next.config.ts))
  - Middleware applies security headers + routing rules ([middleware.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/middleware.ts))

## 10) Implementation Status, Roadmap & Links
### Implementation Status Summary (based on code)
- “Real” server endpoints in this repo:
  - `/api/voices` is implemented as a Next route ([api/voices/route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/voices/route.ts))
  - `/api/v1/email/*` endpoints exist (templates + send) in the catchall route ([route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/v1/%5B...path%5D/route.ts))
- “Prototype mode” modules (explicitly marked / used as placeholders):
  - `dashboardApi` returns dummy data ([dashboard-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/dashboard-api.ts))
  - `extendedApi` contains prototype endpoints for uploads/recordings/etc. ([extended-api.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/lib/extended-api.ts))
- Production behavior note (important):
  - The local `/api/v1/*` catchall route intentionally returns 404 in production for many non-email endpoints, so features relying on those endpoints require a real backend at `NEXT_PUBLIC_API_BASE_URL` (see [route.ts](file:///c:/Users/User/Desktop/Talk-Leee/src/app/api/v1/%5B...path%5D/route.ts))

### Phase-Based Roadmap (only what the codebase implies)
- Phase 1 (Current): UI-first implementation with local prototype backend + dummy modules for product flows (connectors/assistant/meetings/reminders/contacts/recordings)
- Phase 2 (Next integration step implied by the architecture): Replace prototype `/api/v1/*` and `dashboardApi/extendedApi` usage with real backend endpoints behind `NEXT_PUBLIC_API_BASE_URL`, keeping React Query hooks as the stable integration surface
- Phase 3 (Hardening implied by CI): Ensure all backend-backed flows keep `lint/typecheck/test/build/docs:openapi:check/build-storybook` green ([ci.yml](file:///c:/Users/User/Desktop/Talk-Leee/.github/workflows/ci.yml))

### Screenshots and Links
- Product imagery:
  - [public/images/login-page.jpg](file:///c:/Users/User/Desktop/Talk-Leee/public/images/login-page.jpg)
  - [public/images/ai-voice-section..jpg](file:///c:/Users/User/Desktop/Talk-Leee/public/images/ai-voice-section..jpg)
- Visual regression snapshots (Playwright):
  - [dashboard-kpi-row-*](file:///c:/Users/User/Desktop/Talk-Leee/tests/dashboard-first-row.visual.spec.ts-snapshots/)
  - [home-hero-player-*](file:///c:/Users/User/Desktop/Talk-Leee/tests/home-hero.video.visual.spec.ts-snapshots/)
  - [home-secondary-hero-player-*](file:///c:/Users/User/Desktop/Talk-Leee/tests/home-secondary-hero.video.visual.spec.ts-snapshots/)
