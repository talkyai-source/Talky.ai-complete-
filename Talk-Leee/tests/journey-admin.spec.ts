import { expect, test, type Page, type Route } from "@playwright/test";

/*
 * P2 journey 5 — Admin (plan 2.1). A platform admin signs in to the Admin app,
 * completes two-factor authentication, finds a customer, suspends them (with
 * the confirmation) and reactivates them (with the confirmation), and sees the
 * result shown after each step.
 *
 * This spec drives the Admin app (Admin/frontend), not the customer app, so it
 * does not use ./support/session (those helpers sign the customer app in). The
 * Admin app must be served from its production build (`vite preview`); its
 * address is ADMIN_BASE_URL, by default vite preview's own, and the build must
 * call the API on its own origin (VITE_API_BASE_URL=/api/v1).
 *
 * Every request is answered here, in the shape the backend sends (the server
 * code is named beside each answer; BE/ = backend/app/, AD/ = Admin/frontend/src/).
 * A request this file does not answer fails the test.
 */

test.setTimeout(120_000);

const ADMIN_BASE_URL = (process.env.ADMIN_BASE_URL ?? "http://127.0.0.1:4173").replace(/\/+$/, "");

const ADMIN = { id: "0d4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7", email: "admin@example.com", name: "Platform Admin", role: "platform_admin" };
const PASSWORD = "Correct-horse-battery-9";
const ACCESS_TOKEN = "e2e-admin-token";
const CHALLENGE_TOKEN = "e2e-admin-mfa-challenge";

const CUSTOMER_ID = "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d";
const CUSTOMER_NAME = "Northwind Traders";
const SUSPEND_AUDIT_ID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const RESUME_AUDIT_ID = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e";

/* GET /admin/tenants rows: TenantListItem (BE/api/v1/endpoints/admin/tenants.py:78-91). */
type Tenant = {
    id: string; business_name: string; plan_id: string | null; plan_name: string | null; minutes_used: number; minutes_allocated: number;
    status: string; user_count: number; campaign_count: number; max_concurrent_calls: number; created_at: string | null; updated_at: string | null;
};
function tenants(): Tenant[] {
    const row = (id: string, business_name: string, minutes_used: number): Tenant => ({
        id, business_name, plan_id: "starter", plan_name: "Starter", minutes_used, minutes_allocated: 500, status: "active",
        user_count: 3, campaign_count: 2, max_concurrent_calls: 2, created_at: "2026-10-01T09:00:00+00:00", updated_at: "2026-11-17T08:00:00+00:00",
    });
    return [
        row("1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9", "Contoso Dental", 120),
        row(CUSTOMER_ID, CUSTOMER_NAME, 240),
        row("3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f", "Fabrikam Logistics", 60),
    ];
}

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/admin/tenants/" -> "/admin/tenants". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

async function mockBackend(page: Page) {
    const state = {
        signedIn: false,
        tenants: tenants(),
        logins: [] as unknown[],
        mfaVerifies: [] as unknown[],
        searches: [] as string[],
        suspends: [] as unknown[],
        resumes: [] as unknown[],
        unanswered: [] as string[],
    };

    /*
     * POST /admin/tenants/{id}/suspend and /resume: the mutation receipt
     * (BE/api/v1/endpoints/admin/tenants.py:566-575, routes :655-678). A stale
     * expected_updated_at is refused with 409 tenant_changed (:262-269).
     */
    const mutate = (route: Route, target: "suspended" | "active", detail: string, auditId: string, updatedAt: string) => {
        const tenant = state.tenants.find((row) => row.id === CUSTOMER_ID)!;
        const body = route.request().postDataJSON() as { expected_updated_at: string | null };
        (target === "suspended" ? state.suspends : state.resumes).push(body);
        if (body.expected_updated_at !== tenant.updated_at) {
            return json(route, 409, {
                detail: { code: "tenant_changed", message: "Tenant changed. Refresh and review before applying this change.", status: tenant.status, updated_at: tenant.updated_at },
            });
        }
        tenant.status = target;
        tenant.updated_at = updatedAt;
        return json(route, 200, {
            id: tenant.id, detail, changed: true, status: target, updated_at: updatedAt,
            minutes_allocated: tenant.minutes_allocated, max_concurrent_calls: tenant.max_concurrent_calls, audit_event_id: auditId,
        });
    };

    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            const path = apiPath(route);
            const method = request.method();

            // POST /auth/login for an account with two-factor authentication: no token, a challenge
            // (BE/api/v1/endpoints/auth/login.py:293-306).
            if (method === "POST" && path === "/auth/login") {
                state.logins.push(request.postDataJSON());
                return json(route, 200, {
                    access_token: "", token_type: "bearer", user_id: ADMIN.id, tenant_id: null, email: ADMIN.email, role: ADMIN.role,
                    business_name: null, minutes_remaining: null, minutes_state: "unavailable", mfa_required: true,
                    mfa_challenge_token: CHALLENGE_TOKEN,
                    message: "MFA verification required. Use mfa_challenge_token with POST /auth/mfa/verify.",
                });
            }
            // POST /auth/mfa/verify: MFAChallengeVerifyResponse (BE/api/v1/endpoints/mfa/schemas.py:49-62, mfa/verify.py:278-288).
            if (method === "POST" && path === "/auth/mfa/verify") {
                state.mfaVerifies.push(request.postDataJSON());
                state.signedIn = true;
                return json(route, 200, {
                    access_token: ACCESS_TOKEN, token_type: "bearer", user_id: ADMIN.id, email: ADMIN.email, role: ADMIN.role, tenant_id: null,
                    business_name: null, minutes_remaining: null, minutes_state: "unavailable", mfa_verified: true,
                    message: "Login successful.",
                });
            }
            if (!state.signedIn) {
                // No session cookie and no token (BE/api/v1/dependencies.py:276); no refresh cookie
                // (BE/api/v1/endpoints/auth/refresh.py:86-90).
                if (path === "/auth/refresh") return json(route, 401, { detail: "No refresh token." });
                return json(route, 401, { detail: "Authorization required" });
            }
            // GET /auth/me: MeResponse (BE/api/v1/endpoints/auth/schemas.py:82-125, route auth/profile.py:70).
            if (method === "GET" && path === "/auth/me") {
                return json(route, 200, {
                    id: ADMIN.id, email: ADMIN.email, name: ADMIN.name, business_name: null, account_timezone: null, reporting_timezone: null,
                    role: ADMIN.role, legal_acceptance_required: false, terms_version: null, privacy_version: null,
                    minutes_remaining: null, minutes_state: "unavailable", partner_id: null, tenant_id: null, partner_status: null,
                    tenant_status: null, suspended_scope: null, suspension_reason: null, suspended_at: null, subscription_status: null,
                    account_state: "active", account_state_reason: null, mfa_enabled: true, mfa_verified: true,
                });
            }
            // GET /admin/tenants: TenantListItem[] filtered by business name (BE/api/v1/endpoints/admin/tenants.py:446-470).
            if (method === "GET" && path === "/admin/tenants") {
                const search = new URL(request.url()).searchParams.get("search") ?? "";
                if (search) state.searches.push(search);
                const rows = state.tenants.filter((row) => row.business_name.toLowerCase().includes(search.toLowerCase()));
                return json(route, 200, rows);
            }
            if (method === "POST" && path === `/admin/tenants/${CUSTOMER_ID}/suspend`) {
                return mutate(route, "suspended", "Tenant suspended", SUSPEND_AUDIT_ID, "2026-11-17T09:00:00+00:00");
            }
            if (method === "POST" && path === `/admin/tenants/${CUSTOMER_ID}/resume`) {
                return mutate(route, "active", "Tenant resumed", RESUME_AUDIT_ID, "2026-11-17T09:05:00+00:00");
            }

            // Anything else is a request this journey did not expect: the server's answer for an unknown route.
            state.unanswered.push(`${method} ${path}`);
            return json(route, 404, { detail: "Not Found" });
        },
    );

    return state;
}

test("a platform admin signs in with two-factor authentication, finds a customer, suspends and reactivates them", async ({ page }) => {
    const backend = await mockBackend(page);

    // Opening the customers page signed out leads to the sign-in page (AD/components/AdminRouteGuard.tsx:42).
    await page.goto(`${ADMIN_BASE_URL}/tenants`);
    await expect(page.getByRole("heading", { name: "Admin Login" })).toBeVisible(); // AD/pages/LoginPage.tsx:45
    await page.getByLabel("Email").fill(ADMIN.email); // AD/pages/LoginPage.tsx:69
    await page.getByLabel("Password").fill(PASSWORD); // AD/pages/LoginPage.tsx:72
    await page.getByRole("button", { name: "Sign in" }).click(); // AD/pages/LoginPage.tsx:78

    // Two-factor authentication (AD/pages/LoginPage.tsx:45, :56, :78).
    await expect(page.getByRole("heading", { name: "Verify your sign-in" })).toBeVisible();
    await page.getByLabel("Authenticator code").fill("123456");
    await page.getByRole("button", { name: "Verify" }).click();

    // Signed in: back on the customers page, with every customer listed.
    await page.waitForURL(`${ADMIN_BASE_URL}/tenants`);
    const search = page.getByRole("textbox", { name: "Search tenants" }); // AD/components/TenantsTable.tsx:297
    await expect(search).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "Contoso Dental" })).toHaveCount(1);
    await expect(page.getByRole("row").filter({ hasText: "Fabrikam Logistics" })).toHaveCount(1);

    // Find the customer: only they remain.
    await search.fill("Northwind");
    const customerRow = page.getByRole("row").filter({ hasText: CUSTOMER_NAME });
    await expect(customerRow).toHaveCount(1);
    await expect(page.getByRole("row").filter({ hasText: "Contoso Dental" })).toHaveCount(0);
    await expect(customerRow).toContainText("Active"); // AD/components/TenantsTable.tsx:380-382

    // Suspend, with the confirmation (AD/components/TenantsTable.tsx:418, :448, :497-501).
    await customerRow.getByRole("button", { name: `Actions for ${CUSTOMER_NAME}` }).click();
    await page.getByRole("menuitem", { name: "Suspend subscription" }).click();
    const suspendDialog = page.getByRole("dialog");
    await expect(suspendDialog).toContainText("Suspend subscription?");
    await expect(suspendDialog).toContainText(`Suspend the subscription for "${CUSTOMER_NAME}"? New calls will be blocked. Formal holds are separate.`);
    await suspendDialog.getByRole("button", { name: "Suspend", exact: true }).click();

    // The result of the suspension (AD/components/TenantsTable.tsx:280-287) and the customer's new status.
    const saved = page.getByRole("status", { name: "Saved tenant change" });
    await expect(saved).toContainText(`Saved change confirmed for ${CUSTOMER_NAME}. Subscription suspended.`);
    await expect(saved).toContainText(`Audit reference: ${SUSPEND_AUDIT_ID}`);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(customerRow).toContainText("Suspended");

    // Reactivate, with the confirmation (AD/components/TenantsTable.tsx:467, :497-501).
    await customerRow.getByRole("button", { name: `Actions for ${CUSTOMER_NAME}` }).click();
    await page.getByRole("menuitem", { name: "Resume subscription" }).click();
    const resumeDialog = page.getByRole("dialog");
    await expect(resumeDialog).toContainText("Resume subscription?");
    await expect(resumeDialog).toContainText(`Set the subscription for "${CUSTOMER_NAME}" to active? Formal holds and existing plan and quota limits still apply.`);
    await resumeDialog.getByRole("button", { name: "Resume", exact: true }).click();

    await expect(saved).toContainText(`Saved change confirmed for ${CUSTOMER_NAME}. Subscription active.`);
    await expect(saved).toContainText(`Audit reference: ${RESUME_AUDIT_ID}`);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(customerRow).toContainText("Active");

    // What the Admin app sent.
    expect(backend.logins).toEqual([{ email: ADMIN.email, password: PASSWORD }]);
    expect(backend.mfaVerifies).toEqual([{ challenge_token: CHALLENGE_TOKEN, code: "123456" }]);
    expect(backend.searches).toContain("Northwind");
    // Each change names the version it was made against (TenantVersionRequest, BE/api/v1/endpoints/admin/tenants.py:111-114).
    expect(backend.suspends).toEqual([{ expected_updated_at: "2026-11-17T08:00:00+00:00" }]);
    expect(backend.resumes).toEqual([{ expected_updated_at: "2026-11-17T09:00:00+00:00" }]);
    expect(backend.unanswered).toEqual([]);
});
