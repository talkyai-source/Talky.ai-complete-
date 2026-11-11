import { expect, test, type Page } from "@playwright/test";
import { mockDashboardApi, mockPageApis, signInAsE2eUser } from "./support/session";

test.setTimeout(120_000);

const isProdAudit = process.env.PW_PROD_AUDIT === "1";

function trackConsoleIssues(page: Page) {
    const issues: string[] = [];
    page.on("pageerror", (err) => {
        issues.push(`pageerror: ${err.message}`);
    });
    page.on("console", (msg) => {
        const t = msg.type();
        // For a failed request Chrome's message ("Failed to load resource: ... 404") does not name it: add its URL.
        const where = msg.location().url;
        if (t === "error" || t === "warning") issues.push(`${t}: ${msg.text()}${where ? ` (${where})` : ""}`);
    });
    page.on("requestfailed", (req) => {
        const failure = req.failure();
        if (!failure) return;
        if (req.resourceType() !== "document") return;
        if (/ERR_ABORTED|NS_ERROR_ABORT/i.test(failure.errorText)) return;
        issues.push(`requestfailed: ${req.method()} ${req.url()} -> ${failure.errorText}`);
    });
    return issues;
}

const sidebarViewports = [
    { name: "desktop-1920x1080", width: 1920, height: 1080 },
    { name: "laptop-1366x768", width: 1366, height: 768 },
    { name: "mobile-360x800", width: 360, height: 800 },
] as const;

const auditRoutes = [
    "/",
    "/ai-voices",
    "/auth/login",
    "/auth/register",
    "/auth/callback",
    "/connectors/callback?ok=1&type=email",
    "/connectors/email/callback?ok=1",
    "/dashboard",
    "/campaigns",
    "/campaigns/new",
    "/campaigns/camp-001",
    "/calls",
    "/calls/call-001",
    "/contacts",
    "/analytics",
    "/recordings",
    "/ai-options",
    "/assistant",
    "/assistant/actions",
    "/assistant/meetings",
    "/assistant/reminders",
    "/email",
    "/meetings",
    "/reminders",
    "/settings",
    "/connectors",
    "/notifications",
] as const;

// Public, sign-in and OAuth-callback routes. They are not signed-in app pages, so "was not bounced to the
// sign-in page" is not a property they have to keep (a signed-in visitor may legitimately be sent on).
const redirectingRoutes = new Set<string>([
    "/",
    "/auth/login",
    "/auth/register",
    "/auth/callback",
    "/connectors/callback?ok=1&type=email",
    "/connectors/email/callback?ok=1",
]);

test("sidebar fits without scrolling at common resolutions", async ({ page }) => {
    const issues = isProdAudit ? trackConsoleIssues(page) : [];
    await signInAsE2eUser(page);
    await mockDashboardApi(page);

    await page.route(/\/(?:api\/v1\/)?connectors\/status\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] }),
        });
    });

    for (const vp of sidebarViewports) {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto("/dashboard", { waitUntil: "domcontentloaded", timeout: 60_000 });
        await page.evaluate(() => document.fonts?.ready);

        // The sidebar renders after hydration and the mocked /auth/me answer; the first visit
        // also compiles /dashboard in dev mode, which can take longer than expect()'s 5 s.
        if (vp.width >= 1024) {
            const sidebar = page.locator("aside.talklee-sidebar");
            await expect(sidebar).toBeVisible({ timeout: 30_000 });

            const metrics = await sidebar.evaluate((el) => {
                const rect = el.getBoundingClientRect();
                const inner = el.firstElementChild as HTMLElement | null;
                return {
                    width: rect.width,
                    scrollHeight: inner?.scrollHeight ?? 0,
                    clientHeight: inner?.clientHeight ?? 0,
                };
            });

            expect(metrics.width).toBeGreaterThan(150);
            expect(metrics.width).toBeLessThan(320);
            expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
        } else {
            await page.getByRole("button", { name: "Open sidebar" }).click();
            const dialog = page.getByRole("dialog", { name: "Sidebar" });
            await expect(dialog).toBeVisible();
            const metrics = await dialog.evaluate((el) => {
                const inner = el.firstElementChild as HTMLElement | null;
                return {
                    scrollHeight: inner?.scrollHeight ?? 0,
                    clientHeight: inner?.clientHeight ?? 0,
                };
            });
            expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
        }
    }

    if (isProdAudit) expect(issues, "No console warnings/errors or failed requests in production").toEqual([]);
});

// One test per route: visiting all of them in a single test compiled every route in dev mode inside one
// 120 s timeout, and a failure did not say which route it came from.
for (const route of auditRoutes) {
    test(`console clean: ${route}`, async ({ page }) => {
        const issues = trackConsoleIssues(page);
        await signInAsE2eUser(page);
        await mockDashboardApi(page);
        await mockPageApis(page);

        // The app boots after the document has loaded, and `networkidle` alone can fire in the gap before its
        // first request in dev mode. Wait for its sign-in check (GET /auth/me), then for what that triggers.
        const signInChecked = page
            .waitForResponse((res) => res.request().method() === "GET" && /\/api\/v1\/auth\/me\/?$/.test(new URL(res.url()).pathname), {
                timeout: 30_000,
            })
            .catch(() => undefined);
        await page.goto(route, { waitUntil: "domcontentloaded", timeout: 60_000 });
        await page.evaluate(() => document.fonts?.ready);
        await signInChecked;
        await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
        await page.waitForTimeout(500);

        // A lost session would send every app page to /auth/login, which is itself console clean.
        if (!redirectingRoutes.has(route)) {
            const requested = new URL(route, page.url()).pathname.replace(/\/+$/, "");
            expect(new URL(page.url()).pathname.replace(/\/+$/, ""), "stayed on the requested page").toBe(requested);
        }
        expect(issues, "No console warnings/errors or failed requests").toEqual([]);
    });
}

test("demo path: dashboard, meetings, reminders, email, connectors", async ({ page }) => {
    const issues = isProdAudit ? trackConsoleIssues(page) : [];
    await signInAsE2eUser(page);
    await mockDashboardApi(page);

    await page.route(/\/(?:api\/v1\/)?connectors\/status\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "calendar", status: "connected" }, { type: "email", status: "connected" }] }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?calendar\/events\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [] }),
        });
    });

    await page.route(/\/(?:api\/v1\/)?meetings\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?reminders\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    await page.route(/\/(?:api\/v1\/)?email\/templates\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
    });

    // Headings are the pages' own titles (DashboardLayout `title`). Meetings, Reminders and Email are no
    // longer sidebar entries (removed in b5351a50), so each is opened by URL; exact names because
    // getByRole's default name match is a case-insensitive substring ("Save your agent settings" is a link).
    await page.goto("/dashboard", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible({ timeout: 30_000 });

    await page.goto("/meetings", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/meetings/);
    await expect(page.getByRole("heading", { name: /^Meetings$/ })).toBeVisible({ timeout: 20_000 });

    // Not "networkidle": background polling can keep the network busy after the page has rendered
    // (CI 12 Nov 2026 timed out with the Reminders heading on screen). The heading check below is the wait.
    await page.goto("/reminders", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/reminders/);
    await expect(page.getByRole("heading", { name: /^Reminders$/ })).toBeVisible({ timeout: 20_000 });

    await page.goto("/email", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/email/);
    await expect(page.getByRole("heading", { name: "Email", exact: true })).toBeVisible({ timeout: 20_000 });

    await expect(page.getByRole("link", { name: "Settings", exact: true })).toBeVisible();
    await page.goto("/settings", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/settings/);
    // There is no /settings/connectors route; Connectors is a page of its own (sidebar entry "Connectors").
    await page.goto("/connectors", { waitUntil: "domcontentloaded", timeout: 90_000 });
    await expect(page).toHaveURL(/\/connectors/);
    await expect(page.getByRole("heading", { name: "Connectors", exact: true })).toBeVisible({ timeout: 20_000 });

    if (isProdAudit) expect(issues, "No console warnings/errors or failed requests in production").toEqual([]);
});

test("production headers and SEO baseline", async ({ request }) => {
    test.skip(!isProdAudit, "Production audit only");

    const res = await request.get("/", { headers: { "x-forwarded-proto": "https" } });
    expect(res.status(), "Home page should be reachable").toBe(200);

    const headers = res.headers();
    expect(headers["content-security-policy"], "CSP header should be present").toBeTruthy();
    expect(headers["content-security-policy"], "CSP should disable object embedding").toContain("object-src 'none'");
    expect(headers["strict-transport-security"], "HSTS should be present when behind HTTPS proxy").toContain("max-age=");

    const html = await res.text();
    expect(html, "HTML should include a title").toMatch(/<title>[^<]+<\/title>/i);
    expect(html, "HTML should include meta description").toMatch(/<meta\s+name=["']description["'][^>]*>/i);
});

test("email send works end-to-end in production", async ({ page }) => {
    test.skip(!isProdAudit, "Production audit only");

    const issues = trackConsoleIssues(page);
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

    await page.route(/\/(?:api\/v1\/)?connectors\/status\/?(\?.*)?$/, async (route) => {
        const req = route.request();
        if (req.resourceType() === "document") return route.continue();
        if (req.method() !== "GET") return route.continue();
        return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ items: [{ type: "email", status: "connected" }] }),
        });
    });

    await page.goto("/email", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "Email" })).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Send email" }).click();
    await expect(page.getByRole("dialog", { name: "Send email" })).toBeVisible();

    await page.locator("#recipients").fill("qa@example.com");
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByRole("button", { name: "Next" }).click();

    await page
        .getByRole("dialog", { name: "Send email" })
        .getByRole("button", { name: /^Send email$/ })
        .click();

    const toast = page.getByRole("status", { name: "Success notification" }).first();
    await expect(toast).toContainText("Email sent", { timeout: 30_000 });

    if (isProdAudit) expect(issues, "No console warnings/errors or failed requests in production").toEqual([]);
});
