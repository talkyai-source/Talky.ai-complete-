import { test, type Page } from "@playwright/test";

/*
 * Browser-side stand-in for the backend, shared by the optional browser specs
 * (demo.smoke.spec.ts, dashboard-first-row.visual.spec.ts).
 *
 * Why this exists: the CI job ("Browser smoke and visual (optional)",
 * .github/workflows/qa-browser.yml) runs `next dev` with NO backend. Since
 * commit 2d90bf49 the Next API route answers every /api/v1/auth/* request,
 * /api/v1/auth/me included, with 503 auth_backend_unavailable
 * (src/app/api/v1/[...path]/route.ts:746, pinned by src/lib/auth-local-route.test.ts).
 * The specs used to sign in by setting a cookie and relying on that route to
 * accept the dev token, so AuthProvider's api.getMe() now fails, DashboardLayout
 * redirects to /auth/login (src/components/layout/dashboard-layout.tsx:59-76) and
 * neither the sidebar nor the dashboard ever renders. The product code is
 * deliberately not changed; the session is faked in the browser instead.
 *
 * Everything here is answered with page.route, so no request reaches the Next
 * server. Only GET requests are answered; anything else, and any GET that has
 * no entry below, falls through to the network exactly as before. The job sets
 * NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:3100/api/v1, the page's own origin,
 * so the answers need no CORS headers; point it at another origin and they would.
 */

// playwright.config.ts `use.baseURL`; used only when the project does not set one.
const FALLBACK_ORIGIN = "http://127.0.0.1:3100";

export const E2E_AUTH_TOKEN = "e2e-token";

/*
 * Must satisfy MeResponseSchema (src/lib/api.ts:73): id, a valid email and role
 * are required. tenant_id is required in practice: AuthProvider only arms the
 * request identity when the profile has a tenant_id or the role is
 * platform_admin (src/lib/auth-context.tsx:92), and until it is armed every
 * page read waits 15 s and then fails with identity_unverified
 * (src/lib/http-client.ts:239, :487, :584).
 */
export const E2E_USER = {
    id: "9b2f6d1e-3c4a-4f5b-8a7d-1e2f3a4b5c6d",
    email: "e2e@example.com",
    name: "E2E User",
    business_name: "E2E Test Co",
    account_timezone: "UTC",
    role: "user",
    tenant_id: "5d8c1a7e-2b3f-4c6a-9e0d-7a1b2c3d4e5f",
    minutes_state: "known",
    minutes_remaining: 600,
} as const;

// GET /rbac/users/me/permissions (src/lib/inbound-permissions.ts:35). The
// sidebar hides entries once the list is known and lacks the permission, so
// this is a normal tenant member who can reach every page the specs visit.
export const E2E_PERMISSIONS = [
    "campaigns:read", "campaigns:create", "campaigns:update", "campaigns:delete",
    "calls:read", "calls:create", "calls:export",
    "recordings:read", "recordings:download",
    "analytics:read", "analytics:export",
    "connectors:read", "connectors:create", "connectors:update", "connectors:delete",
    "tenants:read",
    "billing:read",
    "inbound:read",
] as const;

// null is a real answer: the call feedback and review reads accept a 200 null as "nothing saved yet".
type MockBody = Record<string, unknown> | unknown[] | null | ((url: URL) => unknown);
export type ApiMock = { path: string | RegExp; body: MockBody };

/** "/api/v1/auth/me/?x=1" -> "/auth/me". Matches whatever host serves /api/v1. */
function apiPath(url: URL): string {
    return url.pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "") || "/";
}

/**
 * Answers `GET /api/v1<path>` with 200 JSON. Handlers registered later run
 * first; a request that matches no entry is passed on to the earlier handlers
 * and finally to the network (route.fallback), so a spec can still add its own
 * `page.route` on top of these.
 */
export async function mockApi(page: Page, mocks: ApiMock[]): Promise<void> {
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.method() !== "GET" || request.resourceType() === "document") return route.fallback();
            const url = new URL(request.url());
            const path = apiPath(url);
            const mock = mocks.find((m) => (typeof m.path === "string" ? m.path === path : m.path.test(path)));
            if (!mock) return route.fallback();
            const body = typeof mock.body === "function" ? mock.body(url) : mock.body;
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
        },
    );
}

/*
 * The shell every signed-in page mounts: the sidebar (permissions), the
 * layout widgets and the root providers. Each body is the smallest one its
 * consumer accepts.
 */
const SHELL_MOCKS: ApiMock[] = [
    { path: "/auth/me", body: E2E_USER },
    // normalizePermissions: src/lib/inbound-permissions.ts:22
    { path: "/rbac/users/me/permissions", body: { permissions: [...E2E_PERMISSIONS] } },
    // HealthIndicator -> useHealth -> backendApi.health (src/lib/backend-api.ts:204)
    { path: "/health", body: { status: "ok" } },
    // PrefetchOnAuth -> prefetchAiOptions (src/lib/queries/ai-options-queries.ts:88). RawProviderListSchema
    // needs llm/stt/tts buckets (src/lib/ai-options-api.ts:227); normalizeConfig needs the fields below (:437).
    {
        path: "/ai-options/providers",
        body: {
            llm: { providers: [], models: [] },
            stt: { providers: [], models: [] },
            tts: { providers: [], models: [] },
        },
    },
    { path: "/ai-options/voices", body: { voices: [] } },
    {
        path: "/ai-options/config",
        body: {
            llm_provider: "groq",
            llm_model: "e2e-llm",
            llm_temperature: 0.5,
            llm_max_tokens: 150,
            stt_provider: "deepgram",
            stt_model: "e2e-stt",
            stt_language: "en",
            tts_provider: "cartesia",
            tts_model: "e2e-tts",
            tts_voice_id: "e2e-voice",
            tts_sample_rate: 24000,
        },
    },
    // SupportLauncher -> loadSupportConfig; supportConfigSchema (src/lib/support-api.ts:33). enabled:false = no support UI.
    {
        path: "/support/config",
        body: {
            enabled: false,
            bot_enabled: false,
            permissions: { report: false, read_team: false },
            categories: [],
            suggestions: [],
            attachments: { max_mb: 0, max_per_message: 0, types: [] },
        },
    },
    // QualifiedLeadAlerts -> useAlerts (src/lib/alerts-api.ts:78). The reader's tenant_id/user_id must equal the
    // signed-in identity or the answer is discarded (alerts-api.ts:109).
    {
        path: "/alerts",
        body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id, user_id: E2E_USER.id },
    },
    // FailedCallAlerts -> dashboardApi.listCalls (src/lib/dashboard-api.ts:760) reads items and total.
    { path: "/calls", body: { items: [], total: 0 } },
];

/**
 * Signs the page in: the legacy cookie the old specs used plus the canonical
 * localStorage token (src/lib/auth-token.ts), and the mocked session and shell
 * above. Call it before the first page.goto.
 */
export async function signInAsE2eUser(page: Page): Promise<void> {
    const origin = test.info().project.use.baseURL ?? FALLBACK_ORIGIN;
    await page.context().addCookies([{ name: "talklee_auth_token", value: E2E_AUTH_TOKEN, url: origin }]);
    await page.addInitScript((token) => {
        try {
            window.localStorage.setItem("talklee.auth.token", token);
        } catch {
            // Storage can be unavailable; the cookie still carries the token.
        }
    }, E2E_AUTH_TOKEN);
    await mockApi(page, SHELL_MOCKS);
}

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/** UTC labels the way the analytics endpoints return them: "YYYY-MM-DD" or "YYYY-MM-DDTHH:00". */
function dayLabel(ms: number): string {
    return new Date(ms).toISOString().slice(0, 10);
}
function hourLabel(ms: number): string {
    return `${new Date(ms).toISOString().slice(0, 13)}:00`;
}

/*
 * GET /analytics/calls (src/lib/extended-api.ts:222): { series, timezone, direction, include_tests },
 * rows { date, total_calls, answered, failed }. `group_by=hour` rows are
 * "YYYY-MM-DDTHH:00", `group_by=day` rows "YYYY-MM-DD", both in the summary's
 * timezone (UTC here). The dashboard keeps only the rows inside its live window
 * (last 4 h from its own clock), so the hours are built backwards from `nowMs`.
 */
function callAnalytics(url: URL, nowMs: number) {
    const hourly = url.searchParams.get("group_by") === "hour";
    const totals = hourly ? [8, 12, 9, 15, 11, 14] : [41, 52, 61];
    const unit = hourly ? HOUR_MS : DAY_MS;
    const series = totals.map((total, i) => {
        const ms = nowMs - (totals.length - 1 - i) * unit;
        const failed = Math.round(total * 0.1);
        return {
            date: hourly ? hourLabel(ms) : dayLabel(ms),
            total_calls: total,
            answered: Math.round(total * 0.65),
            failed,
        };
    });
    return { series, timezone: "UTC", direction: "outbound", include_tests: false };
}

/*
 * What the other audited pages load on open, one entry per request, each the
 * smallest body its consumer accepts (the comment names the consumer). Only
 * requests the page itself makes are listed; the shell above and the dashboard
 * mocks below already answer the shared ones (/calls, /events, /campaigns,
 * /analytics/calls). Keys are paths without the query string.
 */
const PAGE_MOCKS: ApiMock[] = [
    // /calls/call-001 (src/app/calls/[id]/page.tsx): dashboardApi.getCall maps this body (dashboard-api.ts:820-886).
    // A fixed timestamp keeps the page deterministic. Status "completed": active statuses make the list poll.
    {
        path: "/calls/call-001",
        body: { id: "call-001", timestamp: "2026-11-10T10:00:00Z", to_number: "+15551234567", status: "completed", direction: "outbound" },
    },
    { path: "/calls/call-001/transcript", body: { format: "json", turns: [] } }, // getCallTranscript (dashboard-api.ts:889)
    // The real backend answers 404 for "no note / no review yet" and extended-api turns that into null
    // (getCallFeedback :375, getMyReview :477); a 200 null is the same state without a console error.
    { path: "/calls/call-001/feedback", body: null },
    { path: "/calls/call-001/review", body: null },
    { path: "/calls/call-001/reviews", body: [] }, // listCallReviews returns a bare array (extended-api.ts:489)
    {
        path: "/calls/reviews/options", // ReviewOptions (extended-api.ts:28)
        body: { tags: [], rewards_enabled: false, points_per_review: 0, daily_cap: 0, bare_rating_earns_reward: false },
    },
    { path: "/calls/call-001/lead-details", body: { details: [], missing_required: [] } }, // LeadDetailsResponse (lead-details-api.ts:74)
    // /recordings (src/app/recordings/page.tsx:44): RecordingListResponse (extended-api.ts:97).
    { path: "/recordings", body: { items: [], page: 1, page_size: 20, total: 0 } },

    // /campaigns/new (campaign-wizard.tsx:221 -> useCampaignLeadFieldDraft): leadDetailsApi.fieldSpec (lead-details-api.ts:117).
    { path: "/contacts/fields", body: { fields: [], csv_template_headers: [] } },

    // /campaigns/camp-001 (src/app/campaigns/[id]/page.tsx loadData :272). getCampaign returns the {campaign}
    // wrapper (dashboard-api.ts:510); a bare object would throw. Direction must be outbound, otherwise the page
    // looks up inbound campaigns and router.replace()s (campaign-direction.ts:12). Status "completed" keeps the
    // readiness poll (10 s) and the running-campaign refresh (7 s) off.
    {
        path: "/campaigns/camp-001",
        body: {
            campaign: {
                id: "camp-001",
                name: "E2E Campaign",
                description: "E2E smoke campaign",
                status: "completed",
                direction: "outbound",
                system_prompt: "",
                voice_id: "",
                max_concurrent_calls: 1,
                total_leads: 0,
                calls_completed: 0,
                calls_failed: 0,
                created_at: "2026-01-01T00:00:00Z",
                updated_at: "2026-01-01T00:00:00Z",
                script_config: { agent_names: ["Alex"] },
                calling_config: null,
            },
        },
    },
    // fetchAllPages -> listContacts (dashboard-api.ts:678): reads items and total. Empty, so no per-contact requests.
    { path: "/campaigns/camp-001/contacts", body: { items: [], total: 0, page: 1, page_size: 100 } },
    // getCampaignStats (dashboard-api.ts:658) and getMinutesStatus (:630, MinutesStatus :33).
    {
        path: "/campaigns/camp-001/stats",
        body: {
            campaign_id: "camp-001",
            campaign_status: "completed",
            total_leads: 0,
            qualified_leads: 0,
            job_status_counts: {},
            call_outcome_counts: {},
            goals_achieved: 0,
            blocking_reason: null,
            activity: null,
        },
    },
    { path: "/campaigns/minutes/status", body: { allocated: 600, used_minutes: 0, remaining_minutes: 600, unlimited: false, exhausted: false } },
    // CampaignKnowledge (api.ts:271): knowledge-panel.tsx:57 walks `tree` and campaign-duplicate.ts:71 checks `sources`.
    { path: "/campaigns/camp-001/knowledge", body: { campaign_id: "camp-001", knowledge_mode: "none", sources: [], tree: [] } },
    { path: "/campaigns/camp-001/contact-lists", body: [] }, // listContactLists: contact-lists.tsx:88 takes an array
    // LiveCallsPanel (1.5 s poll) and CallIssuesPanel (4 s poll) read only `items` (api.ts:680, :735).
    { path: "/calls/live", body: { items: [], server_time: "2026-11-11T00:00:00Z" } },
    { path: "/calls/issues", body: { items: [], server_time: "2026-11-11T00:00:00Z" } },
    // ScriptCard -> getCampaignCallsWithTranscripts (extended-api.ts:532): CampaignCallsResponse (:125).
    { path: "/campaigns/camp-001/calls", body: { items: [], page: 1, page_size: 20, total: 0 } },

    // RouteGuard (components/guards/route-guard.tsx:81) on /meetings (calendar), /reminders (calendar and email),
    // /email (email) and /assistant/meetings|reminders: every required connector must be "connected" or the guard
    // renders "Connector setup required" instead of the page, and the dev API route answers all five providers
    // "disconnected" (src/app/api/v1/[...path]/route.ts:1837). /connectors reads the same list.
    // ConnectorProviderStatusSchema: models.ts:77.
    { path: "/connectors/status", body: { items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] } },
    // ListResponseSchema(EmailTemplateResponseSchema): models.ts:514 (the Send email modal also reads /campaigns).
    { path: "/email/templates", body: { items: [] } },
    // /meetings and /reminders pages: calendarEvents.list is PaginatedResponseSchema, items required (models.ts:493);
    // reminders.list is ListResponseSchema(ReminderSchema) and is polled every 5 s, so the answer is stateless.
    { path: "/calendar/events", body: { items: [] } },
    { path: "/reminders", body: { items: [] } },
    { path: "/meetings", body: { items: [] } }, // meetings.list, read by /assistant/meetings only (backend-api.ts:414)
    // /assistant/actions: AssistantActionsResponseSchema (models.ts:488-491), parsed at backend-api.ts:667.
    { path: "/assistant/actions", body: { actions: [], total: 0, page: 1, page_size: 50 } },
    // /settings -> ProviderKeysSection (components/settings/provider-keys-section.tsx:11, :54): the page asks for
    // "/tenant-ai-credentials/" (trailing slash, stripped by apiPath) and parses z.array(credentialSchema).
    { path: "/tenant-ai-credentials", body: [] },
];

/** Data for the audited pages beyond the dashboard (see PAGE_MOCKS). */
export async function mockPageApis(page: Page): Promise<void> {
    await mockApi(page, PAGE_MOCKS);
}

/**
 * What /dashboard loads (src/app/dashboard/page.tsx:979 loadData and the
 * widgets it mounts). `nowMs` is the instant the page's own clock will show:
 * pass the frozen value when the spec freezes Date.now.
 */
export async function mockDashboardApi(page: Page, options: { nowMs?: number } = {}): Promise<void> {
    const nowMs = options.nowMs ?? Date.now();
    await mockApi(page, [
        // DashboardSummary (src/lib/dashboard-api.ts:8). `timezone` is mandatory: without it loadData throws
        // "Reporting timezone unavailable" (page.tsx:984). active_calls and avg_call_duration_seconds feed the
        // Active Calls and Avg Duration KPIs directly (page.tsx:808, :817), so two counters are non-zero
        // whatever the analytics rows say. total/answered/failed add up to the outcome_breakdown below.
        {
            path: "/dashboard/summary",
            body: {
                timezone: "UTC",
                total_calls: 154,
                answered_calls: 96,
                failed_calls: 14,
                minutes_used: 412,
                minutes_included: 1000,
                minutes_remaining: 588,
                minutes_state: "known",
                active_campaigns: 2,
                active_calls: 7,
                avg_call_duration_seconds: 84,
                queued_jobs: 5,
                outcome_breakdown: { goal_achieved: 24, answered: 72, voicemail: 18, no_answer: 20, busy: 6, failed: 14 },
            },
        },
        // dashboardApi.listCampaigns (dashboard-api.ts:503): { campaigns }. Empty list = "No campaigns yet" (page.tsx:1999-2002).
        { path: "/campaigns", body: { campaigns: [] } },
        { path: "/analytics/calls/by-campaign", body: { timezone: "UTC", campaigns: [], direction: "outbound", include_tests: false } },
        { path: "/analytics/calls", body: (url) => callAnalytics(url, nowMs) },
        // DialerInsights (src/components/dashboard/dialer-insights.tsx:39): both answers are read after Promise.all.
        { path: "/analytics/best-time", body: { timezone: "UTC", best_hour: null, hours: [] } },
        { path: "/analytics/retry-effectiveness", body: { timezone: "UTC", attempts: [] } },
        // CallIssuesBanner (call-issues-banner.tsx:41) reads items; backendApi.events.list (backend-api.ts:209)
        // also reads tenant_id, user_id and next_cursor, so the body satisfies both.
        { path: "/events", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id, user_id: E2E_USER.id } },
    ]);
}
