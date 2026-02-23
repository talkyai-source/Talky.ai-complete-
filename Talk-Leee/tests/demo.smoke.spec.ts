import { expect, test, type Page } from "@playwright/test";

test.setTimeout(120_000);

const isProdAudit = process.env.PW_PROD_AUDIT === "1";

function trackConsoleIssues(page: Page) {
    const issues: string[] = [];
    page.on("pageerror", (err) => {
        issues.push(`pageerror: ${err.message}`);
    });
    page.on("console", (msg) => {
        const t = msg.type();
        if (t === "error" || t === "warning") issues.push(`${t}: ${msg.text()}`);
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
    "/settings/connectors",
    "/notifications",
] as const;

test("sidebar fits without scrolling at common resolutions", async ({ page }) => {
    const issues = isProdAudit ? trackConsoleIssues(page) : [];
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

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

        if (vp.width >= 1024) {
            const sidebar = page.locator("aside.talklee-sidebar");
            await expect(sidebar).toBeVisible();

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

test("console clean across key routes", async ({ page }) => {
    const issues = trackConsoleIssues(page);
    await page.addInitScript(() => {
        localStorage.setItem("talklee.auth.token", "e2e-token");
    });
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

    for (const route of auditRoutes) {
        await page.goto(route, { waitUntil: "domcontentloaded", timeout: 60_000 });
        await page.evaluate(() => document.fonts?.ready);
        await page.waitForTimeout(250);
    }

    expect(issues, "No console warnings/errors or failed requests").toEqual([]);
});

test("demo path: dashboard, meetings, reminders, email, connectors", async ({ page }) => {
    const issues = isProdAudit ? trackConsoleIssues(page) : [];
    await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);

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

    await page.goto("/dashboard", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({ timeout: 15_000 });

    await expect(page.getByRole("link", { name: "Meetings" })).toBeVisible();
    await page.goto("/meetings", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/meetings/);
    await expect(page.getByRole("heading", { name: /^Meetings$/ })).toBeVisible({ timeout: 20_000 });

    await expect(page.getByRole("link", { name: "Reminders" })).toBeVisible();
    await page.goto("/reminders", { waitUntil: "networkidle", timeout: 60_000 });
    await expect(page).toHaveURL(/\/reminders/);
    await expect(page.getByRole("heading", { name: /^Reminders$/ })).toBeVisible({ timeout: 20_000 });

    await expect(page.getByRole("link", { name: "Email" })).toBeVisible();
    await page.goto("/email", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/email/);
    await expect(page.getByRole("heading", { name: "Email" })).toBeVisible({ timeout: 20_000 });

    await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
    await page.goto("/settings", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await expect(page).toHaveURL(/\/settings/);
    await page.goto("/settings/connectors", { waitUntil: "domcontentloaded", timeout: 90_000 });
    await expect(page).toHaveURL(/\/settings\/connectors/);
    await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible({ timeout: 20_000 });

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
