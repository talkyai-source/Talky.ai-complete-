import { expect, test, type Page, type Route } from "@playwright/test";
import { E2E_AUTH_TOKEN, E2E_PERMISSIONS, E2E_USER, mockApi, mockDashboardApi } from "./support/session";

/*
 * P2 journey 1 — sign-in. A customer signs in with email and password and
 * reaches the Dashboard; a second customer whose account has two-factor
 * authentication goes through the code step first.
 *
 * Every request is answered here, in the shape the backend sends (the server
 * code is named beside each answer; BE/ = backend/app/). The assertions are on
 * what the customer sees.
 *
 * Helpers from ./support/session: mockApi (GET answers), mockDashboardApi (what
 * /dashboard reads), E2E_USER / E2E_PERMISSIONS / E2E_AUTH_TOKEN (the account).
 * signInAsE2eUser is not used: it would start the page already signed in.
 */

test.setTimeout(120_000);

const PASSWORD = "Correct-horse-battery-9";
const CHALLENGE_TOKEN = "e2e-mfa-challenge-token";

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/auth/login/" -> "/auth/login". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

/*
 * POST /auth/login, password accepted and no second factor:
 * BE/api/v1/endpoints/auth/login.py:340-351 (AuthTokenResponse, auth/schemas.py:47-60).
 */
const LOGIN_OK = {
    access_token: E2E_AUTH_TOKEN,
    token_type: "bearer",
    user_id: E2E_USER.id,
    tenant_id: E2E_USER.tenant_id,
    email: E2E_USER.email,
    role: E2E_USER.role,
    business_name: E2E_USER.business_name,
    minutes_remaining: E2E_USER.minutes_remaining,
    minutes_state: E2E_USER.minutes_state,
    message: "Login successful.",
    mfa_required: false,
};

/*
 * POST /auth/login when the account has two-factor authentication: no token
 * yet, a challenge instead. BE/api/v1/endpoints/auth/login.py:293-306.
 */
const LOGIN_MFA_REQUIRED = {
    access_token: "",
    token_type: "bearer",
    user_id: E2E_USER.id,
    tenant_id: E2E_USER.tenant_id,
    email: E2E_USER.email,
    role: E2E_USER.role,
    business_name: E2E_USER.business_name,
    minutes_remaining: null,
    minutes_state: "unavailable",
    mfa_required: true,
    mfa_challenge_token: CHALLENGE_TOKEN,
    message: "MFA verification required. Use mfa_challenge_token with POST /auth/mfa/verify.",
};

/*
 * POST /auth/mfa/verify, code accepted: MFAChallengeVerifyResponse
 * (BE/api/v1/endpoints/mfa/schemas.py:49-62, returned at mfa/verify.py:278-288).
 */
const MFA_VERIFY_OK = {
    access_token: E2E_AUTH_TOKEN,
    token_type: "bearer",
    user_id: E2E_USER.id,
    email: E2E_USER.email,
    role: E2E_USER.role,
    tenant_id: E2E_USER.tenant_id,
    business_name: E2E_USER.business_name,
    minutes_remaining: E2E_USER.minutes_remaining,
    minutes_state: E2E_USER.minutes_state,
    mfa_verified: true,
    message: "Login successful.",
};

/**
 * The backend for one sign-in. Until the sign-in is accepted it answers like a
 * server that has no session for this browser; afterwards it serves the
 * account and what the Dashboard reads.
 */
async function mockBackend(page: Page, options: { mfa: boolean }) {
    const state = { signedIn: false, logins: [] as unknown[], mfaVerifies: [] as unknown[] };

    await mockDashboardApi(page);
    // The shell every signed-in page mounts (the same answers as SHELL_MOCKS in ./support/session, which is not exported).
    await mockApi(page, [
        // GET /rbac/users/me/permissions: UserPermissionResponse (BE/api/v1/endpoints/rbac/schemas.py:32-37, route rbac/users.py:33).
        {
            path: "/rbac/users/me/permissions",
            body: { user_id: E2E_USER.id, tenant_id: E2E_USER.tenant_id, permissions: [...E2E_PERMISSIONS], role: E2E_USER.role, grant_type: "role" },
        },
        { path: "/health", body: { status: "ok" } },
        { path: "/ai-options/providers", body: { llm: { providers: [], models: [] }, stt: { providers: [], models: [] }, tts: { providers: [], models: [] } } },
        { path: "/ai-options/voices", body: { voices: [] } },
        {
            path: "/ai-options/config",
            body: {
                llm_provider: "groq", llm_model: "e2e-llm", llm_temperature: 0.5, llm_max_tokens: 150,
                stt_provider: "deepgram", stt_model: "e2e-stt", stt_language: "en",
                tts_provider: "cartesia", tts_model: "e2e-tts", tts_voice_id: "e2e-voice", tts_sample_rate: 24000,
            },
        },
        {
            path: "/support/config",
            body: { enabled: false, bot_enabled: false, permissions: { report: false, read_team: false }, categories: [], suggestions: [], attachments: { max_mb: 0, max_per_message: 0, types: [] } },
        },
        { path: "/alerts", body: { items: [], next_cursor: null, tenant_id: E2E_USER.tenant_id, user_id: E2E_USER.id } },
        { path: "/calls", body: { items: [], total: 0 } },
    ]);

    // Registered last, so it runs first: the session and the sign-in itself.
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.resourceType() === "document") return route.fallback();
            const path = apiPath(route);
            const method = request.method();

            if (method === "POST" && path === "/auth/login") {
                state.logins.push(request.postDataJSON());
                if (options.mfa) return json(route, 200, LOGIN_MFA_REQUIRED);
                state.signedIn = true;
                return json(route, 200, LOGIN_OK);
            }
            if (method === "POST" && path === "/auth/mfa/verify") {
                state.mfaVerifies.push(request.postDataJSON());
                state.signedIn = true;
                return json(route, 200, MFA_VERIFY_OK);
            }
            if (!state.signedIn) {
                // No session cookie and no token: BE/api/v1/dependencies.py:276.
                if (path === "/auth/refresh") return json(route, 401, { detail: "No refresh token." }); // BE/api/v1/endpoints/auth/refresh.py:86-90
                return json(route, 401, { detail: "Authorization required" });
            }
            if (method === "GET" && path === "/auth/me") {
                // MeResponse (BE/api/v1/endpoints/auth/schemas.py:82-125, built in auth/profile.py:70).
                return json(route, 200, { ...E2E_USER, mfa_enabled: options.mfa, mfa_verified: options.mfa ? true : null });
            }
            return route.fallback();
        },
    );

    // The password step asks Have I Been Pwned for the password's range (login-client.tsx:82). "Not found" is an empty list.
    await page.route("https://api.pwnedpasswords.com/**", (route) => route.fulfill({ status: 200, contentType: "text/plain", body: "" }));

    return state;
}

/** The email step, then the password step, up to pressing "Sign in". */
async function submitEmailAndPassword(page: Page) {
    await page.goto("/auth/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible(); // login-client.tsx:412
    await page.getByLabel("Email").fill(E2E_USER.email); // login-client.tsx:444
    await page.getByRole("button", { name: "Continue with password" }).click(); // login-client.tsx:516
    await page.getByLabel("Password", { exact: true }).fill(PASSWORD); // login-client.tsx:578
    await page.getByRole("button", { name: "Sign in", exact: true }).click(); // login-client.tsx:697
}

/** What a signed-in customer sees on the Dashboard. */
async function expectDashboard(page: Page) {
    await page.waitForURL(/\/dashboard\/?$/);
    await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible(); // dashboard/page.tsx:1576
    await expect(page.getByRole("heading", { name: "Call Volume" })).toBeVisible(); // dashboard/page.tsx:1626
    await expect(page.getByRole("img", { name: "Live calls time-series chart" })).toBeVisible(); // dashboard-charts.tsx (LiveCallsTimeSeriesChart)
}

test("a customer signs in with email and password and reaches the Dashboard", async ({ page }) => {
    const backend = await mockBackend(page, { mfa: false });

    await submitEmailAndPassword(page);

    await expectDashboard(page);
    // LoginRequest (BE/api/v1/endpoints/auth/schemas.py:41-44): the email and the password, nothing else.
    expect(backend.logins).toEqual([{ email: E2E_USER.email, password: PASSWORD }]);
    expect(backend.mfaVerifies).toEqual([]);
});

test("a customer with two-factor authentication enters the code, then reaches the Dashboard", async ({ page }) => {
    const backend = await mockBackend(page, { mfa: true });

    await submitEmailAndPassword(page);

    // The code step, not the Dashboard.
    await expect(page.getByRole("heading", { name: "Two-factor authentication" })).toBeVisible(); // mfa-verification.tsx:194
    await expect(page.getByText("Enter the code from your authenticator app")).toBeVisible(); // mfa-verification.tsx:196
    expect(new URL(page.url()).pathname.replace(/\/$/, "")).toBe("/auth/login");

    await page.getByLabel("Authentication Code").fill("123456"); // mfa-verification.tsx:203
    await page.getByRole("button", { name: "Verify", exact: true }).click(); // mfa-verification.tsx:268

    await expectDashboard(page);
    expect(backend.logins).toEqual([{ email: E2E_USER.email, password: PASSWORD }]);
    // MFA challenge body (api.ts:641-645): the challenge from the sign-in answer and the typed code.
    expect(backend.mfaVerifies).toEqual([{ challenge_token: CHALLENGE_TOKEN, code: "123456" }]);
});
