import { test, expect, type Page } from "@playwright/test";
import { promises as fs } from "node:fs";
import path from "node:path";

const baseUrl = "http://127.0.0.1:3100";

async function setSession(page: Page, token: string) {
    await page.addInitScript((t) => {
        localStorage.setItem("talklee.auth.token", t);
    }, token);
    try {
        await page.evaluate((t) => {
            localStorage.setItem("talklee.auth.token", t);
        }, token);
    } catch {}
    await page.context().addCookies([{ name: "talklee_auth_token", value: token, domain: "127.0.0.1", path: "/" }]);
}

async function concurrentRequests(input: { page: Page; path: string; count: number; token: string; method?: "GET" | "POST"; body?: unknown }) {
    const method = input.method ?? "GET";
    const tasks = Array.from({ length: input.count }).map(async () => {
        if (method === "POST") {
            const res = await input.page.request.post(`${baseUrl}${input.path}`, {
                headers: { authorization: `Bearer ${input.token}`, "content-type": "application/json", accept: "application/json" },
                data: input.body ?? {},
            });
            return res.status();
        }
        const res = await input.page.request.get(`${baseUrl}${input.path}`, {
            headers: { authorization: `Bearer ${input.token}`, accept: "application/json" },
        });
        return res.status();
    });
    return Promise.all(tasks);
}

async function countTenantRows(page: Page) {
    return page.locator('[role="rowgroup"] [role="row"]').count();
}

test.describe("Multi-tenant concurrency control end-to-end validation", () => {
    test("Validates partner/sub-tenant flows and concurrency-limit blocking behavior", async ({ page }) => {
        test.setTimeout(240_000);
        const artifactsDir = path.join(process.cwd(), "test-artifacts", "multi-tenant-concurrency", test.info().project.name);
        await fs.mkdir(artifactsDir, { recursive: true });
        const snap = async (name: string) => {
            const out = path.join(artifactsDir, `${name}.png`);
            await page.screenshot({ path: out, fullPage: true });
            return out;
        };
        const pageErrors: string[] = [];
        page.on("pageerror", (e) => pageErrors.push(String(e)));
        page.on("console", (msg) => {
            if (msg.type() === "error") pageErrors.push(`console.error: ${msg.text()}`);
        });
        page.on("requestfailed", (req) => {
            const failure = req.failure();
            pageErrors.push(`requestfailed: ${req.url()} ${failure?.errorText ?? ""}`.trim());
        });
        const partnerId = `e2e-${Date.now()}`;
        const partnerToken = `partner-${partnerId}-token`;
        let agentSettingsUrl = "";

        await test.step("Admin login and partner creation", async () => {
            await setSession(page, "wl-admin-token");
            await page.goto("/white-label/dashboard", { waitUntil: "domcontentloaded" });
            await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => {});
            await snap("01-white-label-dashboard-attempt");
            const partnerManagement = page.getByText("Partner Management");
            const ok = await partnerManagement
                .waitFor({ state: "visible", timeout: 60_000 })
                .then(() => true)
                .catch(() => false);
            if (!ok) {
                await fs.writeFile(path.join(artifactsDir, "page-errors.json"), JSON.stringify(pageErrors, null, 2));
            }
            expect(ok).toBeTruthy();

            await page.getByLabel("Partner ID").fill(partnerId);
            await page.getByLabel("Display Name").fill(`E2E ${partnerId}`);
            await page.getByLabel("Partner Admin Email").fill(`admin+${partnerId}@example.com`);
            await page.getByRole("button", { name: "Create Partner" }).click();
            await expect(page.getByRole("status")).toContainText("Partner created");
            await expect(page.getByRole("status")).toContainText(partnerToken);
            await snap("02-partner-created");
        });

        let tenantIdFromUrl = "";
        await test.step("Partner login and sub-tenant creation", async () => {
            await setSession(page, partnerToken);
            await page.goto(`/white-label/${encodeURIComponent(partnerId)}/tenants`, { waitUntil: "domcontentloaded" });
            const tenantsHeader = page.getByText("Sub-Tenant Management");
            const tenantsVisible = await tenantsHeader
                .waitFor({ state: "visible", timeout: 15_000 })
                .then(() => true)
                .catch(() => false);
            await snap("03-tenants-page");
            expect(tenantsVisible).toBeTruthy();

            const before = await countTenantRows(page);
            await page.getByRole("button", { name: "Create Sub-Tenant" }).click();
            await expect(page.getByRole("dialog", { name: "Create Sub-Tenant" })).toBeVisible();

            const remainingConcRaw = await page
                .locator('div:has(#subConcurrency) >> text=Remaining:')
                .locator("span")
                .last()
                .textContent();
            const remainingConc = Number(String(remainingConcRaw ?? "").replaceAll(",", "").trim());
            expect(Number.isFinite(remainingConc) && remainingConc >= 1).toBeTruthy();

            const tenantName = `E2E Tenant ${Date.now()}`;
            await page.getByLabel("Tenant Name").fill(tenantName);
            await page.getByLabel("Allocated Minutes").fill("1000");
            await page.getByLabel("Sub-Concurrency").fill(String(Math.min(2, remainingConc)));
            await page.getByRole("button", { name: "Create Tenant" }).click();

            await expect(page.getByText(tenantName)).toBeVisible();
            await snap("04-sub-tenant-created");
            const after = await countTenantRows(page);
            expect.soft(after > before, "Tenant list did not grow after creating a sub-tenant").toBeTruthy();

            const row = page.getByRole("row", { name: new RegExp(tenantName) }).first();
            const agentSettingsLink = row.getByRole("link", { name: "Agent Settings" });
            const href = await agentSettingsLink.getAttribute("href");
            tenantIdFromUrl = href ? decodeURIComponent(href.split("/tenants/")[1]?.split("/")[0] ?? "") : "";
            expect(tenantIdFromUrl.length > 0).toBeTruthy();
            agentSettingsUrl = href ?? "";
            expect(agentSettingsUrl.length > 0).toBeTruthy();

            await page.goto(agentSettingsUrl, { waitUntil: "domcontentloaded" });
            await expect(page.getByLabel("Parallel calls")).toBeVisible({ timeout: 20_000 });
            await snap("05-agent-settings-loaded");
        });

        await test.step("Concurrent API calls ramp-up until limit is reached", async () => {
            const token = partnerToken;
            const volumes = [1, 5, 10, 20];
            const results: Array<{ count: number; statuses: number[] }> = [];
            for (const n of volumes) {
                const statuses = await concurrentRequests({
                    page,
                    path: "/api/v1/assistant/execute",
                    count: n,
                    token,
                    method: "POST",
                    body: { action_type: "execute", source: "e2e", lead_id: tenantIdFromUrl || "e2e-lead", context: {} },
                });
                results.push({ count: n, statuses });
            }

            const first429 = results.find((r) => r.statuses.some((s) => s === 429));
            await fs.writeFile(path.join(artifactsDir, "04-concurrency-ramp-results.json"), JSON.stringify({ results, first429: first429?.count ?? null }, null, 2));
            expect(first429).toBeTruthy();
        });

        await test.step("UI blocks further actions at the concurrency limit", async () => {
            if (!page.url().includes("/agent-settings")) {
                await page.goto(agentSettingsUrl, { waitUntil: "domcontentloaded" });
            }
            await expect(page.getByLabel("Parallel calls")).toBeVisible({ timeout: 20_000 });

            await page.getByLabel("Parallel calls").fill("20");
            await page.getByRole("button", { name: "Run Test" }).click();

            const blockedButton = page.getByRole("button", { name: /Blocked \(\d+s\)/ });
            await expect(blockedButton).toBeVisible({ timeout: 20_000 });
            await expect(blockedButton).toBeDisabled();
            await snap("05-concurrency-ui-blocking");
        });

        await test.step("UI blocks further allocations when capacity is exhausted", async () => {
            await page.goto(`/white-label/${encodeURIComponent(partnerId)}/tenants`, { waitUntil: "domcontentloaded" });
            const tenantsHeader = page.getByText("Sub-Tenant Management");
            await tenantsHeader.waitFor({ state: "visible", timeout: 15_000 });
            await snap("06-tenants-page-before-blocking");
            const before = await countTenantRows(page);

            await page.getByRole("button", { name: "Create Sub-Tenant" }).click();
            await expect(page.getByRole("dialog", { name: "Create Sub-Tenant" })).toBeVisible();
            const remainingConcRaw = await page
                .locator('div:has(#subConcurrency) >> text=Remaining:')
                .locator("span")
                .last()
                .textContent();
            const remainingConc = Number(String(remainingConcRaw ?? "").replaceAll(",", "").trim());
            expect(Number.isFinite(remainingConc) && remainingConc >= 1).toBeTruthy();

            const exhaustName = `Exhaust ${Date.now()}`;
            await page.getByLabel("Tenant Name").fill(exhaustName);
            await page.getByLabel("Allocated Minutes").fill("0");
            await page.getByLabel("Sub-Concurrency").fill(String(remainingConc));
            await page.getByRole("button", { name: "Create Tenant" }).click();
            await expect(page.getByText(exhaustName)).toBeVisible();
            const afterExhaust = await countTenantRows(page);
            expect(afterExhaust).toBe(before + 1);

            await page.getByRole("button", { name: "Create Sub-Tenant" }).click();
            await expect(page.getByRole("dialog", { name: "Create Sub-Tenant" })).toBeVisible();

            const tenantName = `OverLimit ${Date.now()}`;
            await page.getByLabel("Tenant Name").fill(tenantName);
            await page.getByLabel("Allocated Minutes").fill("0");
            await page.getByLabel("Sub-Concurrency").fill("1");

            const createButton = page.getByRole("button", { name: "Create Tenant" });
            const alert = page.getByRole("dialog", { name: "Create Sub-Tenant" }).getByRole("alert");
            await expect(alert).toBeVisible();
            await expect(alert).toContainText("Sub-concurrency exceeds remaining capacity");
            await snap("07-ui-blocking-allocations");

            await expect(createButton).toBeDisabled();

            const after = await countTenantRows(page);
            expect(after).toBe(afterExhaust);
        });
    });
});
