import { test, expect } from "@playwright/test";

test.describe("Agent Settings feature restrictions", () => {
    test.beforeEach(async ({ page }) => {
        const token = "e2e-token";
        await page.addInitScript((t) => {
            localStorage.setItem("talklee.auth.token", t);
        }, token);
        await page.context().addCookies([{ name: "talklee_auth_token", value: token, url: "http://127.0.0.1:3100" }]);
    });

    test("Removes transfer UI and text when transfer_enabled is false", async ({ page }) => {
        await page.goto("/white-label/zen/tenants/demo/agent-settings", { waitUntil: "domcontentloaded" });
        await expect(page.getByText("System Prompt")).toBeVisible();
        await expect(page.locator("button[role='switch']")).toHaveCount(0);

        const hasTransferInText = await page.evaluate(() => document.body.innerText.toLowerCase().includes("transfer"));
        expect(hasTransferInText).toBe(false);

        const hasTransferInDom = await page.evaluate(() => {
            const lower = (v: unknown) => (typeof v === "string" ? v.toLowerCase() : "");
            const els = Array.from(document.body.querySelectorAll("*"));
            for (const el of els) {
                if (el instanceof HTMLScriptElement) continue;
                if (el instanceof HTMLStyleElement) continue;
                if (lower(el.textContent).includes("transfer")) return true;
                for (const name of el.getAttributeNames()) {
                    if (lower(el.getAttribute(name)).includes("transfer")) return true;
                }
            }
            return false;
        });
        expect(hasTransferInDom).toBe(false);
    });

    test("Shows transfer toggle when transfer_enabled is true", async ({ page }) => {
        await page.goto("/white-label/acme/tenants/demo/agent-settings", { waitUntil: "domcontentloaded" });
        await expect(page.getByText("System Prompt")).toBeVisible();
        await expect(page.getByRole("switch", { name: "Enable call transfer" })).toBeVisible();
        await expect(page.locator("button[role='switch']")).toHaveCount(1);

        const hasTransferInText = await page.evaluate(() => document.body.innerText.toLowerCase().includes("transfer"));
        expect(hasTransferInText).toBe(true);
    });
});
