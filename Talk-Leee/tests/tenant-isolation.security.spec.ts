import { test, expect } from "@playwright/test";

const baseUrl = "http://127.0.0.1:3100";

async function setSession(page: import("@playwright/test").Page, token: string) {
    await page.addInitScript((t) => {
        localStorage.setItem("talklee.auth.token", t);
    }, token);
    await page.context().addCookies([{ name: "talklee_auth_token", value: token, url: baseUrl }]);
}

async function fetchMe(page: import("@playwright/test").Page, token: string) {
    const res = await page.request.get(`${baseUrl}/api/v1/auth/me`, {
        headers: { authorization: `Bearer ${token}`, accept: "application/json" },
    });
    const body = (await res.json().catch(() => null)) as unknown;
    return { status: res.status(), body };
}

test.describe("Multi-tenant isolation and security validation", () => {
    test("Authentication sessions are tenant-scoped for Partner A (acme) and Partner B (zen)", async ({ page }) => {
        const partnerAToken = "partner-acme-token";
        await setSession(page, partnerAToken);
        const a = await fetchMe(page, partnerAToken);
        expect(a.status).toBe(200);
        test.info().attach("Partner A session", {
            body: Buffer.from(JSON.stringify(a.body, null, 2)),
            contentType: "application/json",
        });

        await page.goto("/white-label/acme/preview", { waitUntil: "domcontentloaded" });
        await expect(page.locator('[data-white-label-partner="acme"]')).toBeVisible();

        await page.goto("/white-label/zen/preview", { waitUntil: "domcontentloaded" });
        await expect(page.getByText("403 Unauthorized")).toBeVisible();

        const partnerBToken = "partner-zen-token";
        await setSession(page, partnerBToken);
        const b = await fetchMe(page, partnerBToken);
        expect(b.status).toBe(200);
        test.info().attach("Partner B session", {
            body: Buffer.from(JSON.stringify(b.body, null, 2)),
            contentType: "application/json",
        });

        await page.goto("/white-label/zen/preview", { waitUntil: "domcontentloaded" });
        await expect(page.locator('[data-white-label-partner="zen"]')).toBeVisible();

        await page.goto("/white-label/acme/preview", { waitUntil: "domcontentloaded" });
        await expect(page.getByText("403 Unauthorized")).toBeVisible();
    });

    test("Cross-tenant API access is denied and tenant resources are isolated", async ({ page }) => {
        const tenantId = "shared-tenant";

        await setSession(page, "partner-acme-token");
        const createRes = await page.request.patch(
            `${baseUrl}/api/v1/white-label/partners/acme/tenants/${encodeURIComponent(tenantId)}/agent-settings`,
            {
                headers: { "content-type": "application/json" },
                data: { systemPrompt: "Acme only", greetingMessage: "Hello from Acme", transferEnabled: true },
            }
        );
        expect(createRes.status()).toBe(200);

        const readAsAcme = await page.request.get(
            `${baseUrl}/api/v1/white-label/partners/acme/tenants/${encodeURIComponent(tenantId)}/agent-settings`
        );
        expect(readAsAcme.status()).toBe(200);
        const acmeBody = (await readAsAcme.json()) as { config?: { systemPrompt?: string; greetingMessage?: string } };
        expect(acmeBody.config?.systemPrompt).toBe("Acme only");
        expect(acmeBody.config?.greetingMessage).toBe("Hello from Acme");

        await setSession(page, "partner-zen-token");
        const crossRead = await page.request.get(
            `${baseUrl}/api/v1/white-label/partners/acme/tenants/${encodeURIComponent(tenantId)}/agent-settings`
        );
        expect(crossRead.status()).toBe(403);

        const zenRead = await page.request.get(
            `${baseUrl}/api/v1/white-label/partners/zen/tenants/${encodeURIComponent(tenantId)}/agent-settings`
        );
        expect(zenRead.status()).toBe(200);
        const zenBody = (await zenRead.json()) as { config?: { systemPrompt?: string; greetingMessage?: string } };
        expect(zenBody.config?.systemPrompt).not.toBe("Acme only");
        expect(zenBody.config?.greetingMessage).not.toBe("Hello from Acme");

        await setSession(page, "partner-acme-token");
        const crossRead2 = await page.request.get(
            `${baseUrl}/api/v1/white-label/partners/zen/tenants/${encodeURIComponent(tenantId)}/agent-settings`
        );
        expect(crossRead2.status()).toBe(403);
    });

    test("Branding assets and email templates are partner-specific", async ({ page }) => {
        await page.goto("/white-label/acme/preview", { waitUntil: "domcontentloaded" });
        await expect(page.locator('[data-white-label-partner="acme"]')).toBeVisible();
        test.info().attach("Branding - Partner A", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });

        await page.goto("/white-label/zen/preview", { waitUntil: "domcontentloaded" });
        await expect(page.locator('[data-white-label-partner="zen"]')).toBeVisible();
        test.info().attach("Branding - Partner B", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });

        const acmeTemplatesRes = await page.request.get(`${baseUrl}/api/v1/email/templates?partner=acme`, {
            headers: { accept: "application/json" },
        });
        expect(acmeTemplatesRes.status()).toBe(200);
        const acmeTemplates = (await acmeTemplatesRes.json()) as { items: Array<{ html: string }> };
        expect(acmeTemplates.items[0]?.html ?? "").toContain("/white-label/acme/logo.svg");
        expect(acmeTemplates.items[0]?.html ?? "").toContain("wl=acme");

        const zenTemplatesRes = await page.request.get(`${baseUrl}/api/v1/email/templates?partner=zen`, {
            headers: { accept: "application/json" },
        });
        expect(zenTemplatesRes.status()).toBe(200);
        const zenTemplates = (await zenTemplatesRes.json()) as { items: Array<{ html: string }> };
        expect(zenTemplates.items[0]?.html ?? "").toContain("/white-label/zen/logo.svg");
        expect(zenTemplates.items[0]?.html ?? "").toContain("wl=zen");
        expect(zenTemplates.items[0]?.html ?? "").not.toContain("/white-label/acme/logo.svg");
    });
});
